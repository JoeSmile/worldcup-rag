"""Workflow registry and routing."""

from typing import Dict, Optional

from core.config import settings
from workflows.base import Workflow
from workflows.complex_flow import complex_flow_workflow
from workflows.gossip import gossip_workflow
from workflows.router import default_router, route
from workflows.simple_qa import simple_qa_workflow


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: Dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        self._workflows[workflow.name] = workflow

    def get(self, name: str) -> Optional[Workflow]:
        return self._workflows.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._workflows.keys())


registry = WorkflowRegistry()
registry.register(simple_qa_workflow)
registry.register(complex_flow_workflow)
registry.register(gossip_workflow)


def get_workflow(name: Optional[str] = None) -> Workflow:
    workflow_name = name or settings.default_workflow
    workflow = registry.get(workflow_name)
    if workflow is None:
        available = ", ".join(registry.list_names())
        raise ValueError(f"Unknown workflow '{workflow_name}'. Available: {available}")
    return workflow


def chat(
    query: str,
    history: Optional[list] = None,
    workflow: Optional[str] = None,
    auto_route: bool = True,
) -> dict:
    """Dispatch chat to a workflow; auto_route uses rule-based router when workflow is omitted."""
    if workflow is not None:
        return get_workflow(workflow).run(query, history=history)

    if auto_route and settings.workflow_auto_route:
        return default_router.run(query, history=history)

    return get_workflow().run(query, history=history)


__all__ = ["chat", "get_workflow", "registry", "route", "default_router"]
