"""Per-workflow read policies for shared SessionMemory."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_POLICY_NAME = "simple_qa"


@dataclass(frozen=True)
class MemoryPolicy:
    max_turns: int
    max_tokens: int
    recent_limit: int


WORKFLOW_MEMORY_POLICIES: dict[str, MemoryPolicy] = {
    "simple_qa": MemoryPolicy(max_turns=10, max_tokens=4000, recent_limit=6),
    "complex_flow": MemoryPolicy(max_turns=10, max_tokens=6000, recent_limit=8),
    "gossip": MemoryPolicy(max_turns=15, max_tokens=5000, recent_limit=10),
}


def get_memory_policy(workflow: str | None) -> MemoryPolicy:
    if workflow and workflow in WORKFLOW_MEMORY_POLICIES:
        return WORKFLOW_MEMORY_POLICIES[workflow]
    return WORKFLOW_MEMORY_POLICIES[DEFAULT_POLICY_NAME]
