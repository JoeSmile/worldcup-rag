"""Redis Stack semantic cache: KNN over query embeddings."""

from __future__ import annotations

import struct
import time
from functools import lru_cache
from typing import Any

from redis.commands.search.field import TagField, TextField, VectorField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.exceptions import RedisError
from redis.commands.search.query import Query

from core.cache_config import get_cache_config
from core.config import settings
from core.logger import get_logger, log_extra
from core.redis_client import get_redis_binary

logger = get_logger("semantic_cache")

SEMANTIC_PREFIX = "sem:"


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _cosine_similarity_from_distance(distance: float) -> float:
    return 1.0 - distance


class SemanticCache:
    def __init__(self) -> None:
        self.cfg = get_cache_config()
        self.index_name = settings.redis_vector_index
        self._index_ready = False

    def _client(self) -> Any | None:
        return get_redis_binary()

    def ensure_index(self) -> bool:
        if self._index_ready:
            return True
        client = self._client()
        if client is None:
            return False
        try:
            client.ft(self.index_name).info()
            self._index_ready = True
            return True
        except RedisError:
            pass

        algorithm = self.cfg.semantic.vector_algorithm.upper()
        if algorithm not in {"HNSW", "FLAT"}:
            algorithm = "HNSW"

        dim = settings.embedding_dimensions
        vector_kwargs: dict[str, Any] = {
            "TYPE": "FLOAT32",
            "DIM": dim,
            "DISTANCE_METRIC": "COSINE",
        }
        if algorithm == "HNSW":
            vector_kwargs["M"] = 16
            vector_kwargs["EF_CONSTRUCTION"] = 200

        try:
            client.ft(self.index_name).create_index(
                [
                    TextField("query"),
                    TextField("answer"),
                    TagField("is_null"),
                    VectorField("embedding", algorithm, vector_kwargs),
                ],
                definition=IndexDefinition(prefix=[SEMANTIC_PREFIX], index_type=IndexType.HASH),
            )
            self._index_ready = True
            logger.info(
                "semantic cache index created",
                extra=log_extra(index=self.index_name, algorithm=algorithm),
            )
            return True
        except RedisError as exc:
            if "Index already exists" in str(exc):
                self._index_ready = True
                return True
            logger.warning(
                "semantic cache index create failed",
                extra=log_extra(error=str(exc)),
            )
            return False

    def get(self, query: str, embedding: list[float]) -> dict[str, Any] | None:
        if not self.cfg.semantic.enabled:
            return None
        client = self._client()
        if client is None or not self.ensure_index():
            return None

        knn = max(1, self.cfg.semantic.knn)
        blob = _pack_vector(embedding)
        try:
            q = (
                Query(f"*=>[KNN {knn} @embedding $vec AS distance]")
                .sort_by("distance")
                .return_fields("query", "answer", "is_null", "distance")
                .dialect(2)
            )
            result = client.ft(self.index_name).search(q, query_params={"vec": blob})
        except RedisError as exc:
            logger.warning("semantic cache search failed", extra=log_extra(error=str(exc)))
            return None

        if not result.docs:
            return None

        best = result.docs[0]
        distance = float(best.distance if best.distance is not None else best["distance"])
        similarity = _cosine_similarity_from_distance(distance)
        if similarity < self.cfg.semantic.threshold:
            return None

        answer = best.answer if hasattr(best, "answer") else best.get("answer", "")
        if isinstance(answer, bytes):
            answer = answer.decode("utf-8")
        is_null = best.is_null if hasattr(best, "is_null") else best.get("is_null", "0")
        if isinstance(is_null, bytes):
            is_null = is_null.decode("utf-8")

        matched_query = best.query if hasattr(best, "query") else best.get("query", "")
        if isinstance(matched_query, bytes):
            matched_query = matched_query.decode("utf-8")

        return {
            "query": matched_query,
            "answer": answer,
            "is_null": is_null in {"1", "true", "True"},
            "similarity": similarity,
        }

    def store_pipeline(
        self,
        pipe: Any,
        digest: str,
        query: str,
        answer: str,
        embedding: list[float],
        ttl: int,
        is_null: bool,
    ) -> None:
        key = f"{SEMANTIC_PREFIX}{digest}"
        pipe.hset(
            key,
            mapping={
                "query": query,
                "answer": answer,
                "is_null": "1" if is_null else "0",
                "embedding": _pack_vector(embedding),
                "ts": str(int(time.time())),
            },
        )
        pipe.expire(key, ttl)


@lru_cache(maxsize=1)
def get_semantic_cache() -> SemanticCache:
    return SemanticCache()
