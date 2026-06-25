"""Route user queries to simple_qa, complex_flow, or gossip."""

from __future__ import annotations

from typing import Any, Optional

from core.config import settings
from core.logger import get_logger, log_extra
from core.memory import get_session_memory
from workflows.llm_router import (
    has_strong_rule_signal,
    is_ambiguous,
    llm_route,
)
from workflows.route_keywords import (
    COMPLEX_KEYWORDS,
    COMPLEX_WITH_AND,
    GOSSIP_KEYWORDS,
    prefers_simple_qa,
)

logger = get_logger("workflows.router")


def route(query: str) -> str:
    """Return workflow name: gossip | complex_flow | simple_qa."""
    text = query.strip()
    if not text:
        return "simple_qa"

    if any(kw in text for kw in GOSSIP_KEYWORDS):
        return "gossip"

    if prefers_simple_qa(text):
        return "simple_qa"

    if any(kw in text for kw in COMPLEX_KEYWORDS):
        return "complex_flow"

    if any(kw in text for kw in COMPLEX_WITH_AND) and any(
        hint in text for hint in ("谁", "哪个", "哪支", "哪家", "更多", "更强")
    ):
        return "complex_flow"

    return "simple_qa"


class WorkflowRouter:
    """Hybrid router: keyword fast path + optional small LLM for ambiguous turns."""

    def __init__(self) -> None:
        self._memory = get_session_memory()

    def route(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> tuple[str, dict[str, Any]]:
        rule_choice = route(query)
        meta: dict[str, Any] = {
            "method": "rule",
            "confidence": 1.0,
            "reason": "keyword_router",
            "rule_choice": rule_choice,
        }

        memory_ctx: dict[str, Any] = {"recent": [], "summary": ""}
        if session_id and self._memory.available:
            memory_ctx = self._memory.get_router_context(session_id)

        has_history = bool(memory_ctx.get("recent"))
        use_llm = (
            settings.router_llm_enabled
            and session_id
            and has_history
            and not has_strong_rule_signal(query)
            and is_ambiguous(query, has_session_history=has_history)
        )

        if not use_llm:
            return rule_choice, meta

        decision = llm_route(query, memory_ctx, trace_id=trace_id)
        if decision is None:
            meta["method"] = "rule_fallback"
            meta["reason"] = "llm_unavailable"
            return rule_choice, meta

        if decision.confidence >= settings.router_confidence_threshold:
            meta.update(
                {
                    "method": decision.method,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "llm_choice": decision.workflow,
                }
            )
            return decision.workflow, meta

        meta.update(
            {
                "method": "rule_fallback",
                "confidence": decision.confidence,
                "reason": decision.reason or "low_confidence",
                "llm_choice": decision.workflow,
            }
        )
        return rule_choice, meta

    def list_routes(self) -> dict[str, str]:
        return {
            "gossip": "足球八卦、绯闻、趣闻、花絮等闲聊",
            "complex_flow": "对比、排行、多条件统计等复杂查询",
            "simple_qa": "默认：球员数据、赛果、单一事实问答",
        }

    def run(
        self,
        query: str,
        history: Optional[list] = None,
        workflow: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Route (unless workflow forced) and execute."""
        from workflows.registry import registry

        if workflow is not None:
            chosen = workflow
            router_meta = {"method": "explicit", "confidence": 1.0, "reason": "client_forced"}
        else:
            chosen, router_meta = self.route(
                query, session_id=session_id, trace_id=trace_id
            )

        logger.info(
            "router chose workflow",
            extra=log_extra(
                router_choice=chosen,
                auto_routed=workflow is None,
                trace_id=trace_id,
                query_len=len(query.strip()),
                session_id=session_id,
                router_method=router_meta.get("method"),
                router_confidence=router_meta.get("confidence"),
            ),
        )

        wf = registry.get(chosen)
        if wf is None:
            available = ", ".join(registry.list_names())
            raise ValueError(f"Unknown workflow '{chosen}'. Available: {available}")

        result = wf.run(
            query, history=history, trace_id=trace_id, session_id=session_id
        )
        result["router_choice"] = chosen if workflow is None else workflow
        result["auto_routed"] = workflow is None
        result["router_method"] = router_meta.get("method")
        result["router_confidence"] = router_meta.get("confidence")
        if router_meta.get("reason"):
            result["router_reason"] = router_meta.get("reason")
        return result


default_router = WorkflowRouter()
