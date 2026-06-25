"""Backward-compatible chat entry; delegates to workflow registry."""

from typing import Dict, List, Optional

from workflows.registry import chat as workflow_chat


def chat(query: str, history: List[Dict] = None, workflow: Optional[str] = None) -> dict:
    return workflow_chat(query, history=history, workflow=workflow)
