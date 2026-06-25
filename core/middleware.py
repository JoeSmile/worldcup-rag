"""HTTP middleware: inbound sanitization and outbound answer scanning."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse, Response

from core.logger import get_logger, log_extra
from core.security import SecurityFilter
from core.security_config import get_security_config

logger = get_logger("security_middleware")

_CHAT_PATH = "/chat"


async def security_middleware(request: Request, call_next) -> Response:
    cfg = get_security_config().security
    if not cfg.enabled or request.url.path != _CHAT_PATH or request.method != "POST":
        return await call_next(request)

    trace_id = getattr(request.state, "trace_id", None)

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return await call_next(request)

    raw_body = await request.body()
    if not raw_body:
        return await call_next(request)

    try:
        data: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    original_query = data.get("query") if isinstance(data.get("query"), str) else ""

    if cfg.block_sql_injection_in_query and SecurityFilter.looks_like_sql_injection_in_query(original_query):
        SecurityFilter.audit(
            "inbound",
            "blocked_sql_injection",
            trace_id=trace_id,
            query_len=len(original_query),
        )
        return JSONResponse(
            status_code=400,
            content={"detail": "Request blocked: suspicious SQL pattern in query"},
        )

    if cfg.sanitize_input:
        data = SecurityFilter.sanitize_chat_payload(data)
        sanitized_query = data.get("query") if isinstance(data.get("query"), str) else ""
        if sanitized_query != original_query:
            SecurityFilter.audit(
                "inbound",
                "input_sanitized",
                trace_id=trace_id,
                query_len=len(original_query),
            )

    modified_body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": modified_body, "more_body": False}

    request._receive = receive  # noqa: SLF001 — Starlette body rewrite pattern

    response = await call_next(request)

    if not cfg.scan_output or response.status_code != 200:
        return response

    if getattr(request.state, "security_redacted_at_agent", False):
        return response

    body_bytes = b""
    async for chunk in response.body_iterator:
        body_bytes += chunk

    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    if not isinstance(payload, dict):
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    original_answer = payload.get("answer")
    original_sql = payload.get("sql_generated")
    payload, scan = SecurityFilter.scan_and_redact_chat_response(payload)

    if not scan.safe:
        SecurityFilter.audit(
            "outbound",
            "output_issue",
            trace_id=trace_id,
            issues=scan.issues,
            answer_redacted=payload.get("answer") != original_answer,
            sql_redacted=payload.get("sql_generated") != original_sql,
        )
        logger.warning(
            "output sensitive pattern detected",
            extra=log_extra(trace_id=trace_id, issues=scan.issues),
        )

    headers = dict(response.headers)
    headers.pop("content-length", None)
    return JSONResponse(content=payload, status_code=response.status_code, headers=headers)
