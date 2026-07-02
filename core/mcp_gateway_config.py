"""Load MCP Gateway settings from config.yaml with optional env overrides."""

from __future__ import annotations

import json
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO_ROOT / "config.yaml"

_ENV_FIELD_MAP: dict[str, str] = {
    "MCP_GATEWAY_ENABLED": "enabled",
    "MCP_GATEWAY_URL": "url",
    "MCP_GATEWAY_TRANSPORT": "transport",
    "MCP_GATEWAY_EMBED_PROCESS": "embed_gateway_process",
    "MCP_GATEWAY_COMMAND": "command",
    "MCP_GATEWAY_CONFIG": "config_path",
    "MCP_GATEWAY_RULES": "rules_path",
    "MCP_GATEWAY_DEFAULT_AGENT": "default_agent",
    "MCP_GATEWAY_EXTERNAL_AGENT": "external_agent_id",
    "MCP_GATEWAY_TIMEOUT_MS": "timeout_ms",
    "MCP_GATEWAY_DEV_DIRECT_FALLBACK": "dev_direct_fallback",
}


class McpGatewayConfig(BaseModel):
    enabled: bool = False
    transport: Literal["http", "stdio"] = "http"
    url: str | None = "http://localhost:8080/mcp"
    embed_gateway_process: bool = False
    command: str = "uvx"
    args: list[str] = Field(default_factory=lambda: ["agent-mcp-gateway"])
    config_path: str = "mcp/gateway/.mcp.json"
    rules_path: str = "mcp/gateway/.mcp-gateway-rules.json"
    default_agent: str = "worldcup-external"
    external_agent_id: str = "worldcup-external"
    timeout_ms: int = 30_000
    dev_direct_fallback: bool = True

    def resolved_config_path(self) -> Path:
        path = Path(self.config_path)
        if path.is_absolute():
            return path
        return (_REPO_ROOT / path).resolve()

    def resolved_rules_path(self) -> Path:
        path = Path(self.rules_path)
        if path.is_absolute():
            return path
        return (_REPO_ROOT / path).resolve()

    def resolved_url(self) -> str | None:
        if not self.url:
            return None
        return self.url.strip()

    def gateway_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GATEWAY_MCP_CONFIG"] = str(self.resolved_config_path())
        env["GATEWAY_RULES"] = str(self.resolved_rules_path())
        env["GATEWAY_DEFAULT_AGENT"] = self.default_agent
        if self.transport == "http":
            env["GATEWAY_TRANSPORT"] = "http"
            port = os.environ.get("GATEWAY_PORT", "8080")
            env.setdefault("GATEWAY_PORT", port)
        return env


class AppMcpGatewayConfig(BaseModel):
    mcp_gateway: McpGatewayConfig = Field(default_factory=McpGatewayConfig)


def _load_yaml_section() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return {}
    section = data.get("mcp_gateway")
    return {"mcp_gateway": section or {}}


def _parse_env_value(field_name: str, raw: str) -> Any:
    if field_name in {"enabled", "embed_gateway_process", "dev_direct_fallback"}:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if field_name == "timeout_ms":
        return int(raw)
    if field_name == "args":
        return [part.strip() for part in raw.split(",") if part.strip()]
    return raw


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for env_key, field_name in _ENV_FIELD_MAP.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        overrides[field_name] = _parse_env_value(field_name, raw)
    args_raw = os.environ.get("MCP_GATEWAY_ARGS")
    if args_raw:
        overrides["args"] = _parse_env_value("args", args_raw)
    return overrides


@lru_cache(maxsize=1)
def get_mcp_gateway_config() -> McpGatewayConfig:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env", override=False)
    base = AppMcpGatewayConfig.model_validate(_load_yaml_section())
    overrides = _env_overrides()
    if not overrides:
        return base.mcp_gateway
    return base.mcp_gateway.model_copy(update=overrides)


def ensure_gateway_config_files() -> None:
    """Copy example gateway configs and rewrite paths to absolute repo locations."""
    config = get_mcp_gateway_config()
    config_path = config.resolved_config_path()
    rules_path = config.resolved_rules_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    example_config = _REPO_ROOT / "mcp/gateway/.mcp.json.example"
    if not config_path.is_file() and example_config.is_file():
        data = json.loads(example_config.read_text(encoding="utf-8"))
        live = data.get("mcpServers", {}).get("worldcup-live", {})
        live["args"] = [str((_REPO_ROOT / "mcp/servers/worldcup_live/server.py").resolve())]
        live_env = live.setdefault("env", {})
        live_env["PYTHONPATH"] = str(_REPO_ROOT)
        config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    example_rules = _REPO_ROOT / "mcp/gateway/.mcp-gateway-rules.json.example"
    if not rules_path.is_file() and example_rules.is_file():
        shutil.copyfile(example_rules, rules_path)
