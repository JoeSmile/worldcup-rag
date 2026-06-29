"""LLM helpers for complex_flow: multi-step SQL planning and summarization."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from core.config import settings
from core.logger import get_logger, log_extra
from core.security import SecurityFilter
from prompts_complex_flow import (
    COMPLEX_FLOW_SQL_REPLAN_PROMPT,
    COMPLEX_FLOW_SUMMARY_PROMPT,
)

logger = get_logger("workflows.complex_flow_llm")

# Hard caps per user query (replan loop cannot exceed these).
MAX_REPLAN_ROUNDS = 3
MAX_SQL_STEPS = 3
_MAX_RESULT_ROWS = 20


def empty_usage() -> dict[str, int]:
    return _empty_usage()


def merge_usage(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    return _merge_usage(base, extra)


def _empty_usage() -> dict[str, int]:
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}


def _merge_usage(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    return {
        "total_tokens": base.get("total_tokens", 0) + extra.get("total_tokens", 0),
        "prompt_tokens": base.get("prompt_tokens", 0) + extra.get("prompt_tokens", 0),
        "completion_tokens": base.get("completion_tokens", 0) + extra.get("completion_tokens", 0),
    }


def _usage_from_response(response: Any) -> dict[str, int]:
    meta = getattr(response, "response_metadata", None) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}
    if not usage and getattr(response, "usage_metadata", None):
        usage = response.usage_metadata or {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return {
        "total_tokens": total,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _validate_sql(sql: str) -> bool:
    normalized = sql.strip()
    if not normalized:
        return False
    return not SecurityFilter.is_unsafe_sql(normalized)


def _format_history_block(
    history: list[dict[str, str]] | None,
    memory_recent: list[dict[str, str]] | None,
) -> str:
    lines: list[str] = []
    if history:
        for item in history[-3:]:
            lines.append(f"user: {item.get('user', '')}")
            lines.append(f"assistant: {item.get('assistant', '')}")
    elif memory_recent:
        for msg in memory_recent[-6:]:
            lines.append(f"{msg.get('role', 'user')}: {msg.get('content', '')}")
    if not lines:
        return ""
    return "【对话上下文】\n" + "\n".join(lines) + "\n\n"


@lru_cache(maxsize=4)
def _get_complex_flow_llm(model_name: str):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
    ), HumanMessage, SystemMessage


def _get_planner_llm():
    return _get_complex_flow_llm(settings.resolved_complex_flow_model_name)


def _truncate_rows(rows: list[Any], limit: int = _MAX_RESULT_ROWS) -> list[Any]:
    return rows[:limit]


def _serialize_executed_steps(executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for index, item in enumerate(executed_steps, start=1):
        result = item.get("result") or {}
        serialized.append(
            {
                "step": index,
                "purpose": item.get("purpose"),
                "sql": item.get("sql"),
                "row_count": result.get("row_count"),
                "error": result.get("error"),
                "rows": _truncate_rows(result.get("rows") or []),
            }
        )
    return serialized


def _normalize_replan(payload: dict[str, Any]) -> tuple[dict[str, str] | None, str, bool, bool]:
    """Return (step, reason, done, explicit_done).

    explicit_done is True only when the payload sets done=true (or equivalent string).
    Invalid/missing step forces done=True but explicit_done=False so callers can fall back to heuristics.
    """
    reason = str(payload.get("reason") or "").strip()
    explicit_done = _parse_bool_flag(payload.get("done"))
    if explicit_done:
        return None, reason, True, True

    step_raw = payload.get("step")
    if not isinstance(step_raw, dict):
        return None, reason, True, False

    sql = str(step_raw.get("sql") or "").strip()
    if not _validate_sql(sql):
        return None, reason, True, False

    purpose = str(step_raw.get("purpose") or "next_step").strip()
    return {"purpose": purpose, "sql": sql}, reason, False, False


def replan_next_sql_step(
    query: str,
    executed_steps: list[dict[str, Any]],
    *,
    step_number: int,
    history: list[dict[str, str]] | None = None,
    memory_recent: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> tuple[dict[str, str] | None, str, bool, dict[str, int], str]:
    """Return (next_step, reason, done, usage, method)."""
    if not settings.llm_api_key:
        return None, "", True, _empty_usage(), "skipped"

    llm, HumanMessage, SystemMessage = _get_planner_llm()
    payload = {
        "question": query,
        "executed_step_count": len(executed_steps),
        "next_step_number": step_number,
        "executed_steps": _serialize_executed_steps(executed_steps),
    }
    user_parts = [
        _format_history_block(history, memory_recent),
        "【迭代规划任务】\n" + json.dumps(payload, ensure_ascii=False, default=str),
    ]
    run_config = settings.langsmith_run_config(
        "complex_flow_replan",
        trace_id=trace_id,
        tags=["complex_flow", "sql_replan"],
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(content=COMPLEX_FLOW_SQL_REPLAN_PROMPT),
                HumanMessage(content="\n".join(user_parts)),
            ],
            config=run_config,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        usage = _usage_from_response(response)
        parsed = _extract_json_object(content)
        if not parsed:
            logger.warning(
                "complex_flow replan parse failed",
                extra=log_extra(trace_id=trace_id, preview=content[:200]),
            )
            return None, "", True, usage, "failed"

        step, reason, done, explicit_done = _normalize_replan(parsed)
        if done and not explicit_done:
            return step, reason, done, usage, "invalid_step"
        return step, reason, done, usage, "llm"
    except Exception as exc:
        logger.warning(
            "complex_flow replan llm failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )
        return None, "", True, _empty_usage(), "failed"


def summarize_sql_results(
    query: str,
    plan_reason: str,
    step_results: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
    memory_recent: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> tuple[str, dict[str, int], str]:
    """Return (answer, usage, method) where method is llm|template."""
    if not settings.llm_api_key:
        return _template_summary(query, plan_reason, step_results), _empty_usage(), "template"

    payload = {
        "question": query,
        "plan_reason": plan_reason,
        "steps": [
            {
                "purpose": item.get("purpose"),
                "sql": item.get("sql"),
                "row_count": item.get("result", {}).get("row_count"),
                "error": item.get("result", {}).get("error"),
                "rows": _truncate_rows(item.get("result", {}).get("rows") or []),
            }
            for item in step_results
        ],
    }
    history_block = _format_history_block(history, memory_recent)
    user_content = (
        (history_block + "请根据以下 SQL 执行结果回答用户问题。\n\n" if history_block else "请根据以下 SQL 执行结果回答用户问题。\n\n")
        + json.dumps(payload, ensure_ascii=False, default=str)
    )

    llm, HumanMessage, SystemMessage = _get_planner_llm()
    run_config = settings.langsmith_run_config(
        "complex_flow_summarize",
        trace_id=trace_id,
        tags=["complex_flow", "summarize"],
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(content=COMPLEX_FLOW_SUMMARY_PROMPT),
                HumanMessage(content=user_content),
            ],
            config=run_config,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        usage = _usage_from_response(response)
        answer = content.strip()
        if answer:
            return answer, usage, "llm"
    except Exception as exc:
        logger.warning(
            "complex_flow summarize llm failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )

    return _template_summary(query, plan_reason, step_results), _empty_usage(), "template"


def _template_semantic_fallback(
    query: str,
    plan_reason: str,
    fallback_chunks: list[dict[str, Any]],
) -> str:
    lines = ["结构化 SQL 未返回有效结果，已改用语义检索。", ""]
    if plan_reason:
        lines.insert(1, f"分析：{plan_reason}")
        lines.append("")
    for index, item in enumerate(fallback_chunks[:3], start=1):
        content = (item.get("content") or "").strip()
        preview = content[:280] + ("…" if len(content) > 280 else "")
        lines.append(
            f"{index}. [{item.get('collection')}/{item.get('external_id')}] "
            f"(相似度 {item.get('similarity')})"
        )
        if preview:
            lines.append(f"   {preview}")
    return "\n".join(lines)


def summarize_semantic_fallback(
    query: str,
    fallback_chunks: list[dict[str, Any]],
    plan_reason: str = "",
    *,
    history: list[dict[str, str]] | None = None,
    memory_recent: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> tuple[str, dict[str, int], str]:
    """Return (answer, usage, method) where method is llm|template."""
    if not settings.llm_api_key:
        return (
            _template_semantic_fallback(query, plan_reason, fallback_chunks),
            _empty_usage(),
            "template",
        )

    payload = {
        "question": query,
        "plan_reason": plan_reason,
        "semantic_chunks": [
            {
                "collection": item.get("collection"),
                "external_id": item.get("external_id"),
                "similarity": item.get("similarity"),
                "content": (item.get("content") or "")[:500],
            }
            for item in fallback_chunks[:5]
        ],
    }
    history_block = _format_history_block(history, memory_recent)
    user_content = (
        (history_block if history_block else "")
        + "结构化 SQL 无有效结果。请根据以下语义检索片段回答用户问题，不编造数字。\n\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )

    llm, HumanMessage, SystemMessage = _get_planner_llm()
    run_config = settings.langsmith_run_config(
        "complex_flow_summarize_semantic",
        trace_id=trace_id,
        tags=["complex_flow", "summarize", "semantic_fallback"],
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(content=COMPLEX_FLOW_SUMMARY_PROMPT),
                HumanMessage(content=user_content),
            ],
            config=run_config,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        usage = _usage_from_response(response)
        answer = content.strip()
        if answer:
            return answer, usage, "llm"
    except Exception as exc:
        logger.warning(
            "complex_flow semantic summarize llm failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )

    return (
        _template_semantic_fallback(query, plan_reason, fallback_chunks),
        _empty_usage(),
        "template",
    )


def _template_summary(query: str, plan_reason: str, step_results: list[dict[str, Any]]) -> str:
    lines = [f"问题：{query}"]
    if plan_reason:
        lines.append(f"分析：{plan_reason}")
    lines.append("")

    any_rows = False
    for index, item in enumerate(step_results, start=1):
        purpose = item.get("purpose") or f"步骤 {index}"
        result = item.get("result") or {}
        lines.append(f"**{index}. {purpose}**")
        if result.get("error"):
            lines.append(f"- 查询失败：{result['error']}")
            continue
        rows = result.get("rows") or []
        if not rows:
            lines.append("- 无匹配行")
            continue
        any_rows = True
        preview = json.dumps(_truncate_rows(rows, 5), ensure_ascii=False)
        lines.append(f"- 共 {result.get('row_count', len(rows))} 行，示例：{preview}")

    if not any_rows:
        lines.append("未找到足够结构化数据，请尝试换个问法或简化条件。")
    return "\n".join(lines)
