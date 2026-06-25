"""Normalize chat API response fields."""

from __future__ import annotations

from typing import Any

from core.memory_status import resolve_memory_persisted


def enrich_chat_result(result: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    """Attach session_id and three-state memory_persisted to a chat payload."""
    if session_id:
        result["session_id"] = session_id
    result["memory_persisted"] = resolve_memory_persisted(
        session_id,
        persisted=result.get("memory_persisted"),
    )
    return result
