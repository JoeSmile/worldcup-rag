"""Long-lived MCP client to a remote agent-mcp-gateway (HTTP) or dev-embedded stdio."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from core.logger import get_logger, log_extra
from core.mcp_gateway_config import (
    McpGatewayConfig,
    ensure_gateway_config_files,
    get_mcp_gateway_config,
)

logger = get_logger("core.mcp_gateway_client")


class McpGatewayError(RuntimeError):
    """Raised when MCP Gateway is unavailable or returns an error."""


def format_gateway_error(exc: BaseException, *, url: str | None = None) -> str:
    """Turn asyncio/TaskGroup/connection errors into actionable messages."""
    if isinstance(exc, McpGatewayError):
        return str(exc)

    nested: list[BaseException] = []
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            nested.extend(_flatten_exceptions(item))
    else:
        nested = _flatten_exceptions(exc)

    for item in nested:
        if isinstance(item, ConnectionRefusedError):
            target = url or "MCP Gateway"
            return (
                f"无法连接 {target}（connection refused）。"
                "请先 ./scripts/dev-up.sh 启动 mcp-gateway，"
                "或安装 uv 后设 MCP_GATEWAY_EMBED_PROCESS=true，"
                "或保持 MCP_GATEWAY_DEV_DIRECT_FALLBACK=true 走本地 stub。"
            )
        if isinstance(item, TimeoutError):
            return f"MCP Gateway 请求超时：{url or 'gateway'}"

    root = nested[0] if nested else exc
    return str(root)


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for item in exc.exceptions:
            out.extend(_flatten_exceptions(item))
        return out
    if exc.__cause__ is not None:
        return _flatten_exceptions(exc.__cause__)
    return [exc]


class McpGatewayClient:
    """Talk to agent-mcp-gateway via MCP Streamable HTTP (prod) or stdio child (dev only)."""

    def __init__(self, config: McpGatewayConfig | None = None) -> None:
        self._config = config or get_mcp_gateway_config()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: Any = None
        self._stdio_cm: Any = None
        self._ready = threading.Event()
        self._start_error: str | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def start(self) -> None:
        """Warm up client. HTTP: no-op. Embedded stdio: spawn gateway child process."""
        if not self._config.enabled:
            logger.info("mcp gateway disabled, skip start")
            return
        ensure_gateway_config_files()
        if not self._config.embed_gateway_process:
            logger.info(
                "mcp gateway remote mode",
                extra=log_extra(url=self._config.url, transport=self._config.transport),
            )
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run_stdio_loop,
                name="mcp-gateway-stdio",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=60):
            raise McpGatewayError("MCP Gateway stdio client startup timed out")
        if self._start_error:
            raise McpGatewayError(self._start_error)

    def shutdown(self) -> None:
        if not self._config.embed_gateway_process:
            return
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._thread = None
            self._loop = None
            self._session = None
            self._stdio_cm = None
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._async_stdio_shutdown(), loop)
            future.result(timeout=10)
        except Exception as exc:
            logger.warning("mcp gateway shutdown error", extra=log_extra(error=str(exc)))
        loop.call_soon_threadsafe(loop.stop)
        if thread:
            thread.join(timeout=5)

    def call_gateway_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if not self._config.enabled:
            raise McpGatewayError("MCP Gateway is disabled")
        payload = arguments or {}
        if self._config.embed_gateway_process:
            return self._call_stdio_tool(name, payload)
        return self._call_http_tool(name, payload)

    def execute_tool(
        self,
        *,
        server: str,
        tool: str,
        args: dict[str, Any],
        agent_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "server": server,
            "tool": tool,
            "args": args,
        }
        resolved_agent = agent_id or self._config.external_agent_id
        if resolved_agent:
            payload["agent_id"] = resolved_agent
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        text = self.call_gateway_tool("execute_tool", payload)
        parsed = _maybe_parse_json(text)
        return {
            "server": server,
            "tool": tool,
            "text": text,
            "parsed": parsed,
        }

    def _call_http_tool(self, name: str, arguments: dict[str, Any]) -> str:
        url = self._config.resolved_url()
        if not url:
            raise McpGatewayError("MCP_GATEWAY_URL is required when embed_gateway_process=false")
        deadline = max(self._config.timeout_ms / 1000.0, 1.0) + 5.0
        try:
            return asyncio.run(self._async_http_call_tool(url, name, arguments, deadline))
        except McpGatewayError:
            raise
        except Exception as exc:
            raise McpGatewayError(format_gateway_error(exc, url=url)) from exc

    async def _async_http_call_tool(
        self,
        url: str,
        name: str,
        arguments: dict[str, Any],
        deadline: float,
    ) -> str:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with asyncio.timeout(deadline):
            async with streamablehttp_client(url) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
                    text = _extract_tool_text(result)
                    if bool(getattr(result, "isError", False)):
                        raise McpGatewayError(text or f"gateway tool {name} failed")
                    return text

    def _call_stdio_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.start()
        assert self._loop is not None
        deadline = max(self._config.timeout_ms / 1000.0, 1.0) + 5.0
        future = asyncio.run_coroutine_threadsafe(
            self._async_stdio_call_tool(name, arguments),
            self._loop,
        )
        try:
            return future.result(timeout=deadline)
        except Exception as exc:
            raise McpGatewayError(str(exc)) from exc

    def _run_stdio_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_stdio_connect())
            self._ready.set()
            loop.run_forever()
        except Exception as exc:
            self._start_error = str(exc)
            logger.exception("mcp gateway stdio client failed to start")
            self._ready.set()
        finally:
            loop.close()

    async def _async_stdio_connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self._config.command,
            args=list(self._config.args),
            env=self._config.gateway_env(),
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        session = ClientSession(read, write)
        self._session = session
        await session.initialize()
        logger.warning(
            "mcp gateway embedded stdio mode — use remote HTTP gateway in production",
            extra=log_extra(command=self._config.command),
        )

    async def _async_stdio_shutdown(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(None, None, None)
            self._stdio_cm = None

    async def _async_stdio_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if self._session is None:
            raise McpGatewayError("MCP Gateway stdio session is not ready")
        result = await self._session.call_tool(name, arguments)
        text = _extract_tool_text(result)
        if bool(getattr(result, "isError", False)):
            raise McpGatewayError(text or f"gateway tool {name} failed")
        return text


def _extract_tool_text(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, default=str)
    return ""


def _maybe_parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


_client: McpGatewayClient | None = None
_client_lock = threading.Lock()


def get_mcp_gateway_client() -> McpGatewayClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = McpGatewayClient()
        return _client


def reset_mcp_gateway_client() -> None:
    """Test helper: shut down and clear the singleton client."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.shutdown()
            _client = None
