"""Three-tier query cache: L1 TTL → L2 exact Redis → semantic KNN."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from cachetools import TTLCache
from redis.exceptions import RedisError

from core.cache_config import get_cache_config
from core.logger import get_logger, log_extra
from core.redis_client import get_redis_binary, get_redis_text
from core.semantic_cache import get_semantic_cache
from core.semantic_cache import SEMANTIC_PREFIX
from tools import embed_query

logger = get_logger("query_cache")

EXACT_PREFIX = "exact:"


def _digest(query: str) -> str:
    return hashlib.md5(query.strip().encode("utf-8")).hexdigest()


def _exact_key(digest: str) -> str:
    return f"{EXACT_PREFIX}{digest}"


def _is_null_answer(answer: str | None, marker: str) -> bool:
    if not answer or not answer.strip():
        return True
    return answer.strip() == marker


def _should_cache_result(result: dict[str, Any], marker: str) -> bool:
    answer = result.get("answer")
    if _is_null_answer(answer, marker):
        return True
    if result.get("error"):
        return True
    return bool(answer)


class QueryCache:
    def __init__(self) -> None:
        self.cfg = get_cache_config()
        self.l1 = TTLCache(maxsize=self.cfg.l1.maxsize, ttl=self.cfg.l1.ttl)
        self.semantic = get_semantic_cache()
        self.stats: dict[str, int] = {
            "l1_hit": 0,
            "l2_hit": 0,
            "semantic_hit": 0,
            "miss": 0,
        }

    def _l1_put(self, digest: str, payload: dict[str, Any]) -> None:
        self.l1[digest] = payload

    def get(self, query: str) -> tuple[dict[str, Any] | None, str | None]:
        """Return (cached chat payload, hit_layer) where hit_layer is l1|l2|semantic."""
        if not self.cfg.enabled:
            return None, None

        normalized = query.strip()
        if not normalized:
            return None, None

        digest = _digest(normalized)
        marker = self.cfg.null.marker

        if digest in self.l1:
            self.stats["l1_hit"] += 1
            return self._from_payload(self.l1[digest], marker), "l1"

        l2_payload = self._get_l2_exact(digest)
        if l2_payload is not None:
            self.stats["l2_hit"] += 1
            self._l1_put(digest, l2_payload)
            return self._from_payload(l2_payload, marker), "l2"

        semantic_payload = self._get_semantic(normalized)
        if semantic_payload is not None:
            self.stats["semantic_hit"] += 1
            self._l1_put(digest, semantic_payload)
            return self._from_payload(semantic_payload, marker), "semantic"

        self.stats["miss"] += 1
        return None, None

    def _get_l2_exact(self, digest: str) -> dict[str, Any] | None:
        client = get_redis_text()
        if client is None:
            return None
        try:
            raw = client.get(_exact_key(digest))
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except (RedisError, json.JSONDecodeError) as exc:
            logger.warning("exact cache get failed", extra=log_extra(error=str(exc)))
            return None

    def _get_semantic(self, query: str) -> dict[str, Any] | None:
        if not self.cfg.semantic.enabled:
            return None
        try:
            embedding = embed_query(query)
        except Exception as exc:
            logger.warning("embedding for semantic cache failed", extra=log_extra(error=str(exc)))
            return None

        hit = self.semantic.get(query, embedding)
        if hit is None:
            return None

        marker = self.cfg.null.marker
        answer = hit["answer"] if not hit["is_null"] else marker
        return {
            "query": query,
            "answer": answer,
            "is_null": hit["is_null"],
            "workflow": "cache_semantic",
            "tool_name": None,
            "tools_used": [],
            "sql_generated": None,
            "usage": {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
            "matched_query": hit.get("query"),
            "similarity": hit.get("similarity"),
        }

    def set(self, query: str, result: dict[str, Any]) -> None:
        if not self.cfg.enabled:
            return

        normalized = query.strip()
        if not normalized:
            return

        marker = self.cfg.null.marker
        if not _should_cache_result(result, marker):
            return

        answer = result.get("answer") or ""
        is_null = _is_null_answer(answer, marker) or bool(result.get("error"))
        if is_null:
            answer = marker

        digest = _digest(normalized)
        ttl_l2 = self.cfg.null.ttl if is_null else self.cfg.l2.ttl
        ttl_semantic = self.cfg.null.ttl if is_null else self.cfg.semantic.ttl

        payload = {
            "query": normalized,
            "answer": answer,
            "is_null": is_null,
            "workflow": result.get("workflow"),
            "tool_name": result.get("tool_name"),
            "tools_used": result.get("tools_used") or [],
            "sql_generated": result.get("sql_generated"),
            "usage": result.get("usage"),
            "router_choice": result.get("router_choice"),
            "auto_routed": result.get("auto_routed"),
        }

        self._l1_put(digest, payload)
        self._write_redis_atomic(digest, normalized, answer, is_null, payload, ttl_l2, ttl_semantic)

    def _write_redis_atomic(
        self,
        digest: str,
        query: str,
        answer: str,
        is_null: bool,
        payload: dict[str, Any],
        ttl_l2: int,
        ttl_semantic: int,
    ) -> None:
        client = get_redis_binary()
        if client is None:
            return

        try:
            embedding = embed_query(query)
        except Exception as exc:
            logger.warning(
                "embedding for cache write skipped",
                extra=log_extra(error=str(exc)),
            )
            embedding = None

        try:
            pipe = client.pipeline(transaction=True)
            pipe.setex(_exact_key(digest), ttl_l2, json.dumps(payload, ensure_ascii=False))
            if embedding and self.cfg.semantic.enabled:
                self.semantic.store_pipeline(
                    pipe, digest, query, answer, embedding, ttl_semantic, is_null
                )
            pipe.execute()
        except RedisError as exc:
            logger.warning("cache pipeline write failed", extra=log_extra(error=str(exc)))

    def _from_payload(self, payload: dict[str, Any], marker: str) -> dict[str, Any]:
        is_null = payload.get("is_null") or _is_null_answer(payload.get("answer"), marker)
        answer = payload.get("answer") or ""
        if is_null:
            answer = marker if payload.get("answer") == marker else (
                payload.get("answer") or "抱歉，未找到相关信息。"
            )

        return {
            "answer": answer,
            "tool_name": payload.get("tool_name"),
            "tools_used": payload.get("tools_used"),
            "sql_generated": payload.get("sql_generated"),
            "usage": payload.get("usage") or {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
            "workflow": payload.get("workflow"),
            "router_choice": payload.get("router_choice"),
            "auto_routed": payload.get("auto_routed"),
            "error": None if not is_null else marker,
            "cache_hit": True,
            "is_null_result": is_null,
            "matched_query": payload.get("matched_query"),
            "similarity": payload.get("similarity"),
        }

    def get_stats(self) -> dict[str, Any]:
        hits = self.stats["l1_hit"] + self.stats["l2_hit"] + self.stats["semantic_hit"]
        total = hits + self.stats["miss"]
        hit_rate = f"{hits / total * 100:.1f}%" if total > 0 else "0%"
        return {
            **self.stats,
            "total": total,
            "hit_rate": hit_rate,
            "config": {
                "l1_maxsize": self.cfg.l1.maxsize,
                "l1_ttl": self.cfg.l1.ttl,
                "l2_ttl": self.cfg.l2.ttl,
                "semantic_threshold": self.cfg.semantic.threshold,
                "semantic_enabled": self.cfg.semantic.enabled,
                "null_ttl": self.cfg.null.ttl,
            },
        }

    def clear(self) -> dict[str, int]:
        """Clear L1 and Redis exact/semantic keys (dev / benchmark warmup)."""
        self.l1.clear()
        removed = {"exact": 0, "semantic": 0}

        client = get_redis_binary()
        if client is None:
            return removed

        for prefix, label in ((EXACT_PREFIX, "exact"), (f"{SEMANTIC_PREFIX}", "semantic")):
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=f"{prefix}*", count=200)
                if keys:
                    removed[label] += client.delete(*keys)
                if cursor == 0:
                    break

        self.stats = {"l1_hit": 0, "l2_hit": 0, "semantic_hit": 0, "miss": 0}
        return removed


@lru_cache(maxsize=1)
def get_query_cache() -> QueryCache:
    return QueryCache()
