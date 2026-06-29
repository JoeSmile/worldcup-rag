"""Complex reasoning workflow — iterative SQL replan loop, execute, summarize."""

from __future__ import annotations

import re
from typing import Any

from tools import execute_sql, semantic_search
from core.config import settings
from workflows.base import MemoryAwareWorkflow, WorkflowContext
from workflows.complex_flow_llm import (
    MAX_REPLAN_ROUNDS,
    MAX_SQL_STEPS,
    empty_usage,
    merge_usage,
    replan_next_sql_step,
    summarize_semantic_fallback,
    summarize_sql_results,
)

_POSITION_MAP = {
    "前锋": "FW",
    "中场": "MF",
    "后卫": "DF",
    "门将": "GK",
}


def _sql_generated_from_steps(step_results: list[dict[str, Any]]) -> str | None:
    sqls = [item["sql"] for item in step_results if item.get("sql")]
    return "\n---\n".join(sqls) if sqls else None


def _has_result_rows(step_results: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item.get("result"), dict)
        and not item["result"].get("error")
        and item["result"].get("row_count", 0) > 0
        for item in step_results
    )


def _apply_semantic_fallback(
    ctx: WorkflowContext,
    tools_used: list[str],
) -> list[dict[str, Any]] | None:
    fallback = semantic_search(ctx.query, limit=5)
    ctx.metadata["fallback_result"] = fallback
    if fallback:
        tools_used.append("semantic_search")
    return fallback


def _finalize_replan_metadata(
    ctx: WorkflowContext,
    step_results: list[dict[str, Any]],
    tools_used: list[str],
    plan_method: str,
    plan_reasons: list[str],
    replan_rounds: int,
    usage: dict[str, int],
) -> None:
    ctx.metadata["sql_step_results"] = step_results
    ctx.metadata["tools_used"] = tools_used
    ctx.metadata["plan_method"] = plan_method
    ctx.metadata["plan_reason"] = "；".join(plan_reasons)
    ctx.metadata["replan_rounds"] = replan_rounds
    ctx.metadata["usage"] = usage
    ctx.metadata["sql_generated"] = _sql_generated_from_steps(step_results)
    trace = ctx.metadata.setdefault("tools_trace", [])
    trace.extend(tools_used)


def _heuristic_sql_plans(query: str) -> list[dict[str, str]]:
    """Rule-based fallback when LLM replan is unavailable. No generic default SQL."""
    plans: list[dict[str, str]] = []

    if any(kw in query for kw in ("对比", "和", "谁更", "谁进球")):
        if "梅西" in query and ("C罗" in query or "克里斯蒂亚诺" in query):
            plans.append(
                {
                    "purpose": "梅西进球",
                    "sql": (
                        "SELECT display_name, goals FROM vw_player_summary "
                        "WHERE competition = 'Men''s' AND display_name ILIKE '%Messi%' LIMIT 1"
                    ),
                }
            )
            plans.append(
                {
                    "purpose": "C罗进球",
                    "sql": (
                        "SELECT display_name, goals FROM vw_player_summary "
                        "WHERE competition = 'Men''s' AND display_name ILIKE '%Ronaldo%' "
                        "AND display_name NOT ILIKE '%Luis%' LIMIT 1"
                    ),
                }
            )

    if any(kw in query for kw in ("排名", "最多", "最少", "纪录", "榜单")) or "位置" in query:
        pos = next((code for name, code in _POSITION_MAP.items() if name in query), None)
        if pos:
            plans.append(
                {
                    "purpose": f"{pos}位置进球榜",
                    "sql": (
                        f"SELECT display_name, goals, appearances FROM vw_player_summary "
                        f"WHERE competition = 'Men''s' AND position_code = '{pos}' "
                        "ORDER BY goals DESC LIMIT 5"
                    ),
                }
            )
        elif any(kw in query for kw in ("排名", "最多", "最少", "纪录", "榜单")):
            plans.append(
                {
                    "purpose": "男子进球榜",
                    "sql": (
                        "SELECT display_name, goals FROM vw_player_summary "
                        "WHERE competition = 'Men''s' ORDER BY goals DESC LIMIT 5"
                    ),
                }
            )

    year_match = re.search(r"(20\d{2})", query)
    if year_match and ("中国" in query or "小组赛" in query or "比赛" in query):
        year = year_match.group(1)
        plans.append(
            {
                "purpose": f"{year}年相关比赛",
                "sql": (
                    f"SELECT match_id, home_team, away_team, score, goals FROM vw_match_summary "
                    f"WHERE tournament_id = '{year}' "
                    "AND (home_team ILIKE '%China%' OR away_team ILIKE '%China%')"
                ),
            }
        )
    elif year_match and not plans:
        year = year_match.group(1)
        plans.append(
            {
                "purpose": f"{year}届比赛样本",
                "sql": (
                    f"SELECT match_id, home_team, away_team, score FROM vw_match_summary "
                    f"WHERE tournament_id = '{year}' LIMIT 10"
                ),
            }
        )

    return plans


def _execute_heuristic_batch(ctx: WorkflowContext) -> bool:
    """Run rule-based SQL batch. Returns False when no heuristic patterns matched."""
    plans = _heuristic_sql_plans(ctx.query)[:MAX_SQL_STEPS]
    trace = ctx.metadata.setdefault("tools_trace", [])

    if not plans:
        ctx.metadata["sql_step_results"] = []
        ctx.metadata["tools_used"] = []
        ctx.metadata["plan_method"] = "heuristic_empty"
        ctx.metadata["plan_reason"] = "规则回退无匹配模式"
        ctx.metadata["replan_rounds"] = 0
        ctx.metadata["sql_generated"] = None
        trace.append("plan_sql:heuristic_empty")
        return False

    step_results: list[dict[str, Any]] = []
    tools_used: list[str] = []
    for plan in plans:
        sql = plan.get("sql") or ""
        step_results.append(
            {
                "purpose": plan.get("purpose"),
                "sql": sql,
                "result": execute_sql(sql),
            }
        )
        tools_used.append("sql_query")

    ctx.metadata["sql_step_results"] = step_results
    ctx.metadata["tools_used"] = tools_used
    ctx.metadata["plan_method"] = "heuristic"
    ctx.metadata["plan_reason"] = "规则回退批量计划"
    ctx.metadata["replan_rounds"] = 0
    ctx.metadata["sql_generated"] = _sql_generated_from_steps(step_results)
    trace.append("plan_sql:heuristic")
    trace.extend(tools_used)
    return True


def _finish_heuristic_or_empty(ctx: WorkflowContext, usage: dict[str, int]) -> WorkflowContext:
    executed = _execute_heuristic_batch(ctx)
    tools_used = list(ctx.metadata.get("tools_used") or [])
    step_results = ctx.metadata.get("sql_step_results") or []

    if not executed or not _has_result_rows(step_results):
        _apply_semantic_fallback(ctx, tools_used)
        ctx.metadata["tools_used"] = tools_used

    ctx.metadata["usage"] = usage
    return ctx


def step_replan_execute_loop(ctx: WorkflowContext) -> WorkflowContext:
    ctx.metadata["tools_trace"] = []
    memory_recent = ctx.metadata.get("memory_recent")
    trace_id = ctx.metadata.get("trace_id")

    step_results: list[dict[str, Any]] = []
    tools_used: list[str] = []
    plan_reasons: list[str] = []
    usage = empty_usage()
    seen_sql: set[str] = set()
    replan_rounds = 0
    plan_method = "replan"
    replan_decided_no_sql = False

    for _ in range(MAX_REPLAN_ROUNDS):
        if len(step_results) >= MAX_SQL_STEPS:
            plan_reasons.append(f"已达 SQL 步数上限（{MAX_SQL_STEPS}）")
            break

        next_step, reason, done, round_usage, method = replan_next_sql_step(
            ctx.query,
            step_results,
            step_number=len(step_results) + 1,
            history=ctx.history,
            memory_recent=memory_recent,
            trace_id=trace_id,
        )
        usage = merge_usage(usage, round_usage)
        replan_rounds += 1

        if method == "skipped":
            return _finish_heuristic_or_empty(ctx, usage)

        if method == "failed" and not step_results:
            return _finish_heuristic_or_empty(ctx, merge_usage(usage, empty_usage()))

        if method == "invalid_step" and not step_results:
            return _finish_heuristic_or_empty(ctx, merge_usage(usage, empty_usage()))

        ctx.metadata.setdefault("tools_trace", []).append(f"replan:{method}")
        if method == "failed":
            plan_reasons.append("replan 解析/调用失败，保留已执行步骤")
        if method == "invalid_step":
            plan_reasons.append("replan 返回无效 SQL，保留已执行步骤")
        if reason:
            plan_reasons.append(reason)

        if done or not next_step:
            if done and not step_results and method == "llm":
                replan_decided_no_sql = True
            break

        sql = next_step.get("sql") or ""
        normalized_sql = " ".join(sql.split())
        if normalized_sql in seen_sql:
            plan_reasons.append("重复 SQL，停止迭代")
            break
        seen_sql.add(normalized_sql)

        result = execute_sql(sql)
        step_results.append(
            {
                "purpose": next_step.get("purpose"),
                "sql": sql,
                "result": result,
            }
        )
        tools_used.append("sql_query")

    if not step_results and plan_method == "replan":
        if replan_decided_no_sql:
            _apply_semantic_fallback(ctx, tools_used)
            _finalize_replan_metadata(
                ctx,
                step_results,
                tools_used,
                "replan_done",
                plan_reasons,
                replan_rounds,
                usage,
            )
            return ctx
        return _finish_heuristic_or_empty(ctx, merge_usage(usage, empty_usage()))

    if not _has_result_rows(step_results):
        _apply_semantic_fallback(ctx, tools_used)

    _finalize_replan_metadata(
        ctx,
        step_results,
        tools_used,
        plan_method,
        plan_reasons,
        replan_rounds,
        usage,
    )
    return ctx


def step_summarize(ctx: WorkflowContext) -> WorkflowContext:
    plan_reason = ctx.metadata.get("plan_reason") or ""
    step_results = ctx.metadata.get("sql_step_results") or []
    usage = ctx.metadata.get("usage") or empty_usage()
    tools_used = list(ctx.metadata.get("tools_used") or [])
    memory_recent = ctx.metadata.get("memory_recent")
    summarize_kwargs = {
        "history": ctx.history,
        "memory_recent": memory_recent,
        "trace_id": ctx.metadata.get("trace_id"),
    }

    fallback = ctx.metadata.get("fallback_result")
    has_rows = _has_result_rows(step_results)

    if has_rows:
        answer, sum_usage, sum_method = summarize_sql_results(
            ctx.query,
            plan_reason,
            step_results,
            **summarize_kwargs,
        )
        ctx.metadata["tools_trace"].append(f"summarize:{sum_method}")
        usage = merge_usage(usage, sum_usage)
    elif isinstance(fallback, list) and fallback:
        answer, sum_usage, sum_method = summarize_semantic_fallback(
            ctx.query,
            fallback,
            plan_reason,
            **summarize_kwargs,
        )
        ctx.metadata["tools_trace"].append(f"summarize:{sum_method}")
        usage = merge_usage(usage, sum_usage)
    else:
        answer, sum_usage, sum_method = summarize_sql_results(
            ctx.query,
            plan_reason,
            step_results,
            **summarize_kwargs,
        )
        ctx.metadata["tools_trace"].append(f"summarize:{sum_method}")
        usage = merge_usage(usage, sum_usage)

    ctx.set_answer(
        answer,
        tool_name=tools_used[-1] if tools_used else "complex_flow",
        tools_used=tools_used,
        sql_generated=ctx.metadata.get("sql_generated"),
        usage=usage,
        model=settings.resolved_complex_flow_model_name,
        plan_method=ctx.metadata.get("plan_method"),
        plan_reason=plan_reason,
        sql_step_count=len(step_results),
        replan_rounds=ctx.metadata.get("replan_rounds"),
    )
    return ctx


complex_flow_workflow = MemoryAwareWorkflow(
    name="complex_flow",
    steps=[
        step_replan_execute_loop,
        step_summarize,
    ],
)
