"""Chat request validation helpers."""

from __future__ import annotations

HISTORY_SESSION_CONFLICT = (
    "provide either session_id (server memory) or history (client), not both"
)


def validate_history_session_conflict(
    *,
    session_id: str | None,
    history: list | None,
) -> None:
    if session_id and history is not None:
        raise ValueError(HISTORY_SESSION_CONFLICT)
