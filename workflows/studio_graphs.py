"""LangGraph graph exports for LangSmith Studio (langgraph dev / langgraph up).

Run from repo root:
  langgraph dev -c langgraph.json
  # Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024

All studio graphs expose the same root output fields for LangSmith / evaluators:
  query, answer, workflow, graph, tools_used, tool_name, error
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from core.config import settings
from workflows.base import Workflow, WorkflowContext, WorkflowStep
from workflows.chat_metadata import extract_chat_metadata
from workflows.complex_flow import complex_flow_workflow
from workflows.gossip import (
    apply_gossip_studio_controls,
    apply_gossip_studio_skip,
    gossip_workflow,
    step_classify_topic,
    step_compose_reply,
    step_enrich_player_context,
    step_retrieve_stories,
)
from workflows.simple_qa import build_simple_qa_messages, get_agent_for_prompt
from workflows.studio_context import GossipStudioContext, StudioContext


class StudioGraphState(TypedDict, total=False):
    """Shared state for all LangGraph studio exports (including worldcup_chat)."""

    query: str
    graph: str
    history: list[dict[str, str]]
    messages: list[Any]
    metadata: dict[str, Any]
    answer: str
    workflow: str
    tools_used: list[str]
    tool_name: str | None
    error: str | None


def _canonical_studio_output(
    *,
    query: str,
    answer: str,
    workflow: str,
    tools_used: list[str] | None = None,
    tool_name: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Flat output aligned with agent.chat / POST /chat response."""
    return {
        "query": query,
        "answer": answer or "",
        "workflow": workflow,
        "graph": workflow,
        "tools_used": list(tools_used or []),
        "tool_name": tool_name,
        "error": error,
    }


def _query_from_messages(messages: list[Any]) -> str:
    for msg in reversed(messages):
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if isinstance(msg, dict):
            role = msg.get("role")
        if role in ("human", "user"):
            content = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
            if content:
                return str(content).strip()
    return ""


def _run_worldcup_chat(state: StudioGraphState) -> dict[str, Any]:
    """Same routing as agent.chat / run_langsmith_evaluate.run_agent."""
    from agent import chat

    question = (state.get("query") or "").strip()
    workflow = state.get("graph") or state.get("workflow") or None
    result = chat(
        question,
        workflow=workflow,
        skip_cache=True,
        record_metrics=False,
    )
    routed = result.get("workflow") or workflow or ""
    return _canonical_studio_output(
        query=question,
        answer=result.get("answer") or "",
        workflow=routed,
        tools_used=result.get("tools_used") or [],
        tool_name=result.get("tool_name"),
        error=result.get("error"),
    )


def _build_worldcup_chat_graph():
    builder = StateGraph(StudioGraphState)
    builder.add_node("chat", _run_worldcup_chat)
    builder.set_entry_point("chat")
    builder.add_edge("chat", END)
    return builder.compile()


def _ctx_from_state(state: StudioGraphState) -> WorkflowContext:
    return WorkflowContext(
        query=state.get("query", ""),
        history=state.get("history"),
        messages=list(state.get("messages") or []),
        metadata=dict(state.get("metadata") or {}),
        error=state.get("error"),
    )


def _state_from_ctx(ctx: WorkflowContext, workflow_name: str) -> StudioGraphState:
    tools_used = list(ctx.metadata.get("tools_used") or [])
    return {
        "query": ctx.query,
        "history": ctx.history,
        "messages": ctx.messages,
        "metadata": ctx.metadata,
        "error": ctx.error,
        "answer": ctx.metadata.get("answer"),
        "workflow": workflow_name,
        "graph": workflow_name,
        "tools_used": tools_used,
        "tool_name": ctx.metadata.get("tool_name"),
    }


def _finalize_step_output(state: StudioGraphState, workflow_name: str) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    tools_used = metadata.get("tools_used") or state.get("tools_used") or []
    return _canonical_studio_output(
        query=state.get("query") or "",
        answer=state.get("answer") or metadata.get("answer") or "",
        workflow=workflow_name,
        tools_used=list(tools_used),
        tool_name=metadata.get("tool_name") or state.get("tool_name"),
        error=state.get("error"),
    )


def _normalize_simple_qa_input(state: StudioGraphState) -> dict[str, Any]:
    """Map Studio `query` + optional `history` to agent `messages` (aligned with /chat)."""
    if state.get("messages"):
        return {}
    query = (state.get("query") or "").strip()
    if not query:
        return {}
    metadata = state.get("metadata") or {}
    return {
        "query": query,
        "messages": build_simple_qa_messages(
            query,
            history=state.get("history"),
            memory_recent=metadata.get("memory_recent"),
        ),
    }


def _run_simple_qa_agent(
    state: StudioGraphState,
    runtime: Runtime[StudioContext],
) -> dict[str, Any]:
    messages = state.get("messages") or []
    trace_id = (state.get("metadata") or {}).get("trace_id")
    run_config = settings.langsmith_run_config("simple_qa", trace_id=trace_id)
    prompt = runtime.context.simple_qa_system_prompt
    agent = get_agent_for_prompt(prompt)
    try:
        result = agent.invoke({"messages": messages}, config=run_config)
        return {"messages": result["messages"], "error": None}
    except Exception as exc:
        return {"messages": messages, "error": str(exc)}


def _finalize_simple_qa_output(state: StudioGraphState) -> dict[str, Any]:
    if state.get("error"):
        query = (state.get("query") or "").strip() or _query_from_messages(state.get("messages") or [])
        return _canonical_studio_output(
            query=query,
            answer="",
            workflow="simple_qa",
            error=state.get("error"),
        )

    messages = state.get("messages") or []
    query = (state.get("query") or "").strip() or _query_from_messages(messages)
    answer = ""
    tool_name = None
    tools_used: list[str] = []
    if messages:
        meta = extract_chat_metadata(messages)
        tool_name = meta.get("tool_name")
        tools_used = meta.get("tools_used") or []
        last = messages[-1]
        content = last.content if hasattr(last, "content") else last.get("content", "")
        answer = content if isinstance(content, str) else str(content)
    return _canonical_studio_output(
        query=query,
        answer=answer,
        workflow="simple_qa",
        tools_used=tools_used,
        tool_name=tool_name,
        error=state.get("error"),
    )


def _build_simple_qa_studio_graph():
    builder = StateGraph[StudioGraphState, StudioContext, StudioGraphState, StudioGraphState](StudioGraphState, context_schema=StudioContext)
    builder.add_node("normalize_input", _normalize_simple_qa_input)
    builder.add_node("agent", _run_simple_qa_agent)
    builder.add_node("finalize_output", _finalize_simple_qa_output)
    builder.set_entry_point("normalize_input")
    builder.add_edge("normalize_input", "agent")
    builder.add_edge("agent", "finalize_output")
    builder.add_edge("finalize_output", END)
    return builder.compile()


def _compile_gossip_studio_graph():
    """Gossip graph with Studio Assistant controls (skip steps / disable tools)."""
    workflow_name = gossip_workflow.name
    steps = [
        step_classify_topic,
        step_retrieve_stories,
        step_enrich_player_context,
        step_compose_reply,
    ]
    step_names = [step.__name__ for step in steps]

    builder = StateGraph[
        StudioGraphState,
        GossipStudioContext,
        StudioGraphState,
        StudioGraphState,
    ](StudioGraphState, context_schema=GossipStudioContext)

    for step_fn in steps:
        step_name = step_fn.__name__

        def _run_gossip_step(
            state: StudioGraphState,
            runtime: Runtime[GossipStudioContext],
            step_fn: WorkflowStep = step_fn,
            step_name: str = step_name,
        ) -> StudioGraphState:
            ctx = _ctx_from_state(state)
            studio = runtime.context
            if step_name in studio.skip_steps:
                ctx = apply_gossip_studio_skip(step_name, ctx)
            else:
                apply_gossip_studio_controls(
                    ctx,
                    enable_semantic_search=studio.enable_semantic_search,
                    enable_player_stats=studio.enable_player_stats,
                )
                ctx = step_fn(ctx)
            return _state_from_ctx(ctx, workflow_name)

        builder.add_node(step_name, _run_gossip_step)

    def _finalize(state: StudioGraphState) -> dict[str, Any]:
        return _finalize_step_output(state, workflow_name)

    builder.add_node("finalize_output", _finalize)
    builder.set_entry_point(step_names[0])
    for current, nxt in zip(step_names, step_names[1:]):
        builder.add_edge(current, nxt)
    builder.add_edge(step_names[-1], "finalize_output")
    builder.add_edge("finalize_output", END)
    return builder.compile()


def _compile_step_workflow(workflow: Workflow):
    """Wrap a sequential Workflow as a LangGraph with one node per step."""

    workflow_name = workflow.name
    builder = StateGraph(StudioGraphState)
    step_names: list[str] = []

    for index, step in enumerate(workflow.steps):
        step_name = getattr(step, "__name__", f"step_{index}")

        def _run_step(state: StudioGraphState, step_fn: WorkflowStep = step) -> StudioGraphState:
            ctx = _ctx_from_state(state)
            ctx = step_fn(ctx)
            return _state_from_ctx(ctx, workflow_name)

        builder.add_node(step_name, _run_step)
        step_names.append(step_name)

    if not step_names:
        raise ValueError(f"workflow '{workflow.name}' has no steps")

    def _finalize(state: StudioGraphState) -> dict[str, Any]:
        return _finalize_step_output(state, workflow_name)

    builder.add_node("finalize_output", _finalize)
    builder.set_entry_point(step_names[0])
    for current, nxt in zip(step_names, step_names[1:]):
        builder.add_edge(current, nxt)
    builder.add_edge(step_names[-1], "finalize_output")
    builder.add_edge("finalize_output", END)
    return builder.compile()


# Accepts query or messages — finalize_output ensures output.answer for LangSmith evaluators
simple_qa_graph = _build_simple_qa_studio_graph()

complex_flow_graph = _compile_step_workflow(complex_flow_workflow)
gossip_graph = _compile_gossip_studio_graph()

# Dataset Evaluate: one target reads inputs.query + inputs.graph (like run_agent)
worldcup_chat_graph = _build_worldcup_chat_graph()
