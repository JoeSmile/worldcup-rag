"""LangSmith Evaluate evaluators for worldcup-rag experiments."""

from __future__ import annotations

import re
from typing import Any

_MIN_CHUNK_LEN = 4


def _extract_reference(example: Any) -> str:
    outputs = getattr(example, "outputs", None) or {}
    if isinstance(outputs, dict):
        return str(outputs.get("reference") or "").strip()
    return ""


def _extract_answer(run: Any) -> str:
    outputs = getattr(run, "outputs", None) or {}
    if isinstance(outputs, dict):
        return str(outputs.get("answer") or "").strip()
    return ""


def _digit_token_in_text(digit: str, text: str) -> bool:
    """Match a full digit run — avoids '4' matching inside '14'."""
    return re.search(rf"(?<!\d){re.escape(digit)}(?!\d)", text) is not None


def reference_overlap(run: Any, example: Any) -> dict[str, Any]:
    """Score 1 when the answer substantively overlaps the reference."""
    reference = _extract_reference(example)
    answer = _extract_answer(run)
    if not reference:
        return {"key": "reference_overlap", "score": 0.0, "comment": "missing reference"}
    if not answer:
        return {"key": "reference_overlap", "score": 0.0, "comment": "empty answer"}

    if reference in answer:
        return {"key": "reference_overlap", "score": 1.0, "comment": "reference substring"}

    ref_digits = re.findall(r"\d+", reference)
    if ref_digits and all(_digit_token_in_text(d, answer) for d in ref_digits):
        return {"key": "reference_overlap", "score": 1.0, "comment": "digit overlap"}

    for chunk in re.split(r"[，。；、\s]+", reference):
        chunk = chunk.strip()
        if len(chunk) >= _MIN_CHUNK_LEN and chunk in answer:
            return {"key": "reference_overlap", "score": 1.0, "comment": f"matched: {chunk[:20]}"}

    return {"key": "reference_overlap", "score": 0.0}
