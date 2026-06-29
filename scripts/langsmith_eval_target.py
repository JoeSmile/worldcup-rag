"""LangSmith Evaluate target — returns a flat dict for evaluator path mapping.

Evaluate UI: set Target to this module's `run_agent` or langgraph graph `worldcup_chat`.
Map evaluator variables (Example / Run):
  userQuestion     → input.query
  graph            → input.graph
  referenceOutput  → referenceOutput.reference
  assistantAnswer  → output.answer
"""

from __future__ import annotations

from typing import Any


def _extract_user_question(inputs: dict[str, Any]) -> str:
    if inputs.get("query"):
        return str(inputs["query"]).strip()
    if inputs.get("user_question"):
        return str(inputs["user_question"]).strip()
    messages = inputs.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict) and last.get("content"):
            return str(last["content"]).strip()
    return ""


def run_agent(inputs: dict[str, Any]) -> dict[str, Any]:
    from agent import chat

    question = _extract_user_question(inputs)
    workflow = inputs.get("graph") or inputs.get("workflow")

    result = chat(
        question,
        workflow=workflow,
        skip_cache=True,
        record_metrics=False,
    )
    return {
        "answer": result.get("answer") or "",
        "workflow": result.get("workflow"),
        "tools_used": result.get("tools_used") or [],
        "error": result.get("error"),
    }
