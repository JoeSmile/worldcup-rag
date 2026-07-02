"""LangGraph Studio runtime context — editable fields in Manage Assistants."""

from __future__ import annotations

from pydantic import BaseModel, Field

from prompts import SYSTEM_PROMPT

GOSSIP_STUDIO_STEP_NAMES: tuple[str, ...] = (
    "step_classify_topic",
    "step_retrieve_stories",
    "step_enrich_player_context",
    "step_compose_reply",
)


class StudioContext(BaseModel):
    """Runtime context for studio graphs (not persisted in graph state)."""

    simple_qa_system_prompt: str = Field(
        default=SYSTEM_PROMPT,
        description="System prompt for the simple_qa ReAct agent (Studio: Manage Assistants).",
        json_schema_extra={
            "langgraph_type": "prompt",
            "langgraph_nodes": ["agent"],
        },
    )


class GossipStudioContext(BaseModel):
    """Runtime context for the gossip Studio graph — skip steps or disable tools."""

    skip_steps: list[str] = Field(
        default_factory=list,
        description=(
            "Gossip step nodes to no-op (Studio debug). "
            "Valid: step_classify_topic, step_retrieve_stories, "
            "step_enrich_player_context, step_compose_reply"
        ),
        json_schema_extra={
            "langgraph_nodes": list(GOSSIP_STUDIO_STEP_NAMES),
        },
    )
    enable_semantic_search: bool = Field(
        default=True,
        description="When false, step_retrieve_stories skips pgvector semantic_search.",
        json_schema_extra={"langgraph_nodes": ["step_retrieve_stories"]},
    )
    enable_player_stats: bool = Field(
        default=True,
        description="When false, step_enrich_player_context skips player_stats lookups.",
        json_schema_extra={"langgraph_nodes": ["step_enrich_player_context"]},
    )
