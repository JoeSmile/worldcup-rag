#!/usr/bin/env python3
"""stdio MCP server: live / 2026+ World Cup lookup (MCP Gateway downstream)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from lookup import search_live as _search_live

mcp = FastMCP("worldcup-live")


@mcp.tool()
def search_live(query: str) -> str:
    """Search live or future World Cup facts (2026 schedules, hosts, current events)."""
    return _search_live(query)


if __name__ == "__main__":
    mcp.run(transport="stdio")
