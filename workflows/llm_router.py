"""Small-model router for ambiguous session turns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from core.config import settings
from core.logger import get_logger, log_extra
from workflows.route_keywords import (
    AMBIGUOUS_PRONOUNS,
    COMPLEX_KEYWORDS,
    GOSSIP_KEYWORDS,
)

logger = get_logger("workflows.llm_router")

_ROUTE_DESCRIPTIONS = {
    "simple_qa": "单一事实问答：球员数据、赛果、进球数等",
    "complex_flow": "对比、排行、多条件统计、复杂查询",
    "gossip": "足球八卦、绯闻、趣闻、花絮等闲聊",
}


@dataclass
class RouterDecision:
    workflow: str
    confidence: float
    reason: str
    method: str


def has_strong_rule_signal(query: str) -> bool:
    text = query.strip()
    if any(kw in text for kw in GOSSIP_KEYWORDS):
        return True
    if any(kw in text for kw in COMPLEX_KEYWORDS):
        return True
    return False


def is_ambiguous(query: str, *, has_session_history: bool) -> bool:
    """True only for pronoun/continuation cues that need session context."""
    if not has_session_history:
        return False

    text = query.strip()
    if any(token in text for token in AMBIGUOUS_PRONOUNS):
        return True

    if len(text) <= 6 and not has_strong_rule_signal(query):
        return True

    return False


def _format_router_prompt(query: str, memory_ctx: dict[str, Any]) -> str:
    lines = ["请根据会话上下文和当前问题，选择最合适的 workflow。"]
    summary = memory_ctx.get("summary") or ""
    if summary:
        lines.append(f"【会话摘要】{summary}")

    recent = memory_ctx.get("recent") or []
    if recent:
        lines.append("【最近对话】")
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            wf = msg.get("workflow")
            suffix = f" (workflow={wf})" if wf else ""
            lines.append(f"{role}{suffix}: {content}")

    lines.append(f"【当前问题】{query}")
    lines.append("")
    lines.append("可选 workflow：")
    for name, desc in _ROUTE_DESCRIPTIONS.items():
        lines.append(f"- {name}: {desc}")
    lines.append("")
    lines.append(
        '只输出 JSON：{"workflow":"simple_qa|complex_flow|gossip","confidence":0.0-1.0,"reason":"..."}'
    )
    return "\n".join(lines)


def _parse_router_response(raw: str) -> RouterDecision | None:
    text = raw.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    workflow = payload.get("workflow")
    if workflow not in _ROUTE_DESCRIPTIONS:
        return None

    confidence = float(payload.get("confidence", 0))
    reason = str(payload.get("reason", ""))
    return RouterDecision(
        workflow=workflow,
        confidence=max(0.0, min(confidence, 1.0)),
        reason=reason,
        method="llm",
    )


@lru_cache(maxsize=1)
def _get_router_llm():
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.router_model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
    ), HumanMessage, SystemMessage


def llm_route(
    query: str,
    memory_ctx: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> RouterDecision | None:
    if not settings.llm_api_key:
        logger.warning("router llm skipped: no API key", extra=log_extra(trace_id=trace_id))
        return None

    llm, HumanMessage, SystemMessage = _get_router_llm()
    prompt = _format_router_prompt(query, memory_ctx)
    run_config = settings.langsmith_run_config(
        "router",
        trace_id=trace_id,
        tags=["router"],
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(content="你是 workflow 路由分类器，只返回 JSON。"),
                HumanMessage(content=prompt),
            ],
            config=run_config,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _parse_router_response(content)
    except Exception as exc:
        logger.warning(
            "router llm failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )
        return None
