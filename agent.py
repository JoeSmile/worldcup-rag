"""Backward-compatible chat entry with three-tier query cache."""

from typing import Dict, List, Optional

from core.cache_config import get_cache_config
from core.logger import get_logger, log_extra
from core.post_chat_queue import enqueue_post_chat_tasks
from core.queue_config import get_queue_config
from core.query_cache import get_query_cache
from core.security import SecurityFilter
from workflows.registry import chat as workflow_chat

logger = get_logger("agent")


def chat(
    query: str,
    history: List[Dict] = None,
    workflow: Optional[str] = None,
    trace_id: Optional[str] = None,
    skip_cache: bool = False,
    session_id: Optional[str] = None,
) -> dict:
    cache_cfg = get_cache_config()
    # Session-scoped turns may rely on server-side memory; skip global query cache.
    use_cache = cache_cfg.enabled and not skip_cache and not history and not session_id

    if use_cache:
        cached, layer = get_query_cache().get(query)
        if cached is not None:
            cached = SecurityFilter.redact_chat_result(cached)
            cached["cache_layer"] = layer
            cached["cache_hit"] = True
            logger.info(
                "query cache hit",
                extra=log_extra(layer=layer, trace_id=trace_id),
            )
            return cached

    result = workflow_chat(
        query,
        history=history,
        workflow=workflow,
        trace_id=trace_id,
        session_id=session_id,
    )

    result = SecurityFilter.redact_chat_result(result)

    if session_id:
        result["session_id"] = session_id

    queue_cfg = get_queue_config()
    defer_cache = queue_cfg.queue.enabled and queue_cfg.queue.defer_cache_write

    if use_cache and not defer_cache:
        get_query_cache().set(query, result)
    elif use_cache and defer_cache:
        logger.info(
            "query cache write deferred to worker",
            extra=log_extra(trace_id=trace_id),
        )

    if use_cache:
        result["cache_hit"] = False
        result["cache_layer"] = None
    else:
        result.setdefault("cache_hit", False)
        result.setdefault("cache_layer", None)

    if queue_cfg.queue.enabled:
        result["post_chat_scheduled"] = enqueue_post_chat_tasks(
            query,
            result,
            trace_id=trace_id,
            session_id=session_id,
            use_query_cache=use_cache,
        )

    return result
