"""Redis Stream queue for post-chat async work (summary, cache write)."""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from typing import Any

from redis.exceptions import RedisError, ResponseError

from core.cache_config import get_cache_config
from core.logger import get_logger, log_extra
from core.queue_config import get_queue_config
from core.redis_client import get_redis_text

logger = get_logger("post_chat_queue")


class PostChatEventType(StrEnum):
    CACHE_WRITE = "cache_write"
    SUMMARY_COMPRESS = "summary_compress"


def _queue_cfg() -> Any:
    return get_queue_config().queue


@lru_cache(maxsize=1)
def _ensure_consumer_group() -> bool:
    client = get_redis_text()
    if client is None:
        return False
    cfg = _queue_cfg()
    try:
        client.xgroup_create(cfg.stream_key, cfg.consumer_group, id="0", mkstream=True)
        logger.info(
            "post-chat consumer group created",
            extra=log_extra(stream=cfg.stream_key, group=cfg.consumer_group),
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            logger.warning("post-chat xgroup_create failed", extra=log_extra(error=str(exc)))
            return False
    return True


def enqueue(event_type: PostChatEventType | str, payload: dict[str, Any], trace_id: str | None = None) -> str | None:
    cfg = get_queue_config()
    if not cfg.queue.enabled:
        return None

    client = get_redis_text()
    if client is None:
        logger.warning("post-chat enqueue skipped: redis unavailable")
        return None

    try:
        message_id = client.xadd(
            cfg.queue.stream_key,
            {
                "type": str(event_type),
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
                "trace_id": trace_id or "",
            },
        )
        return message_id
    except RedisError as exc:
        logger.warning("post-chat enqueue failed", extra=log_extra(error=str(exc), event=event_type))
        return None


def enqueue_cache_write(query: str, result: dict[str, Any], trace_id: str | None) -> str | None:
    return enqueue(
        PostChatEventType.CACHE_WRITE,
        {"query": query, "result": result},
        trace_id=trace_id,
    )


def enqueue_summary_compress(session_id: str, workflow: str, trace_id: str | None) -> str | None:
    return enqueue(
        PostChatEventType.SUMMARY_COMPRESS,
        {"session_id": session_id, "workflow": workflow},
        trace_id=trace_id,
    )


def enqueue_post_chat_tasks(
    query: str,
    result: dict[str, Any],
    *,
    trace_id: str | None,
    session_id: str | None,
    use_query_cache: bool,
) -> dict[str, bool]:
    """Enqueue deferred work after a successful chat response."""
    cfg = get_queue_config()
    scheduled = {"cache_write": False, "summary_compress": False}

    if not cfg.queue.enabled:
        return scheduled

    if use_query_cache and cfg.queue.defer_cache_write:
        scheduled["cache_write"] = enqueue_cache_write(query, result, trace_id) is not None

    if (
        cfg.summary.enabled
        and session_id
        and result.get("memory_persisted") is True
    ):
        workflow = result.get("workflow") or "simple_qa"
        scheduled["summary_compress"] = enqueue_summary_compress(session_id, workflow, trace_id) is not None

    return scheduled


def read_group(
    consumer_name: str,
    *,
    count: int | None = None,
    block_ms: int | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Read new messages from the consumer group. Returns [(msg_id, fields)]."""
    client = get_redis_text()
    if client is None or not _ensure_consumer_group():
        return []

    cfg = _queue_cfg()
    try:
        rows = client.xreadgroup(
            groupname=cfg.consumer_group,
            consumername=consumer_name,
            streams={cfg.stream_key: ">"},
            count=count or cfg.batch_size,
            block=block_ms if block_ms is not None else cfg.block_ms,
        )
    except RedisError as exc:
        logger.warning("post-chat xreadgroup failed", extra=log_extra(error=str(exc)))
        return []

    messages: list[tuple[str, dict[str, str]]] = []
    for _stream, entries in rows or []:
        for msg_id, fields in entries:
            messages.append((msg_id, fields))
    return messages


def ack(message_id: str) -> None:
    client = get_redis_text()
    if client is None:
        return
    cfg = _queue_cfg()
    try:
        client.xack(cfg.stream_key, cfg.consumer_group, message_id)
    except RedisError as exc:
        logger.warning("post-chat xack failed", extra=log_extra(error=str(exc), message_id=message_id))
