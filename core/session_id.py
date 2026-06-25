"""Validate session_id for safe Redis key usage."""

from __future__ import annotations

import re

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SESSION_ID_ERROR = (
    "session_id must be 1-128 chars, start with alphanumeric, "
    "and contain only letters, digits, '.', '_', '-'"
)


def normalize_session_id(raw: str | None) -> str | None:
    """Return normalized session_id, empty string if blank, None if input was None."""
    if raw is None:
        return None
    value = raw.strip()
    return value


def validate_session_id(session_id: str) -> str:
    """Return session_id if valid; raise ValueError otherwise."""
    if not session_id:
        raise ValueError("session_id cannot be empty")
    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError(SESSION_ID_ERROR)
    return session_id
