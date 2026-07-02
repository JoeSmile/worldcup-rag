"""Plan which downstream MCP server/tool to call via agent-mcp-gateway."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.config import settings
from core.logger import get_logger, log_extra
from core.mcp_gateway_client import McpGatewayError

logger = get_logger("workflows.external_gateway_planner")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fence = _JSON_FENCE_RE.search(stripped)
    if fence:
        stripped = fence.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_gateway_json(text: str) -> Any:
    parsed = _maybe_parse_json(text)
    if parsed is not None:
        return parsed
    return _extract_json_object(text)


def _maybe_parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def normalize_servers(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        servers = payload.get("servers")
        if isinstance(servers, list):
            return [item for item in servers if isinstance(item, dict)]
    return []


def normalize_tools(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        tools = payload.get("tools")
        if isinstance(tools, list):
            return [item for item in tools if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _heuristic_pick_server(query: str, servers: list[dict[str, Any]]) -> str:
    if not servers:
        raise McpGatewayError("gateway list_servers returned no servers")
    lowered = query.lower()
    for item in servers:
        name = str(item.get("name") or "")
        description = str(item.get("description") or "").lower()
        if "2026" in query and ("live" in name or "live" in description):
            return name
        if any(token in lowered for token in ("搜索", "search", "网页", "web")) and "search" in name:
            return name
    return str(servers[0].get("name") or "")


def _heuristic_pick_tool(query: str, tools: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not tools:
        raise McpGatewayError("gateway get_server_tools returned no tools")
    for item in tools:
        name = str(item.get("name") or "")
        schema = item.get("inputSchema") or {}
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict) and "query" in properties:
            return name, {"query": query.strip()}
    first = tools[0]
    return str(first.get("name") or ""), {"query": query.strip()}


def _llm_pick_server_tool(
    query: str,
    servers: list[dict[str, Any]],
    tools_by_server: dict[str, list[dict[str, Any]]],
    *,
    trace_id: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    catalog = {
        server["name"]: {
            "description": server.get("description"),
            "tools": [
                {
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "inputSchema": tool.get("inputSchema"),
                }
                for tool in tools_by_server.get(str(server.get("name")), [])
            ],
        }
        for server in servers
        if server.get("name")
    }
    llm = ChatOpenAI(
        model=settings.router_model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
    )
    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "你是 MCP Gateway 调度器。根据用户问题和可用 server/tool 列表，"
                    "选择最合适的 server、tool 和 args。只输出 JSON："
                    '{"server":"...","tool":"...","args":{...}}'
                )
            ),
            HumanMessage(
                content=(
                    f"用户问题：{query}\n\n可用能力：\n"
                    f"{json.dumps(catalog, ensure_ascii=False, default=str)}"
                )
            ),
        ],
        config=settings.langsmith_run_config("external_gateway_plan", trace_id=trace_id),
    )
    payload = _extract_json_object(str(response.content))
    if not payload:
        raise McpGatewayError("gateway planner returned invalid JSON")
    server = str(payload.get("server") or "")
    tool = str(payload.get("tool") or "")
    args = payload.get("args")
    if not server or not tool or not isinstance(args, dict):
        raise McpGatewayError("gateway planner JSON missing server/tool/args")
    return server, tool, args


def plan_gateway_invocation(
    query: str,
    servers: list[dict[str, Any]],
    tools_by_server: dict[str, list[dict[str, Any]]],
    *,
    trace_id: str | None = None,
) -> tuple[str, str, dict[str, Any], str]:
    """Return (server, tool, args, method)."""
    if settings.llm_api_key and servers:
        try:
            server, tool, args = _llm_pick_server_tool(
                query,
                servers,
                tools_by_server,
                trace_id=trace_id,
            )
            return server, tool, args, "llm"
        except Exception as exc:
            logger.warning(
                "gateway planner llm failed, using heuristic",
                extra=log_extra(error=str(exc), trace_id=trace_id),
            )

    server = _heuristic_pick_server(query, servers)
    tool, args = _heuristic_pick_tool(query, tools_by_server.get(server, []))
    return server, tool, args, "heuristic"
