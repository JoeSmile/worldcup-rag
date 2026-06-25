"""Simple QA workflow: LangChain agent + World Cup tools."""

import json
import logging
from typing import Dict, List

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

from core.config import settings
from prompts import SYSTEM_PROMPT
from tools import (
    execute_sql,
    get_player_stats,
    search_players_by_name,
    semantic_search as search_worldcup_knowledge,
)
from workflows.base import StepWorkflow, Workflow, WorkflowContext

settings.apply_langsmith_env()
logger = logging.getLogger(__name__)


def _to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


@tool
def search_players(name: str) -> str:
    """按名字或中文俗称搜索球员 fact card（贝利、梅西、大罗、C罗等）。"""
    return _to_json(search_players_by_name(name))


@tool
def semantic_search(query: str) -> str:
    """语义搜索世界杯知识库，适合开放式问题和不确定该查什么表的问题。"""
    return _to_json(search_worldcup_knowledge(query))


@tool
def player_stats(name: str) -> str:
    """搜索球员世界杯生涯、进球、出场、奖项；支持中文俗称（贝利、大罗、C罗、梅西等）。"""
    return _to_json(get_player_stats(name))


@tool
def sql_query(sql: str) -> str:
    """执行只读 SQL。仅可查询 vw_player_summary、vw_match_summary、vw_team_tournament_summary 或 documents/document_chunks。"""
    return _to_json(execute_sql(sql))


_llm = ChatOpenAI(
    model=settings.model_name,
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    temperature=0,
)

_agent_executor = create_agent(
    model=_llm,
    tools=[search_players, semantic_search, player_stats, sql_query],
    system_prompt=SYSTEM_PROMPT,
    debug=settings.agent_debug,
)


def _extract_chat_metadata(messages) -> dict:
    tools_used: list[str] = []
    sql_generated = None
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0

    for msg in messages:
        for tc in getattr(msg, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if not name:
                continue
            tools_used.append(name)
            if name == "sql_query":
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                if isinstance(args, dict) and args.get("sql"):
                    sql_generated = args["sql"]

        usage = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
        total_tokens += int(usage.get("total_tokens") or 0)
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)

    return {
        "tool_name": tools_used[-1] if tools_used else None,
        "tools_used": tools_used,
        "sql_generated": sql_generated,
        "usage": {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def step_prepare_messages(ctx: WorkflowContext) -> WorkflowContext:
    messages: List[Dict[str, str]] = []
    if ctx.history:
        for item in ctx.history[-5:]:
            messages.append({"role": "user", "content": item["user"]})
            messages.append({"role": "assistant", "content": item["assistant"]})
    messages.append({"role": "user", "content": ctx.query})
    ctx.messages = messages
    return ctx


def step_invoke_agent(ctx: WorkflowContext) -> WorkflowContext:
    result = _agent_executor.invoke(
        {"messages": ctx.messages},
        config={"recursion_limit": settings.agent_recursion_limit},
    )
    ctx.metadata["agent_messages"] = result["messages"]
    return ctx


def step_finalize_response(ctx: WorkflowContext) -> WorkflowContext:
    agent_messages = ctx.metadata["agent_messages"]
    metadata = _extract_chat_metadata(agent_messages)
    ctx.set_answer(agent_messages[-1].content, **metadata)
    return ctx


simple_qa_workflow = StepWorkflow(
    name="simple_qa",
    steps=[
        step_prepare_messages,
        step_invoke_agent,
        step_finalize_response,
    ],
)


def chat(query: str, history: List[Dict] = None) -> dict:
    """Run Simple QA workflow (benchmark / direct use)."""
    try:
        return simple_qa_workflow.run(query, history=history)
    except Exception as exc:
        logger.exception("simple_qa workflow failed")
        return Workflow._error_response(str(exc))
