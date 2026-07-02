#!/usr/bin/env python3
"""
Expose agent-mcp-gateway (stdio-only) as Streamable HTTP on :8080.

Upstream agent-mcp-gateway M1 does not listen on HTTP; this bridge spawns the
gateway subprocess and proxies MCP tools over /mcp with GET /health for probes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.mcp_gateway_config import ensure_gateway_config_files, get_mcp_gateway_config  # noqa: E402

logger = logging.getLogger("mcp.gateway.http_bridge")


class _GatewayStdioBackend:
    """Long-lived stdio client to agent-mcp-gateway."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session: Any = None
        self._stdio_cm: Any = None

    async def start(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        cfg = get_mcp_gateway_config()
        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=cfg.gateway_env(),
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        logger.info(
            "stdio gateway ready command=%s args=%s",
            cfg.command,
            cfg.args,
        )

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(None, None, None)
            self._stdio_cm = None

    async def list_tools(self) -> Any:
        async with self._lock:
            assert self._session is not None
            return await self._session.list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any:
        async with self._lock:
            assert self._session is not None
            return await self._session.call_tool(name, arguments or {})


def _build_proxy_server(backend: _GatewayStdioBackend) -> Any:
    from mcp.server.lowlevel.server import Server
    import mcp.types as types

    server = Server("agent-mcp-gateway-http-bridge")

    @server.list_tools()
    async def list_tools(_request: types.ListToolsRequest) -> types.ListToolsResult:
        result = await backend.list_tools()
        return types.ListToolsResult(tools=list(result.tools))

    @server.call_tool()
    async def call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        result = await backend.call_tool(name, arguments)
        blocks: list[types.TextContent | types.ImageContent | types.EmbeddedResource] = []
        for block in result.content or []:
            if isinstance(block, types.TextContent):
                blocks.append(block)
            elif isinstance(block, types.ImageContent):
                blocks.append(block)
            elif isinstance(block, types.EmbeddedResource):
                blocks.append(block)
            else:
                text = getattr(block, "text", None)
                if text:
                    blocks.append(types.TextContent(type="text", text=text))
        if not blocks and getattr(result, "structuredContent", None) is not None:
            blocks.append(
                types.TextContent(
                    type="text",
                    text=json.dumps(result.structuredContent, ensure_ascii=False, default=str),
                )
            )
        return blocks

    return server


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-gateway-http-bridge"})


async def _metrics(_request: Request) -> PlainTextResponse:
    # Minimal stub so Prometheus relay can scrape without failing.
    return PlainTextResponse("# agent-mcp-gateway http bridge (no native metrics)\n")


def create_app() -> Starlette:
    from mcp.server.fastmcp.server import StreamableHTTPASGIApp
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    ensure_gateway_config_files()
    backend = _GatewayStdioBackend()
    proxy = _build_proxy_server(backend)
    session_manager = StreamableHTTPSessionManager(
        app=proxy,
        stateless=True,
        json_response=False,
    )
    mcp_app = StreamableHTTPASGIApp(session_manager)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        async with session_manager.run():
            await backend.start()
            port = os.environ.get("GATEWAY_PORT", "8080")
            logger.info("MCP Gateway HTTP bridge listening on 0.0.0.0:%s (/mcp)", port)
            yield
        await backend.stop()

    return Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),
            Route("/metrics", _metrics, methods=["GET"]),
            Route("/mcp", mcp_app, methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("GATEWAY_PORT", "8080"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
