#!/usr/bin/env python3
"""Prometheus exporter for MCP Gateway stack (health + optional gateway /metrics relay)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from wsgiref.simple_server import make_server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY_CONFIG = Path(
    os.environ.get("MCP_GATEWAY_CONFIG", str(_REPO_ROOT / "mcp/gateway/.mcp.json"))
)

GATEWAY_UP = Gauge("worldcup_mcp_gateway_up", "MCP gateway health probe (1=up)")
GATEWAY_PROBE_SECONDS = Gauge(
    "worldcup_mcp_gateway_probe_seconds",
    "Duration of the last MCP gateway health probe",
)
GATEWAY_METRICS_UP = Gauge(
    "worldcup_mcp_gateway_metrics_up",
    "MCP gateway /metrics endpoint reachable (1=up)",
)
CONFIGURED_SERVERS = Gauge(
    "worldcup_mcp_server_configured",
    "Downstream MCP servers listed in gateway config (1=configured)",
    ["server"],
)
PROBE_TOTAL = Counter(
    "worldcup_mcp_probe_total",
    "MCP stack probe attempts",
    ["target", "result"],
)


def _fetch(url: str, *, timeout: float) -> tuple[int, str]:
    request = Request(url, headers={"Accept": "*/*"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body


def _probe_url(url: str, *, timeout: float) -> bool:
    try:
        status, _ = _fetch(url, timeout=timeout)
        return 200 <= status < 400
    except (HTTPError, URLError, TimeoutError, ValueError):
        return False


def _load_configured_servers() -> list[str]:
    if not _GATEWAY_CONFIG.is_file():
        return []
    try:
        data = json.loads(_GATEWAY_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    return sorted(str(name) for name in servers.keys())


def collect_stack_metrics() -> None:
    timeout = float(os.environ.get("MCP_PROBE_TIMEOUT_SECONDS", "3"))
    health_url = os.environ.get("MCP_GATEWAY_HEALTH_URL", "http://mcp-gateway:8080/health")
    metrics_url = os.environ.get("MCP_GATEWAY_METRICS_URL", "http://mcp-gateway:8080/metrics")

    started = time.perf_counter()
    health_ok = _probe_url(health_url, timeout=timeout)
    GATEWAY_PROBE_SECONDS.set(time.perf_counter() - started)
    GATEWAY_UP.set(1 if health_ok else 0)
    PROBE_TOTAL.labels(target="health", result="ok" if health_ok else "fail").inc()

    metrics_ok = _probe_url(metrics_url, timeout=timeout)
    GATEWAY_METRICS_UP.set(1 if metrics_ok else 0)
    PROBE_TOTAL.labels(target="metrics", result="ok" if metrics_ok else "fail").inc()

    configured = _load_configured_servers()
    for name in configured:
        CONFIGURED_SERVERS.labels(server=name).set(1)


def metrics_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    if environ.get("PATH_INFO") not in {"/metrics", "/"}:
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]

    collect_stack_metrics()
    payload = generate_latest()
    start_response("200 OK", [("Content-Type", CONTENT_TYPE_LATEST)])
    return [payload]


def main() -> None:
    host = os.environ.get("MCP_METRICS_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_METRICS_PORT", "8081"))
    interval = float(os.environ.get("MCP_PROBE_INTERVAL_SECONDS", "15"))

    print(f"MCP stack metrics on http://{host}:{port}/metrics (probe every {interval}s)")
    with make_server(host, port, metrics_app) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
