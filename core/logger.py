"""Structured JSON logging with per-request trace_id."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

_CONFIGURED = False
_LOGGER_NAMESPACE = "worldcup-rag"


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None) or trace_id_var.get(),
        }

        context = getattr(record, "log_context", None)
        if isinstance(context, dict) and context:
            log_obj["context"] = context

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_obj["exception"] = record.exc_text

        return json.dumps(log_obj, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure the worldcup-rag logger tree once."""
    global _CONFIGURED

    resolved_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger(_LOGGER_NAMESPACE)
    root.setLevel(resolved_level)

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        root.addHandler(handler)
        root.propagate = False
        _CONFIGURED = True

    return root


def get_logger(name: str) -> logging.Logger:
    if name.startswith(f"{_LOGGER_NAMESPACE}.") or name == _LOGGER_NAMESPACE:
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAMESPACE}.{name}")


def new_trace_id() -> str:
    return uuid4().hex[:16]


def bind_trace_id(trace_id: str | None) -> str:
    resolved = trace_id or new_trace_id()
    trace_id_var.set(resolved)
    return resolved


def get_trace_id() -> str | None:
    return trace_id_var.get()


def log_extra(**context: Any) -> dict[str, Any]:
    """Build logger extra= dict with trace_id and structured context."""
    extra: dict[str, Any] = {"trace_id": get_trace_id()}
    if context:
        extra["log_context"] = context
    return extra
