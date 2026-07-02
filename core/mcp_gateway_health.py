"""MCP Gateway reachability checks for startup, /ready, and dev scripts."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.mcp_gateway_config import McpGatewayConfig, get_mcp_gateway_config


def gateway_health_url(config: McpGatewayConfig | None = None) -> str | None:
    cfg = config or get_mcp_gateway_config()
    if not cfg.enabled or cfg.embed_gateway_process:
        return None
    url = cfg.resolved_url()
    if not url:
        return None
    base = url.rstrip("/")
    if base.endswith("/mcp"):
        return base[: -len("/mcp")] + "/health"
    return f"{base}/health"


def probe_gateway_http(
    *,
    health_url: str | None = None,
    timeout: float = 2.0,
    config: McpGatewayConfig | None = None,
) -> tuple[bool, str]:
    """Return (reachable, detail)."""
    target = health_url or gateway_health_url(config)
    if not target:
        return True, "not_applicable"

    try:
        request = Request(target, headers={"Accept": "*/*"})
        with urlopen(request, timeout=timeout) as response:
            if 200 <= response.status < 400:
                return True, f"http_{response.status}"
            return False, f"http_{response.status}"
    except HTTPError as exc:
        return False, f"http_{exc.code}"
    except ConnectionRefusedError:
        return False, "connection_refused"
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if "Connection refused" in str(reason):
            return False, "connection_refused"
        return False, str(reason)
    except TimeoutError:
        return False, "timeout"
    except OSError as exc:
        return False, str(exc)


def get_mcp_gateway_status(*, config: McpGatewayConfig | None = None) -> dict[str, Any]:
    cfg = config or get_mcp_gateway_config()
    if not cfg.enabled:
        return {"enabled": False, "status": "disabled"}

    if cfg.embed_gateway_process:
        return {
            "enabled": True,
            "mode": "embed_stdio",
            "status": "configured",
            "detail": "API embeds agent-mcp-gateway via stdio (uvx)",
        }

    health_url = gateway_health_url(cfg)
    reachable, detail = probe_gateway_http(health_url=health_url, config=cfg)
    status: dict[str, Any] = {
        "enabled": True,
        "mode": "remote_http",
        "url": cfg.resolved_url(),
        "health_url": health_url,
        "reachable": reachable,
        "detail": detail,
        "dev_direct_fallback": cfg.dev_direct_fallback,
    }
    if not reachable:
        status["status"] = "down"
        status["alert"] = (
            "MCP Gateway HTTP 不可达（8080 无服务）。"
            "agent-mcp-gateway 当前 Docker 镜像以 stdio 运行，不会监听 8080。"
            "可选：MCP_GATEWAY_EMBED_PROCESS=true + 安装 uv，或依赖 dev_direct_fallback。"
        )
    else:
        status["status"] = "up"
    return status


def startup_alert(*, config: McpGatewayConfig | None = None) -> dict[str, Any] | None:
    """Non-None when operators should see a loud log line at API startup."""
    status = get_mcp_gateway_status(config=config)
    if not status.get("enabled"):
        return None
    if status.get("mode") == "embed_stdio":
        return None
    if status.get("reachable"):
        return None
    return {
        "component": "mcp_gateway",
        "status": status.get("status"),
        "url": status.get("url"),
        "health_url": status.get("health_url"),
        "detail": status.get("detail"),
        "alert": status.get("alert"),
        "dev_direct_fallback": status.get("dev_direct_fallback"),
    }


def format_startup_alert_line(alert: dict[str, Any]) -> str:
    return json.dumps(alert, ensure_ascii=False)
