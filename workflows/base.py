"""Workflow base types and orchestration."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger, log_extra
from core.memory import SessionMemory, get_session_memory

logger = get_logger("workflows.base")

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

    def run(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute all steps and return API/benchmark-compatible response dict."""
        ctx = WorkflowContext(query=query.strip(), history=history)
        if trace_id:
            ctx.metadata["trace_id"] = trace_id
        if session_id:
            ctx.metadata["session_id"] = session_id

        if not ctx.query:
            return self._attach_session_fields(
                self._error_response("query cannot be empty"),
                session_id=session_id,
            )

        logger.info(
            "workflow started",
            extra=log_extra(workflow=self.name, trace_id=trace_id, step_count=len(self.steps)),
        )

        for step in self.steps:
            step_name = getattr(step, "__name__", str(step))
            try:
                ctx = step(ctx)
            except Exception as exc:
                ctx.error = str(exc)
                logger.exception(
                    "workflow step failed",
                    extra=log_extra(workflow=self.name, step=step_name, trace_id=trace_id),
                )
                break

        if ctx.error:
            logger.warning(
                "workflow finished with error",
                extra=log_extra(workflow=self.name, error=ctx.error, trace_id=trace_id),
            )
            return self._attach_session_fields(
                self._error_response(ctx.error),
                session_id=session_id,
            )

        answer = ctx.final_answer
        if not answer:
            return self._attach_session_fields(
                self._error_response("workflow finished without an answer"),
                session_id=session_id,
            )

        logger.info(
            "workflow completed",
            extra=log_extra(
                workflow=self.name,
                trace_id=trace_id,
                tool_name=ctx.metadata.get("tool_name"),
                tools_used=ctx.metadata.get("tools_used"),
            ),
        )
        response: Dict[str, Any] = {
            "answer": answer,
            "tool_name": ctx.metadata.get("tool_name"),
            "tools_used": ctx.metadata.get("tools_used"),
            "sql_generated": ctx.metadata.get("sql_generated"),
            "usage": ctx.metadata.get("usage"),
            "workflow": self.name,
            "error": None,
        }
        if session_id:
            response["session_id"] = session_id
            response["memory_persisted"] = ctx.metadata.get("memory_persisted", False)
        return response

    @staticmethod
    def _attach_session_fields(
        response: Dict[str, Any],
        *,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        if session_id:
            response["session_id"] = session_id
            response["memory_persisted"] = False
        return response

    def execute(self, query: str, context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Legacy helper: run workflow and return answer text only."""
        history = (context or {}).get("history")
        session_id = (context or {}).get("session_id")
        result = self.run(query, history=history, session_id=session_id)
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


class MemoryAwareWorkflow(Workflow):
    """Workflow with shared Redis session memory load/persist steps."""

    def __init__(
        self,
        name: str,
        steps: List[WorkflowStep],
        memory: SessionMemory | None = None,
    ):
        self.memory = memory or get_session_memory()
        super().__init__(
            name=name,
            steps=[self._load_memory, *steps, self._persist_memory],
        )

    def _load_memory(self, ctx: WorkflowContext) -> WorkflowContext:
        if ctx.history:
            return ctx

        session_id = ctx.metadata.get("session_id")
        if not session_id or not self.memory.available:
            return ctx

        recent, _, _ = self.memory.get_context(session_id, for_workflow=self.name)
        ctx.metadata["memory_recent"] = recent
        return ctx

    def _persist_memory(self, ctx: WorkflowContext) -> WorkflowContext:
        session_id = ctx.metadata.get("session_id")
        if not session_id:
            return ctx

        if ctx.history:
            ctx.metadata["memory_persisted"] = False
            return ctx

        answer = ctx.final_answer
        if not answer or not self.memory.available:
            ctx.metadata["memory_persisted"] = False
            if session_id and answer and not self.memory.available:
                logger.warning(
                    "session memory unavailable",
                    extra=log_extra(
                        workflow=self.name,
                        session_id=session_id,
                        trace_id=ctx.metadata.get("trace_id"),
                    ),
                )
            return ctx

        persisted = self.memory.add_turn(
            session_id,
            ctx.query,
            answer,
            workflow=self.name,
        )
        ctx.metadata["memory_persisted"] = persisted
        if not persisted:
            logger.warning(
                "session memory persist failed",
                extra=log_extra(
                    workflow=self.name,
                    session_id=session_id,
                    trace_id=ctx.metadata.get("trace_id"),
                ),
            )
        return ctx
