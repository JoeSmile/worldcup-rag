"""Shared Redis clients (text + binary for vector blobs)."""

from __future__ import annotations

from functools import lru_cache

import redis
from redis.exceptions import RedisError

from core.config import settings
from core.logger import get_logger

logger = get_logger("redis_client")


@lru_cache(maxsize=1)
def get_redis_text() -> redis.Redis | None:
    try:
        client = redis.from_url(
            settings.resolved_redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        return client
    except RedisError as exc:
        logger.warning("redis text client unavailable", extra={"log_context": {"error": str(exc)}})
        return None


@lru_cache(maxsize=1)
def get_redis_binary() -> redis.Redis | None:
    try:
        client = redis.from_url(
            settings.resolved_redis_url,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        return client
    except RedisError as exc:
        logger.warning("redis binary client unavailable", extra={"log_context": {"error": str(exc)}})
        return None
