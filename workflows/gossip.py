"""Gossip workflow — casual chat about football stories, scandals, and fun facts.

Uses semantic search over fact cards; does not invent private scandals.
Future: dedicated gossip corpus or external news adapter.
"""

from __future__ import annotations

import re
from typing import Any

from tools import get_player_stats, resolve_player_id, semantic_search
from workflows.base import MemoryAwareWorkflow, WorkflowContext
from workflows.route_keywords import FUN_KEYWORDS, GOSSIP_KEYWORDS

def _classify_gossip(query: str) -> dict[str, Any]:
    topics: list[str] = []
    if any(kw in query for kw in GOSSIP_KEYWORDS):
        topics.append("gossip")
    if any(kw in query for kw in FUN_KEYWORDS):
        topics.append("fun_fact")
    if not topics:
        topics.append("casual_football")
    return {"topics": topics}


def _extract_player_mentions(query: str) -> list[str]:
    """Lightweight player mention extraction for optional context enrichment."""
    patterns = [
        r"梅西",
        r"C罗|克里斯蒂亚诺",
        r"贝利|球王贝利",
        r"大罗|罗纳尔多",
        r"马拉多纳",
        r"齐达内",
        r"贝克汉姆",
        r"姆巴佩",
        r"内马尔",
    ]
    found: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, query)
        if match and match.group(0) not in found:
            found.append(match.group(0))
    return found[:3]


def step_classify_topic(ctx: WorkflowContext) -> WorkflowContext:
    ctx.metadata["gossip_analysis"] = _classify_gossip(ctx.query)
    ctx.metadata["player_mentions"] = _extract_player_mentions(ctx.query)
    ctx.metadata["tools_trace"] = ["classify_topic"]
    return ctx


def step_retrieve_stories(ctx: WorkflowContext) -> WorkflowContext:
    # Broad semantic pull — fact cards may contain match anecdotes, awards drama, etc.
    search_query = f"世界杯 足球 {ctx.query}"
    hits = semantic_search(search_query, limit=5)
    ctx.metadata["story_hits"] = hits
    ctx.metadata["tools_trace"].append("semantic_search")
    return ctx


def step_enrich_player_context(ctx: WorkflowContext) -> WorkflowContext:
    snippets: list[dict[str, Any]] = []
    for name in ctx.metadata.get("player_mentions", []):
        player_id = resolve_player_id(name)
        if not player_id:
            continue
        cards = get_player_stats(name, limit=1)
        if cards:
            snippets.append(
                {
                    "mention": name,
                    "player_id": player_id,
                    "preview": (cards[0].get("content") or "")[:280],
                }
            )
    if snippets:
        ctx.metadata["player_context"] = snippets
        ctx.metadata["tools_trace"].append("player_stats")
    return ctx


def step_compose_reply(ctx: WorkflowContext) -> WorkflowContext:
    analysis = ctx.metadata.get("gossip_analysis", {})
    topics = ", ".join(analysis.get("topics", []))
    hits = ctx.metadata.get("story_hits", [])
    player_ctx = ctx.metadata.get("player_context", [])

    lines = [
        "【Gossip · 闲聊模式】",
        "",
        "聊点轻松的——下面是知识库里和您问题相关的公开资料片段，",
        "我不会编造未经证实的绯闻或隐私。",
        "",
    ]

    if player_ctx:
        lines.append("相关球员公开世界杯履历：")
        for item in player_ctx:
            lines.append(f"- **{item['mention']}**：{item['preview'].replace(chr(10), ' ')}")
        lines.append("")

    if hits:
        lines.append("检索到的趣闻 / 故事线索：")
        for index, hit in enumerate(hits[:3], start=1):
            content = (hit.get("content") or "").replace("\n", " ")
            if len(content) > 200:
                content = content[:200] + "…"
            lines.append(
                f"{index}. [{hit.get('collection')}] {hit.get('external_id')} "
                f"(相似度 {hit.get('similarity', 0):.2f})"
            )
            lines.append(f"   {content}")
        lines.append("")
        lines.append(
            "想听更完整的场外故事，可以具体问某届世界杯、某场比赛或某位球员的花边轶事。"
        )
    else:
        lines.append(
            "知识库里暂时没有匹配的八卦素材；当前数据以世界杯赛果、球员生涯统计为主。"
        )
        lines.append("你可以换个更具体的问题，例如「1998 年世界杯有什么经典花絮？」")

    lines.append("")
    lines.append(f"（话题类型：{topics} · Mock 闲聊工作流，后续可接新闻/社交数据源）")

    ctx.set_answer(
        "\n".join(lines),
        tool_name="semantic_search",
        tools_used=list(ctx.metadata.get("tools_trace", [])),
        sql_generated=None,
        usage={"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
        mock=True,
        gossip_topics=analysis.get("topics"),
        story_hit_count=len(hits),
    )
    ctx.metadata["tools_trace"].append("compose_reply")
    return ctx


gossip_workflow = MemoryAwareWorkflow(
    name="gossip",
    steps=[
        step_classify_topic,
        step_retrieve_stories,
        step_enrich_player_context,
        step_compose_reply,
    ],
)
