"""Session-scoped conversation memory in Redis (shared across workflows)."""

from __future__ import annotations

import hashlib
import time
from functools import lru_cache
from typing import Any

from redis.exceptions import LockError, RedisError

from core.config import settings
from core.logger import get_logger, log_extra
from core.memory_policy import get_memory_policy
from core.redis_client import get_redis_text

logger = get_logger("memory")

MEMORY_PREFIX = "chat:session:"
ROUTER_RECENT_LIMIT = 2


def _keys(session_id: str) -> dict[str, str]:
    base = f"{MEMORY_PREFIX}{session_id}"
    return {
        "messages": f"{base}:messages",
        "metadata": f"{base}:metadata",
        "summary": f"{base}:summary",
        "core_facts": f"{base}:core_facts",
        "lock": f"{base}:lock",
    }


def _message_key(session_id: str, msg_id: str) -> str:
    return f"{MEMORY_PREFIX}{session_id}:message:{msg_id}"


def _filter_for_workflow(recent: list[dict[str, str]], workflow: str) -> list[dict[str, str]]:
    policy = get_memory_policy(workflow)
    trimmed = recent[-policy.recent_limit :]
    if workflow == "complex_flow":
        kept: list[dict[str, str]] = []
        for msg in recent:
            if msg.get("role") == "user":
                kept.append(msg)
            elif msg.get("workflow") in (None, "simple_qa", "complex_flow"):
                kept.append(msg)
        return kept[-policy.recent_limit :] or trimmed
    return trimmed


def _estimate_message_tokens(data: dict[str, str], *, content: str = "") -> int:
    stored = int(data.get("tokens") or 0)
    if stored > 0:
        return stored
    text = content or data.get("content") or ""
    return max(1, len(text) // 2)


class SessionMemory:
    """Redis-backed session memory with workflow-tagged messages."""

    def __init__(
        self,
        max_turns: int = 10,
        max_tokens: int = 4000,
        ttl_days: int = 7,
    ) -> None:
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.ttl_seconds = ttl_days * 86400
        self._redis = get_redis_text()

    @property
    def available(self) -> bool:
        return self._redis is not None

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        workflow: str,
        tokens: int = 0,
    ) -> str | None:
        if not self._redis or not session_id or not content.strip():
            return None

        msg_id = self._generate_msg_id(session_id, content)
        keys = _keys(session_id)
        msg_key = _message_key(session_id, msg_id)
        token_count = tokens or _estimate_message_tokens({}, content=content)

        try:
            lock = self._redis.lock(keys["lock"], timeout=5, blocking_timeout=3)
            with lock:
                self._redis.hset(
                    msg_key,
                    mapping={
                        "role": role,
                        "content": content,
                        "workflow": workflow,
                        "timestamp": str(int(time.time())),
                        "tokens": str(token_count),
                    },
                )
                self._redis.expire(msg_key, self.ttl_seconds)
                self._redis.rpush(keys["messages"], msg_id)
                self._redis.expire(keys["messages"], self.ttl_seconds)
                self._redis.hincrby(keys["metadata"], "total_tokens", token_count)
                self._redis.expire(keys["metadata"], self.ttl_seconds)
                self._trim_messages(session_id, max_messages=self.max_turns * 2)
                self._prune_by_tokens(session_id, max_tokens=self.max_tokens)
        except (LockError, RedisError) as exc:
            logger.warning(
                "session memory write failed",
                extra=log_extra(session_id=session_id, error=str(exc)),
            )
            return None

        return msg_id

    def add_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        *,
        workflow: str,
    ) -> bool:
        """Persist one user/assistant turn atomically under a single lock."""
        if not self._redis or not session_id:
            return False
        if not user_content.strip() or not assistant_content.strip():
            return False

        policy = get_memory_policy(workflow)
        keys = _keys(session_id)
        user_tokens = _estimate_message_tokens({}, content=user_content)
        assistant_tokens = _estimate_message_tokens({}, content=assistant_content)
        user_id: str | None = None

        try:
            lock = self._redis.lock(keys["lock"], timeout=5, blocking_timeout=3)
            with lock:
                user_id = self._write_message_locked(
                    session_id,
                    "user",
                    user_content,
                    workflow=workflow,
                    tokens=user_tokens,
                )
                if not user_id:
                    return False

                assistant_id = self._write_message_locked(
                    session_id,
                    "assistant",
                    assistant_content,
                    workflow=workflow,
                    tokens=assistant_tokens,
                )
                if not assistant_id:
                    self._rollback_message(session_id, user_id, user_tokens)
                    return False

                self._trim_messages(session_id, max_messages=policy.max_turns * 2)
                self._prune_by_tokens(session_id, max_tokens=policy.max_tokens)
        except (LockError, RedisError) as exc:
            if user_id:
                self._rollback_message(session_id, user_id, user_tokens)
            logger.warning(
                "session memory turn write failed",
                extra=log_extra(session_id=session_id, error=str(exc)),
            )
            return False

        return True

    def get_context(
        self,
        session_id: str,
        *,
        for_workflow: str | None = None,
    ) -> tuple[list[dict[str, str]], str, list[str]]:
        if not self._redis or not session_id:
            return [], "", []

        policy = get_memory_policy(for_workflow)
        recent = self._fetch_recent(session_id, limit=policy.max_turns * 2)
        if for_workflow:
            recent = _filter_for_workflow(recent, for_workflow)

        keys = _keys(session_id)
        try:
            summary = self._redis.get(keys["summary"]) or ""
            core_facts = list(self._redis.smembers(keys["core_facts"]) or [])
        except RedisError as exc:
            logger.warning(
                "session memory read failed",
                extra=log_extra(session_id=session_id, error=str(exc)),
            )
            return recent, "", []

        return recent, summary, core_facts

    def get_router_context(self, session_id: str) -> dict[str, Any]:
        if not self._redis or not session_id:
            return {"recent": [], "summary": ""}

        recent = self._fetch_recent(session_id, limit=ROUTER_RECENT_LIMIT * 2)
        keys = _keys(session_id)
        try:
            summary = self._redis.get(keys["summary"]) or ""
        except RedisError:
            summary = ""
        return {"recent": recent, "summary": summary}

    def get_stats(self, session_id: str) -> dict[str, Any]:
        if not self._redis or not session_id:
            return {"available": False}

        keys = _keys(session_id)
        try:
            msg_count = self._redis.llen(keys["messages"])
            return {
                "available": True,
                "total_messages": msg_count,
                "total_turns": msg_count // 2,
                "total_tokens": int(self._redis.hget(keys["metadata"], "total_tokens") or 0),
                "core_facts_count": self._redis.scard(keys["core_facts"]),
                "has_summary": bool(self._redis.exists(keys["summary"])),
            }
        except RedisError as exc:
            return {"available": False, "error": str(exc)}

    def should_compress_summary(
        self,
        session_id: str,
        *,
        min_turns: int,
        token_threshold: int,
    ) -> bool:
        if not self._redis or not session_id:
            return False

        stats = self.get_stats(session_id)
        if not stats.get("available"):
            return False

        turns = int(stats.get("total_turns") or 0)
        tokens = int(stats.get("total_tokens") or 0)
        return turns >= min_turns or tokens >= token_threshold

    def set_summary(self, session_id: str, summary: str) -> bool:
        if not self._redis or not session_id or not summary.strip():
            return False

        keys = _keys(session_id)
        try:
            self._redis.set(keys["summary"], summary.strip())
            self._redis.expire(keys["summary"], self.ttl_seconds)
            return True
        except RedisError as exc:
            logger.warning(
                "session summary write failed",
                extra=log_extra(session_id=session_id, error=str(exc)),
            )
            return False

    def archive_summarized_messages(self, session_id: str, *, keep_messages: int) -> int:
        """Drop oldest messages after summary, keeping the latest keep_messages entries."""
        if not self._redis or not session_id:
            return 0

        keys = _keys(session_id)
        try:
            length = self._redis.llen(keys["messages"])
            if length <= keep_messages:
                return 0

            overflow = length - keep_messages
            stale_ids = self._redis.lrange(keys["messages"], 0, overflow - 1)
            self._delete_message_ids(session_id, stale_ids)
            self._redis.ltrim(keys["messages"], overflow, -1)
            return len(stale_ids)
        except RedisError as exc:
            logger.warning(
                "session archive after summary failed",
                extra=log_extra(session_id=session_id, error=str(exc)),
            )
            return 0

    def clear(self, session_id: str) -> int:
        if not self._redis or not session_id:
            return 0

        pattern = f"{MEMORY_PREFIX}{session_id}:*"
        removed = 0
        cursor = 0
        try:
            while True:
                cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=200)
                if keys:
                    removed += self._redis.delete(*keys)
                if cursor == 0:
                    break
        except RedisError as exc:
            logger.warning(
                "session memory clear failed",
                extra=log_extra(session_id=session_id, error=str(exc)),
            )
        return removed

    def _write_message_locked(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        workflow: str,
        tokens: int,
    ) -> str | None:
        msg_id = self._generate_msg_id(session_id, content)
        keys = _keys(session_id)
        msg_key = _message_key(session_id, msg_id)
        self._redis.hset(
            msg_key,
            mapping={
                "role": role,
                "content": content,
                "workflow": workflow,
                "timestamp": str(int(time.time())),
                "tokens": str(tokens),
            },
        )
        self._redis.expire(msg_key, self.ttl_seconds)
        self._redis.rpush(keys["messages"], msg_id)
        self._redis.expire(keys["messages"], self.ttl_seconds)
        self._redis.hincrby(keys["metadata"], "total_tokens", tokens)
        self._redis.expire(keys["metadata"], self.ttl_seconds)
        return msg_id

    def _fetch_recent(self, session_id: str, *, limit: int) -> list[dict[str, str]]:
        keys = _keys(session_id)
        msg_ids = self._redis.lrange(keys["messages"], -limit, -1) if limit > 0 else []
        messages: list[dict[str, str]] = []
        for msg_id in msg_ids:
            data = self._redis.hgetall(_message_key(session_id, msg_id))
            if not data:
                continue
            messages.append(
                {
                    "role": data.get("role", ""),
                    "content": data.get("content", ""),
                    "workflow": data.get("workflow", ""),
                }
            )
        return messages

    def _trim_messages(self, session_id: str, *, max_messages: int | None = None) -> None:
        keys = _keys(session_id)
        limit = max_messages if max_messages is not None else self.max_turns * 2
        length = self._redis.llen(keys["messages"])
        if length <= limit:
            return

        overflow = length - limit
        stale_ids = self._redis.lrange(keys["messages"], 0, overflow - 1)
        self._delete_message_ids(session_id, stale_ids)
        self._redis.ltrim(keys["messages"], overflow, -1)

    def _prune_by_tokens(self, session_id: str, *, max_tokens: int | None = None) -> None:
        keys = _keys(session_id)
        token_limit = max_tokens if max_tokens is not None else self.max_tokens
        while self._session_token_count(session_id) > token_limit:
            msg_id = self._redis.lpop(keys["messages"])
            if not msg_id:
                break
            self._delete_message_ids(session_id, [msg_id])

    def _session_token_count(self, session_id: str) -> int:
        keys = _keys(session_id)
        stored = self._redis.hget(keys["metadata"], "total_tokens")
        if stored is not None:
            return max(0, int(stored))
        return 0

    def _delete_message_ids(self, session_id: str, msg_ids: list[str]) -> None:
        if not msg_ids:
            return
        keys = _keys(session_id)
        for msg_id in msg_ids:
            msg_key = _message_key(session_id, msg_id)
            data = self._redis.hgetall(msg_key) or {}
            tokens = _estimate_message_tokens(data)
            self._redis.delete(msg_key)
            if tokens:
                self._redis.hincrby(keys["metadata"], "total_tokens", -tokens)

    def _rollback_message(self, session_id: str, msg_id: str, tokens: int) -> None:
        keys = _keys(session_id)
        self._redis.lrem(keys["messages"], 1, msg_id)
        self._redis.delete(_message_key(session_id, msg_id))
        if tokens:
            self._redis.hincrby(keys["metadata"], "total_tokens", -tokens)
        stored = self._redis.hget(keys["metadata"], "total_tokens")
        if stored is not None and int(stored) < 0:
            self._redis.hset(keys["metadata"], "total_tokens", 0)

    @staticmethod
    def _generate_msg_id(session_id: str, content: str) -> str:
        raw = f"{session_id}:{content}:{time.time_ns()}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def get_session_memory() -> SessionMemory:
    return SessionMemory(
        max_turns=settings.memory_max_turns,
        max_tokens=settings.memory_max_tokens,
        ttl_days=settings.memory_ttl_days,
    )
