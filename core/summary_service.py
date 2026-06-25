"""LLM-based session history summary compression."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from core.config import settings
from core.logger import get_logger, log_extra
from core.memory import get_session_memory
from core.queue_config import get_queue_config

logger = get_logger("summary_service")

SUMMARY_PROMPT = """你是会话摘要助手。请把「已有摘要」与「新增对话」合并成一段简洁的中文摘要。

要求：
1. 保留用户关心的实体（球员、球队、年份、数字统计）。
2. 去掉寒暄和重复信息。
3. 不超过 400 字。
4. 不要编造未在对话中出现的事实。

【已有摘要】
{existing_summary}

【新增对话】
{conversation}

请直接输出更新后的摘要正文，不要加标题或 markdown 代码块。"""


@lru_cache(maxsize=1)
def _summary_llm() -> ChatOpenAI:
    model_name = get_queue_config().summary.model_name
    return ChatOpenAI(
        model=model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
    )


def _format_conversation(messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = "用户" if role == "user" else "助手"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def maybe_compress_session(session_id: str, workflow: str, trace_id: str | None = None) -> bool:
    """Compress session history into Redis summary when thresholds are met."""
    cfg = get_queue_config()
    if not cfg.summary.enabled:
        return False

    memory = get_session_memory()
    if not memory.available:
        return False

    if not memory.should_compress_summary(
        session_id,
        min_turns=cfg.summary.compress_after_turns,
        token_threshold=cfg.summary.token_threshold,
    ):
        return False

    recent, existing_summary, _ = memory.get_context(session_id, for_workflow=workflow)
    if not recent:
        return False

    keep_count = max(2, cfg.summary.keep_recent_turns * 2)
    if len(recent) <= keep_count:
        return False

    to_summarize = recent[:-keep_count]
    conversation = _format_conversation(to_summarize)
    if not conversation.strip():
        return False

    prompt = SUMMARY_PROMPT.format(
        existing_summary=existing_summary or "（无）",
        conversation=conversation,
    )

    try:
        response = _summary_llm().invoke(prompt)
        new_summary = (response.content or "").strip()
    except Exception as exc:
        logger.warning(
            "summary LLM failed",
            extra=log_extra(session_id=session_id, error=str(exc), trace_id=trace_id),
        )
        return False

    if not new_summary:
        return False

    if not memory.set_summary(session_id, new_summary):
        return False

    archived_count = memory.archive_summarized_messages(session_id, keep_messages=keep_count)
    logger.info(
        "session summary compressed",
        extra=log_extra(
            session_id=session_id,
            workflow=workflow,
            archived_messages=archived_count,
            trace_id=trace_id,
        ),
    )
    return True
