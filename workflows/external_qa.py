"""External QA workflow — Mode A MCP Gateway lookup + answer composition."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import settings
from core.logger import get_logger, log_extra
from core.mcp_gateway_client import McpGatewayError
from workflows.base import MemoryAwareWorkflow, WorkflowContext
from workflows.external_lookup import format_external_payload_for_llm, run_external_mcp_lookup

logger = get_logger("workflows.external_qa")


def _empty_usage() -> dict[str, int]:
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}


def _template_answer(query: str, payload_text: str) -> str:
    return (
        f"根据外部查询结果回答「{query}」：\n\n"
        f"{payload_text}\n\n"
        "（当前为 MCP Gateway 外部能力；可在 mcp/gateway/.mcp.json 接入 brave-search 获取实时网页结果。）"
    )


def _compose_answer(
    query: str,
    payload_text: str,
    *,
    history: list[dict[str, str]] | None = None,
    memory_recent: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> tuple[str, dict[str, int], str]:
    if not settings.llm_api_key:
        return _template_answer(query, payload_text), _empty_usage(), "template"

    history_lines: list[str] = []
    if history:
        for item in history[-3:]:
            history_lines.append(f"用户：{item.get('user', '')}")
            history_lines.append(f"助手：{item.get('assistant', '')}")
    elif memory_recent:
        for msg in memory_recent[-6:]:
            history_lines.append(f"{msg.get('role', 'user')}：{msg.get('content', '')}")

    user_content = ""
    if history_lines:
        user_content += "【会话上下文】\n" + "\n".join(history_lines) + "\n\n"
    user_content += (
        "用户问题："
        + query
        + "\n\n外部 MCP 查询结果（请据此回答，不要编造库内没有的数字）：\n"
        + payload_text
    )

    llm = ChatOpenAI(
        model=settings.resolved_complex_flow_model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
    )
    run_config = settings.langsmith_run_config("external_qa_compose", trace_id=trace_id)
    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "你是世界杯问答助手。用户问题超出历史知识库时，只能依据提供的外部 MCP 查询结果回答。"
                    "若结果不足，明确说明无法确认，不要编造。"
                )
            ),
            HumanMessage(content=user_content),
        ],
        config=run_config,
    )
    usage_meta = getattr(response, "usage_metadata", None) or {}
    usage = {
        "total_tokens": int(usage_meta.get("total_tokens") or 0),
        "prompt_tokens": int(usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens") or 0),
        "completion_tokens": int(
            usage_meta.get("output_tokens") or usage_meta.get("completion_tokens") or 0
        ),
    }
    return str(response.content), usage, "llm"


def step_mcp_lookup(ctx: WorkflowContext) -> WorkflowContext:
    ctx.metadata["tools_trace"] = ["mcp_gateway"]
    try:
        payload = run_external_mcp_lookup(ctx.query, trace_id=ctx.metadata.get("trace_id"))
    except McpGatewayError as exc:
        logger.warning(
            "external mcp lookup failed",
            extra=log_extra(error=str(exc), trace_id=ctx.metadata.get("trace_id")),
        )
        ctx.error = str(exc)
        return ctx

    ctx.metadata["mcp_payload"] = payload
    trace = payload.get("tool_trace")
    if isinstance(trace, list):
        ctx.metadata["tools_trace"].extend(trace[1:])
    elif isinstance(trace, str):
        ctx.metadata["tools_trace"].append(trace)
    return ctx


def step_compose_answer(ctx: WorkflowContext) -> WorkflowContext:
    payload = ctx.metadata.get("mcp_payload") or {}
    payload_text = format_external_payload_for_llm(payload)
    tools_used = list(ctx.metadata.get("tools_trace") or [])

    answer, usage, method = _compose_answer(
        ctx.query,
        payload_text,
        history=ctx.history,
        memory_recent=ctx.metadata.get("memory_recent"),
        trace_id=ctx.metadata.get("trace_id"),
    )
    ctx.metadata["tools_trace"].append(f"compose:{method}")
    ctx.set_answer(
        answer,
        tool_name=tools_used[-1] if tools_used else "mcp_gateway",
        tools_used=tools_used,
        usage=usage,
        model=settings.resolved_complex_flow_model_name,
        mcp_server=payload.get("server"),
        mcp_tool=payload.get("tool"),
        mcp_gateway_mode=payload.get("mcp_gateway_mode"),
    )
    return ctx


external_qa_workflow = MemoryAwareWorkflow(
    name="external_qa",
    steps=[
        step_mcp_lookup,
        step_compose_answer,
    ],
)
