"""External lookup — article-style gateway flow: list → discover → execute."""

from __future__ import annotations

import json
import re

from core.logger import get_logger, log_extra
from core.mcp_gateway_client import McpGatewayError, get_mcp_gateway_client
from core.mcp_gateway_config import get_mcp_gateway_config
from core.mcp_gateway_direct import direct_execute_tool
from workflows.external_gateway_planner import (
    normalize_servers,
    normalize_tools,
    parse_gateway_json,
    plan_gateway_invocation,
)
from workflows.route_keywords import EXTERNAL_LOOKUP_KEYWORDS

logger = get_logger("workflows.external_lookup")

_FUTURE_WORLD_CUP_RE = re.compile(r"20(2[5-9]|[3-9]\d)")
_WORLD_CUP_TOPIC_HINTS = (
    "世界杯",
    "世足",
    "赛程",
    "主办",
    "冠军",
    "分组",
    "揭幕战",
    "东道主",
)


def needs_external_lookup(query: str) -> bool:
    """True when the query targets live / future facts outside the static corpus."""
    text = query.strip()
    if not text:
        return False

    if any(kw in text for kw in EXTERNAL_LOOKUP_KEYWORDS):
        return True

    if _FUTURE_WORLD_CUP_RE.search(text) and any(hint in text for hint in _WORLD_CUP_TOPIC_HINTS):
        return True

    return False


def _run_direct_fallback(query: str, *, trace_id: str | None = None) -> dict[str, object]:
    config = get_mcp_gateway_config()
    server = "worldcup-live"
    tool = "search_live"
    args = {"query": query.strip()}
    text = direct_execute_tool(server=server, tool=tool, args=args)
    parsed = _maybe_parse_json(text)
    logger.warning(
        "gateway unavailable, using direct in-repo server fallback",
        extra=log_extra(trace_id=trace_id, server=server, tool=tool),
    )
    return {
        "text": text,
        "parsed": parsed,
        "tool_trace": [
            "mcp_gateway",
            "mcp:direct_fallback",
            f"mcp:execute_tool:{server}/{tool}",
        ],
        "server": server,
        "tool": tool,
        "plan_method": "direct_fallback",
        "mcp_gateway_mode": "direct_fallback",
        "gateway_flow": ["direct_fallback"],
    }


def run_external_mcp_lookup(query: str, *, trace_id: str | None = None) -> dict[str, object]:
    """Article flow: list_servers → get_server_tools → execute_tool (no hardcoded server/tool)."""
    config = get_mcp_gateway_config()
    if not config.enabled:
        raise McpGatewayError("MCP Gateway is disabled")

    try:
        return _run_gateway_lookup(query, trace_id=trace_id)
    except McpGatewayError as exc:
        if not config.dev_direct_fallback:
            raise
        logger.warning(
            "external mcp gateway lookup failed",
            extra=log_extra(trace_id=trace_id, error=str(exc)),
        )
        return _run_direct_fallback(query, trace_id=trace_id)


def _run_gateway_lookup(query: str, *, trace_id: str | None = None) -> dict[str, object]:
    config = get_mcp_gateway_config()
    client = get_mcp_gateway_client()
    agent_id = config.external_agent_id
    tools_trace = ["mcp_gateway", "mcp:list_servers"]

    servers_text = client.call_gateway_tool("list_servers", {"agent_id": agent_id})
    servers = normalize_servers(parse_gateway_json(servers_text))
    if not servers:
        raise McpGatewayError("gateway list_servers returned empty result")

    tools_by_server: dict[str, list[dict[str, object]]] = {}
    for server in servers:
        server_name = str(server.get("name") or "")
        if not server_name:
            continue
        tools_trace.append(f"mcp:get_server_tools:{server_name}")
        tools_text = client.call_gateway_tool(
            "get_server_tools",
            {"agent_id": agent_id, "server": server_name},
        )
        tools_by_server[server_name] = normalize_tools(parse_gateway_json(tools_text))

    server, tool, args, plan_method = plan_gateway_invocation(
        query,
        servers,
        tools_by_server,
        trace_id=trace_id,
    )
    tools_trace.append(f"mcp:plan:{plan_method}")
    tools_trace.append(f"mcp:execute_tool:{server}/{tool}")

    logger.info(
        "external mcp lookup planned",
        extra=log_extra(
            trace_id=trace_id,
            server=server,
            tool=tool,
            plan_method=plan_method,
            agent_id=agent_id,
        ),
    )

    result = client.execute_tool(
        server=server,
        tool=tool,
        args=args,
        agent_id=agent_id,
        timeout_ms=config.timeout_ms,
    )
    return {
        "text": result.get("text") or "",
        "parsed": result.get("parsed"),
        "tool_trace": tools_trace,
        "server": server,
        "tool": tool,
        "plan_method": plan_method,
        "mcp_gateway_mode": "gateway",
        "gateway_flow": ["list_servers", "get_server_tools", "execute_tool"],
    }


def _maybe_parse_json(text: str) -> object | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def format_external_payload_for_llm(payload: dict[str, object]) -> str:
    parsed = payload.get("parsed")
    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False, default=str)
    return str(payload.get("text") or "")
