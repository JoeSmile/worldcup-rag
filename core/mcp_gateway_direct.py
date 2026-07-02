"""Direct in-repo MCP server calls when Gateway HTTP/stdio is unavailable (local dev)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from core.mcp_gateway_config import _REPO_ROOT

_SERVER_LOOKUP = {
    ("worldcup-live", "search_live"): _REPO_ROOT / "mcp/servers/worldcup_live/lookup.py",
}


def direct_execute_tool(*, server: str, tool: str, args: dict[str, object]) -> str:
    """Invoke a known in-repo MCP server module without agent-mcp-gateway."""
    key = (server, tool)
    script_path = _SERVER_LOOKUP.get(key)
    if script_path is None or not script_path.is_file():
        raise KeyError(f"no direct fallback for {server}/{tool}")

    spec = importlib.util.spec_from_file_location(
        f"mcp_direct_{server.replace('-', '_')}",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, tool, None)
    if handler is None:
        raise AttributeError(f"{tool} not found in {script_path}")

    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("direct fallback requires args.query")
    return str(handler(query))
