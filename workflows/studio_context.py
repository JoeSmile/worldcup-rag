"""LangGraph Studio runtime context — editable fields in Manage Assistants."""

from __future__ import annotations

from pydantic import BaseModel, Field

from prompts import SYSTEM_PROMPT


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
