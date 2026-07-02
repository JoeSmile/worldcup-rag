"""Gossip workflow — casual chat about football stories, scandals, and fun facts.

Retrieves fact cards via semantic search; composes replies with LLM (fallback: template).
"""

from __future__ import annotations

import re
from typing import Any

from core.config import settings
from tools import get_player_stats, resolve_player_id, semantic_search
from workflows.base import MemoryAwareWorkflow, WorkflowContext
from workflows.gossip_llm import compose_gossip_reply
from workflows.route_keywords import FUN_KEYWORDS, GOSSIP_KEYWORDS

_GOSSIP_EXTERNAL_TOOLS = frozenset({"semantic_search", "player_stats"})


def _external_tools_from_trace(trace: list[str]) -> list[str]:
    """API-facing tools_used — excludes internal step names like classify_topic."""
    seen: list[str] = []
    for name in trace:
        if name in _GOSSIP_EXTERNAL_TOOLS and name not in seen:
            seen.append(name)
    return seen


_IDENTITY_QUERY_RE = re.compile(
    r"(你是谁|你谁啊|你是什么|你是啥|介绍一下你自己|你是哪位|你叫什么|你能做什么|你能干什么|你干什么用的)",
)
_GREETING_QUERY_RE = re.compile(
    r"^(你好|嗨|hello|hi|hey|在吗|在不在|早上好|晚上好|午安|哈喽)[!！。.?？~～]*$",
    re.IGNORECASE,
)
_FOOTBALL_HINT_RE = re.compile(
    r"世界杯|足球|球员|进球|冠军|决赛|小组赛|女足|男足|贝利|梅西|马拉多纳|罗纳尔多|姆巴佩"
)


def _is_identity_query(query: str) -> bool:
    return bool(_IDENTITY_QUERY_RE.search(query.strip()))


def _is_greeting_query(query: str) -> bool:
    return bool(_GREETING_QUERY_RE.match(query.strip()))


def _needs_story_retrieval(ctx: WorkflowContext) -> bool:
    """Skip embedding + pgvector when the query cannot benefit from fact-card search."""
    query = ctx.query.strip()
    if not query:
        ctx.metadata["gossip_fast_path"] = "empty"
        return False
    if _is_identity_query(query):
        mentions = ctx.metadata.get("player_mentions") or []
        if _FOOTBALL_HINT_RE.search(query) or mentions:
            return True
        ctx.metadata["gossip_fast_path"] = "identity"
        return False
    if _is_greeting_query(query):
        ctx.metadata["gossip_fast_path"] = "greeting"
        return False

    analysis = ctx.metadata.get("gossip_analysis") or {}
    topics = analysis.get("topics") or []
    mentions = ctx.metadata.get("player_mentions") or []

    if "gossip" in topics or "fun_fact" in topics or mentions:
        return True
    if _FOOTBALL_HINT_RE.search(query):
        return True

    ctx.metadata["gossip_fast_path"] = "casual_no_hint"
    return False


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
    trace = ctx.metadata.setdefault("tools_trace", [])
    if ctx.metadata.get("studio_enable_semantic_search") is False:
        ctx.metadata["story_hits"] = []
        if "retrieve_skipped" not in trace:
            trace.append("retrieve_skipped")
        return ctx

    if not _needs_story_retrieval(ctx):
        ctx.metadata["story_hits"] = []
        ctx.metadata["tools_trace"].append("retrieve_skipped")
        return ctx

    search_query = f"世界杯 足球 {ctx.query}"
    hits = semantic_search(search_query, limit=5)
    ctx.metadata["story_hits"] = hits
    ctx.metadata["tools_trace"].append("semantic_search")
    return ctx


def step_enrich_player_context(ctx: WorkflowContext) -> WorkflowContext:
    if ctx.metadata.get("studio_enable_player_stats") is False:
        # Thread/checkpoint may still carry player_context from a prior turn — clear it.
        ctx.metadata["player_context"] = []
        return ctx

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
    topics = analysis.get("topics") or []
    hits = ctx.metadata.get("story_hits", [])
    player_ctx = ctx.metadata.get("player_context", [])
    fast_path = ctx.metadata.get("gossip_fast_path")
    trace_id = ctx.metadata.get("trace_id")

    answer, usage, compose_method = compose_gossip_reply(
        ctx.query,
        topics,
        hits,
        player_ctx,
        fast_path=fast_path,
        history=ctx.history,
        memory_recent=ctx.metadata.get("memory_recent"),
        trace_id=trace_id,
    )

    retrieval_ran = "semantic_search" in ctx.metadata.get("tools_trace", [])
    tool_name = "semantic_search" if retrieval_ran else None
    if fast_path in ("identity", "greeting", "casual_no_hint", "empty"):
        tool_name = None

    tools_trace = list(ctx.metadata.get("tools_trace", []))
    tools_used = _external_tools_from_trace(tools_trace)

    ctx.set_answer(
        answer,
        tool_name=tool_name,
        tools_used=tools_used,
        tools_trace=tools_trace,
        sql_generated=None,
        usage=usage,
        model=settings.router_model_name if compose_method == "llm" else None,
        compose_method=compose_method,
        gossip_topics=topics,
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


def apply_gossip_studio_controls(
    ctx: WorkflowContext,
    *,
    enable_semantic_search: bool,
    enable_player_stats: bool,
) -> None:
    """Inject Studio Assistant flags into metadata (Studio-only; production ignores)."""
    ctx.metadata["studio_enable_semantic_search"] = enable_semantic_search
    ctx.metadata["studio_enable_player_stats"] = enable_player_stats


def apply_gossip_studio_skip(step_name: str, ctx: WorkflowContext) -> WorkflowContext:
    """No-op a gossip step with safe defaults so downstream compose still runs."""
    skipped = list(ctx.metadata.get("studio_skipped_steps") or [])
    if step_name not in skipped:
        skipped.append(step_name)
    ctx.metadata["studio_skipped_steps"] = skipped

    if step_name == "step_classify_topic":
        ctx.metadata.setdefault("gossip_analysis", {"topics": ["casual_football"]})
        ctx.metadata.setdefault("player_mentions", [])
        ctx.metadata.setdefault("tools_trace", ["classify_topic"])
    elif step_name == "step_retrieve_stories":
        ctx.metadata.setdefault("story_hits", [])
        trace = list(ctx.metadata.get("tools_trace") or [])
        if "retrieve_skipped" not in trace:
            trace.append("retrieve_skipped")
        ctx.metadata["tools_trace"] = trace
    elif step_name == "step_enrich_player_context":
        ctx.metadata.setdefault("player_context", [])

    return ctx
