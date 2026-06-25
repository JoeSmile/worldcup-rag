"""Resolve memory_persisted for API responses."""

from __future__ import annotations


def resolve_memory_persisted(
    session_id: str | None,
    *,
    persisted: bool | None,
    skipped: bool = False,
) -> bool | None:
    """
    Three-state memory_persisted:
    - None: no session_id (not applicable)
    - False: session present but skipped, unavailable, or write failed
    - True: turn persisted successfully
    """
    if not session_id:
        return None
    if skipped:
        return False
    if persisted is True:
        return True
    return False
