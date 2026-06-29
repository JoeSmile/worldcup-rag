"""LLM helpers for gossip workflow reply composition."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from core.config import settings
from core.logger import get_logger, log_extra
from prompts_gossip import GOSSIP_REPLY_PROMPT

logger = get_logger("workflows.gossip_llm")


def _empty_usage() -> dict[str, int]:
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}


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
def _get_gossip_llm(model_name: str):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0.3,
    ), HumanMessage, SystemMessage


def _truncate_hit(hit: dict[str, Any]) -> dict[str, Any]:
    content = (hit.get("content") or "").strip()
    if len(content) > 500:
        content = content[:500] + "…"
    return {
        "collection": hit.get("collection"),
        "external_id": hit.get("external_id"),
        "similarity": hit.get("similarity"),
        "content": content,
    }


def template_compose_reply(
    query: str,
    topics: list[str],
    hits: list[dict[str, Any]],
    player_ctx: list[dict[str, Any]],
    fast_path: str | None = None,
) -> str:
    """Fallback when LLM unavailable or invoke fails."""
    if fast_path == "identity":
        return (
            "我是世界杯足球问答助手，熟悉赛果、球员数据和赛场花絮。"
            "你可以问我例如「梅西世界杯进了几个球」「意大利拿过几次冠军」，"
            "或者「有什么世界杯趣闻花絮」之类轻松话题。"
        )
    if fast_path == "greeting":
        return "你好！想聊世界杯的话随时问我——赛果、球员、趣闻花絮都可以。"
    if fast_path == "casual_no_hint":
        return (
            "我们主要聊世界杯相关的话题。你可以问赛果、球员数据，"
            "或者「有什么世界杯趣闻花絮」之类轻松问题。"
        )

    lines = [
        "聊点轻松的——下面是知识库里和您问题相关的公开资料片段，我不会编造未经证实的绯闻或隐私。",
        "",
    ]
    if player_ctx:
        lines.append("相关球员公开世界杯履历：")
        for item in player_ctx:
            lines.append(f"- {item['mention']}：{item['preview'].replace(chr(10), ' ')}")
        lines.append("")

    if hits:
        lines.append("检索到的趣闻 / 故事线索：")
        for index, hit in enumerate(hits[:3], start=1):
            content = (hit.get("content") or "").replace("\n", " ")
            if len(content) > 200:
                content = content[:200] + "…"
            lines.append(f"{index}. {content}")
        lines.append("")
        lines.append("想听更完整的故事，可以具体问某届世界杯、某场比赛或某位球员的花边轶事。")
    else:
        lines.append("知识库里暂时没有匹配的八卦素材；当前数据以世界杯赛果、球员生涯统计为主。")
        lines.append("你可以换个更具体的问题，例如「1998 年世界杯有什么经典花絮？」")

    return "\n".join(lines)


def compose_gossip_reply(
    query: str,
    topics: list[str],
    hits: list[dict[str, Any]],
    player_ctx: list[dict[str, Any]],
    *,
    fast_path: str | None = None,
    history: list[dict[str, str]] | None = None,
    memory_recent: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> tuple[str, dict[str, int], str]:
    """Return (answer, usage, method) where method is llm|template."""
    if not settings.llm_api_key:
        return (
            template_compose_reply(query, topics, hits, player_ctx, fast_path),
            _empty_usage(),
            "template",
        )

    payload = {
        "question": query,
        "topics": topics,
        "fast_path": fast_path,
        "story_hits": [_truncate_hit(hit) for hit in hits[:5]],
        "player_context": [
            {
                "mention": item.get("mention"),
                "player_id": item.get("player_id"),
                "preview": (item.get("preview") or "")[:280],
            }
            for item in player_ctx
        ],
    }
    history_block = _format_history_block(history, memory_recent)
    user_content = (
        (history_block if history_block else "")
        + "请根据以下上下文回答用户问题。\n\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )

    llm, HumanMessage, SystemMessage = _get_gossip_llm(settings.router_model_name)
    run_config = settings.langsmith_run_config(
        "gossip_compose",
        trace_id=trace_id,
        tags=["gossip", "compose"],
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(content=GOSSIP_REPLY_PROMPT),
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
            "gossip compose llm failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )

    return (
        template_compose_reply(query, topics, hits, player_ctx, fast_path),
        _empty_usage(),
        "template",
    )
