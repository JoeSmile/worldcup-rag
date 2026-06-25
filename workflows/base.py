"""Workflow base types and orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

WorkflowStep = Callable[["WorkflowContext"], "WorkflowContext"]


@dataclass
class WorkflowContext:
    """Mutable state passed through workflow steps."""

    query: str
    history: Optional[List[Dict[str, str]]] = None
    messages: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def final_answer(self) -> Optional[str]:
        return self.metadata.get("answer")

    def set_answer(self, answer: str, **extra: Any) -> None:
        self.metadata["answer"] = answer
        self.metadata.update(extra)


class Workflow(ABC):
    """Sequential workflow: each step receives and returns WorkflowContext."""

    def __init__(self, name: str, steps: Optional[List[WorkflowStep]] = None):
        self.name = name
        self.steps = list[WorkflowStep](steps or [])

    def run(self, query: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Execute all steps and return API/benchmark-compatible response dict."""
        ctx = WorkflowContext(query=query.strip(), history=history)
        if not ctx.query:
            return self._error_response("query cannot be empty")

        for step in self.steps:
            try:
                ctx = step(ctx)
            except Exception as exc:
                ctx.error = str(exc)
                break

        if ctx.error:
            return self._error_response(ctx.error)

        answer = ctx.final_answer
        if not answer:
            return self._error_response("workflow finished without an answer")

        return {
            "answer": answer,
            "tool_name": ctx.metadata.get("tool_name"),
            "tools_used": ctx.metadata.get("tools_used"),
            "sql_generated": ctx.metadata.get("sql_generated"),
            "usage": ctx.metadata.get("usage"),
            "workflow": self.name,
            "error": None,
        }

    def execute(self, query: str, context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Legacy helper: run workflow and return answer text only."""
        history = (context or {}).get("history")
        result = self.run(query, history=history)
        if result.get("error"):
            return result.get("answer")
        return result.get("answer")

    @staticmethod
    def _error_response(message: str) -> Dict[str, Any]:
        return {
            "answer": f"抱歉，处理您的问题时出错：{message}",
            "tool_name": None,
            "tools_used": [],
            "sql_generated": None,
            "usage": {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
            "error": message,
        }


class StepWorkflow(Workflow):
    """Workflow composed of plain step functions."""

    def __init__(self, name: str, steps: List[WorkflowStep]):
        super().__init__(name=name, steps=steps)
