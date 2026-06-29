"""Prometheus metrics for chat SLA and query-cache observability."""

from __future__ import annotations

import os
import re
import secrets
import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from prometheus_client import CollectorRegistry, multiprocess
from starlette.requests import Request
from starlette.responses import Response

from core.metrics_config import get_metrics_config

_SESSION_PATH = re.compile(r"^/session/[^/]+")

CHAT_REQUESTS_TOTAL = Counter(
    "worldcup_chat_requests_total",
    "Chat requests handled by agent.chat",
    ["workflow", "status", "cache_hit"],
)

CHAT_DURATION_SECONDS = Histogram(
    "worldcup_chat_duration_seconds",
    "Chat request duration in seconds",
    ["workflow", "cache_layer"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

CACHE_LOOKUP_TOTAL = Counter(
    "worldcup_cache_lookup_total",
    "Query cache lookups by result layer",
    ["layer"],
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

HTTP_ACTIVE_REQUESTS = Gauge(
    "http_active_requests",
    "In-flight HTTP requests",
)

LLM_TOKEN_CONSUMPTION_TOTAL = Counter(
    "worldcup_llm_tokens_total",
    "LLM token usage from chat responses (prompt + completion only)",
    ["model", "type"],
)


def metrics_enabled() -> bool:
    return get_metrics_config().metrics.enabled


def chat_status_from_result(result: dict[str, Any] | None, status: str | None = None) -> str:
    """Map chat payload to ok|error for SLA counters."""
    if status is not None:
        return status
    if result is None or result.get("error"):
        return "error"
    return "ok"


def _resolve_usage_model(result: dict[str, Any] | None) -> str:
    from core.config import get_settings

    settings = get_settings()
    if result and result.get("model"):
        return str(result["model"])
    workflow = (result or {}).get("workflow") or ""
    if workflow == "complex_flow":
        return settings.resolved_complex_flow_model_name
    return settings.model_name


def init_prometheus_multiproc() -> None:
    """Clear stale multiprocess metric files before workers start."""
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return
    os.makedirs(multiproc_dir, exist_ok=True)
    for name in os.listdir(multiproc_dir):
        path = os.path.join(multiproc_dir, name)
        if os.path.isfile(path):
            os.remove(path)


def normalize_http_path(path: str) -> str:
    """Collapse high-cardinality paths for metric labels."""
    if _SESSION_PATH.match(path):
        return "/session/{session_id}"
    return path


def _generate_metrics_body() -> bytes:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest()


def metrics_response() -> Response:
    return Response(_generate_metrics_body(), media_type=CONTENT_TYPE_LATEST)


def authorize_metrics_request(request: Request) -> bool:
    cfg = get_metrics_config().metrics
    if cfg.public_endpoint:
        return True
    expected = (cfg.auth_token or "").strip()
    if not expected:
        return False
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return False
    return secrets.compare_digest(header[7:].strip(), expected)


def record_cache_lookup(layer: str | None) -> None:
    if not metrics_enabled():
        return
    CACHE_LOOKUP_TOTAL.labels(layer=layer or "miss").inc()


def record_chat_result(
    result: dict[str, Any] | None,
    duration_seconds: float,
    status: str | None = None,
) -> None:
    if not metrics_enabled():
        return

    resolved_status = chat_status_from_result(result, status)
    workflow = (result or {}).get("workflow") or "unknown"
    cache_hit = "true" if (result or {}).get("cache_hit") else "false"
    cache_layer = (result or {}).get("cache_layer") or "none"

    CHAT_REQUESTS_TOTAL.labels(workflow=workflow, status=resolved_status, cache_hit=cache_hit).inc()
    CHAT_DURATION_SECONDS.labels(workflow=workflow, cache_layer=cache_layer).observe(duration_seconds)

    usage = (result or {}).get("usage") or {}
    model = _resolve_usage_model(result)
    if isinstance(usage, dict):
        for token_type in ("prompt_tokens", "completion_tokens"):
            count = usage.get(token_type)
            if isinstance(count, int) and count > 0:
                LLM_TOKEN_CONSUMPTION_TOTAL.labels(model=model, type=token_type).inc(count)


async def metrics_middleware(request: Request, call_next) -> Response:
    """Record HTTP-level latency and in-flight gauge for all routes."""
    if not metrics_enabled() or request.url.path == "/metrics":
        return await call_next(request)

    HTTP_ACTIVE_REQUESTS.inc()
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - start
        endpoint = normalize_http_path(request.url.path)
        HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, endpoint=endpoint).observe(duration)
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=endpoint,
            status=str(status_code),
        ).inc()
        HTTP_ACTIVE_REQUESTS.dec()
