"""Mock complex reasoning workflow — multi-step SQL plan, execute, fallback, summarize.

Reserved for future: multi-table JOIN, nested queries, cross-entity comparison.
Currently uses heuristics + real tools where possible; no ComplexReasoningAgent yet.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tools import execute_sql, semantic_search
from workflows.base import StepWorkflow, WorkflowContext

_COMPLEX_HINTS = ("对比", "和", "谁更", "排名", "名单", "统计", "合计", "分别", "各届", "对比分析")
_POSITION_MAP = {
    "前锋": "FW",
    "中场": "MF",
    "后卫": "DF",
    "门将": "GK",
}


def _detect_intent(query: str) -> dict[str, Any]:
    lowered = query.lower()
    intents: list[str] = []
    if any(kw in query for kw in ("对比", "和", "谁更", "谁进球")):
        intents.append("player_compare")
    if any(kw in query for kw in ("排名", "最多", "最少", "纪录", "榜单")):
        intents.append("leaderboard")
    if "位置" in query or any(p in query for p in _POSITION_MAP):
        intents.append("position_filter")
    if re.search(r"20\d{2}", query):
        intents.append("tournament_scope")
    if not intents:
        intents.append("general_complex")
    return {
        "intents": intents,
        "is_complex": any(h in query for h in _COMPLEX_HINTS) or len(intents) > 1,
    }


def step_analyze_query(ctx: WorkflowContext) -> WorkflowContext:
    analysis = _detect_intent(ctx.query)
    ctx.metadata["analysis"] = analysis
    ctx.metadata["tools_trace"] = ["analyze_query"]
    return ctx


def step_plan_queries(ctx: WorkflowContext) -> WorkflowContext:
    intents = ctx.metadata.get("analysis", {}).get("intents", [])
    plans: list[str] = []

    if "leaderboard" in intents or "position_filter" in intents:
        pos = next((code for name, code in _POSITION_MAP.items() if name in ctx.query), None)
        if pos:
            plans.append(
                f"SELECT display_name, goals, appearances FROM vw_player_summary "
                f"WHERE competition = 'Men''s' AND position_code = '{pos}' "
                "ORDER BY goals DESC LIMIT 5"
            )
        else:
            plans.append(
                "SELECT display_name, goals FROM vw_player_summary "
                "WHERE competition = 'Men''s' ORDER BY goals DESC LIMIT 5"
            )

    if "player_compare" in intents:
        plans.append(
            "SELECT display_name, goals, appearances FROM vw_player_summary "
            "WHERE competition = 'Men''s' AND display_name ILIKE '%Messi%' OR display_name ILIKE '%Ronaldo%'"
        )

    if "tournament_scope" in intents:
        year = re.search(r"(20\d{2})", ctx.query)
        if year:
            plans.append(
                f"SELECT match_id, home_team, away_team, score FROM vw_match_summary "
                f"WHERE tournament_id = '{year.group(1)}' LIMIT 10"
            )

    if not plans:
        plans.append(
            "[mock] 复杂查询计划：多表关联 vw_player_summary + vw_match_summary（尚未实现）"
        )

    ctx.metadata["sql_plans"] = plans
    ctx.metadata["tools_trace"].append("plan_queries")
    return ctx


def step_execute_with_fallback(ctx: WorkflowContext) -> WorkflowContext:
    plans = ctx.metadata.get("sql_plans", [])
    tools_used: list[str] = []
    sql_generated = None
    raw_result: Any = None

    for sql in plans:
        if sql.startswith("[mock]"):
            continue
        sql_generated = sql
        raw_result = execute_sql(sql)
        tools_used.append("sql_query")
        if isinstance(raw_result, dict) and not raw_result.get("error") and raw_result.get("row_count", 0) > 0:
            break

    if not raw_result or (isinstance(raw_result, dict) and raw_result.get("error")):
        raw_result = semantic_search(ctx.query, limit=5)
        tools_used.append("semantic_search")

    ctx.metadata["raw_result"] = raw_result
    ctx.metadata["sql_generated"] = sql_generated
    ctx.metadata["tools_used"] = tools_used
    ctx.metadata["tools_trace"].extend(tools_used)
    return ctx


def step_summarize(ctx: WorkflowContext) -> WorkflowContext:
    analysis = ctx.metadata.get("analysis", {})
    plans = ctx.metadata.get("sql_plans", [])
    raw = ctx.metadata.get("raw_result")
    tools_used = ctx.metadata.get("tools_used", [])

    sections = [
        "【ComplexFlow · Mock 模式】",
        "",
        f"问题：{ctx.query}",
        f"识别意图：{', '.join(analysis.get('intents', []))}",
        "",
        "查询计划：",
    ]
    for index, plan in enumerate(plans, start=1):
        sections.append(f"{index}. {plan}")

    sections.append("")
    sections.append("执行结果：")

    if isinstance(raw, dict) and raw.get("rows"):
        rows = raw["rows"][:5]
        sections.append(json.dumps(rows, ensure_ascii=False, indent=2))
        sections.append("")
        sections.append(
            f"共 {raw.get('row_count', len(rows))} 行（展示前 {len(rows)} 行）。"
            "完整 LLM 总结将在接入 ComplexReasoningAgent 后启用。"
        )
    elif isinstance(raw, list) and raw:
        preview = [
            {
                "collection": item.get("collection"),
                "external_id": item.get("external_id"),
                "similarity": item.get("similarity"),
            }
            for item in raw[:3]
        ]
        sections.append(json.dumps(preview, ensure_ascii=False, indent=2))
        sections.append("")
        sections.append("SQL 无结果或失败，已降级 semantic_search。")
    elif isinstance(raw, dict) and raw.get("error"):
        sections.append(f"SQL 错误：{raw['error']}")
        sections.append("已尝试语义检索作为降级。")
    else:
        sections.append("暂无结构化结果。")

    sections.append("")
    sections.append("说明：此为复杂流程占位实现；正式版将支持多表 JOIN 与嵌套查询。")

    ctx.set_answer(
        "\n".join(sections),
        tool_name=tools_used[-1] if tools_used else "complex_flow_mock",
        tools_used=list(ctx.metadata.get("tools_trace", [])),
        sql_generated=ctx.metadata.get("sql_generated"),
        usage={"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
        mock=True,
        intents=analysis.get("intents"),
    )
    ctx.metadata["tools_trace"].append("summarize")
    return ctx


complex_flow_workflow = StepWorkflow(
    name="complex_flow",
    steps=[
        step_analyze_query,
        step_plan_queries,
        step_execute_with_fallback,
        step_summarize,
    ],
)
