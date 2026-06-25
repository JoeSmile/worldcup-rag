"""Consume post-chat Redis Stream events (summary compression, cache write)."""

from __future__ import annotations

import json
import socket
import time
from typing import Any

from core.logger import get_logger, log_extra
from core.post_chat_queue import ack, PostChatEventType, read_group
from core.query_cache import get_query_cache
from core.summary_service import maybe_compress_session

logger = get_logger("post_chat_worker")


def _consumer_name() -> str:
    return f"worker-{socket.gethostname()}-{int(time.time())}"


def handle_message(fields: dict[str, str]) -> None:
    event_type = fields.get("type") or ""
    trace_id = fields.get("trace_id") or None
    raw_payload = fields.get("payload") or "{}"
    try:
        payload: dict[str, Any] = json.loads(raw_payload)
    except json.JSONDecodeError:
        logger.warning("invalid post-chat payload", extra=log_extra(event=event_type))
        return

    if event_type == PostChatEventType.CACHE_WRITE:
        query = payload.get("query") or ""
        result = payload.get("result") or {}
        if query and isinstance(result, dict):
            get_query_cache().set(query, result)
            logger.info("cache write completed", extra=log_extra(trace_id=trace_id))
        return

    if event_type == PostChatEventType.SUMMARY_COMPRESS:
        session_id = payload.get("session_id") or ""
        workflow = payload.get("workflow") or "simple_qa"
        if session_id:
            maybe_compress_session(session_id, workflow, trace_id=trace_id)
        return

    logger.warning("unknown post-chat event", extra=log_extra(event=event_type))


def run_forever(consumer_name: str | None = None) -> None:
    name = consumer_name or _consumer_name()
    logger.info("post-chat worker started", extra=log_extra(consumer=name))

    while True:
        batch = read_group(name)
        if not batch:
            continue
        for message_id, fields in batch:
            try:
                handle_message(fields)
            except Exception as exc:
                logger.exception(
                    "post-chat handler failed",
                    extra=log_extra(message_id=message_id, error=str(exc)),
                )
            finally:
                ack(message_id)
