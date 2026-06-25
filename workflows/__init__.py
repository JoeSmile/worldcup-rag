"""Workflow package (lazy exports — avoid heavy imports at package load)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflows.registry import WorkflowRegistry

__all__ = ["chat", "default_router", "get_workflow", "registry", "route"]


def __getattr__(name: str):
    if name in __all__:
        from workflows import registry as registry_module

        return getattr(registry_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
