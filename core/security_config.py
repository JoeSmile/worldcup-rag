"""Load security pipeline settings from config.yaml with optional env overrides."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

# Env overrides: SECURITY_ENABLED=true, SECURITY_ENTROPY_THRESHOLD=4.5, etc.
_ENV_FIELD_MAP: dict[str, str] = {
    "SECURITY_ENABLED": "enabled",
    "SECURITY_SANITIZE_INPUT": "sanitize_input",
    "SECURITY_SCAN_OUTPUT": "scan_output",
    "SECURITY_SCAN_SQL_GENERATED": "scan_sql_generated",
    "SECURITY_REDACT_OUTPUT_ON_ISSUE": "redact_output_on_issue",
    "SECURITY_BLOCK_SQL_INJECTION_IN_QUERY": "block_sql_injection_in_query",
    "SECURITY_AUDIT_LOG": "audit_log",
    "SECURITY_REDACT_PLACEHOLDER": "redact_placeholder",
    "SECURITY_ENTROPY_SCAN_ENABLED": "entropy_scan_enabled",
    "SECURITY_ENTROPY_MIN_TOKEN_LENGTH": "entropy_min_token_length",
    "SECURITY_ENTROPY_THRESHOLD": "entropy_threshold",
}


class SecurityConfig(BaseModel):
    enabled: bool = True
    sanitize_input: bool = True
    scan_output: bool = True
    scan_sql_generated: bool = True
    redact_output_on_issue: bool = True
    block_sql_injection_in_query: bool = True
    audit_log: bool = True
    redact_placeholder: str = "[REDACTED]"
    entropy_scan_enabled: bool = True
    entropy_min_token_length: int = 24
    entropy_threshold: float = 4.2


class AppSecurityConfig(BaseModel):
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def _load_yaml_section() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return {}
    section = data.get("security")
    return {"security": section or {}}


def _parse_env_value(field_name: str, raw: str) -> Any:
    if field_name in {
        "enabled",
        "sanitize_input",
        "scan_output",
        "scan_sql_generated",
        "redact_output_on_issue",
        "block_sql_injection_in_query",
        "audit_log",
        "entropy_scan_enabled",
    }:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if field_name == "entropy_min_token_length":
        return int(raw)
    if field_name == "entropy_threshold":
        return float(raw)
    return raw


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for env_key, field_name in _ENV_FIELD_MAP.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        overrides[field_name] = _parse_env_value(field_name, raw)
    return overrides


@lru_cache(maxsize=1)
def get_security_config() -> AppSecurityConfig:
    base = AppSecurityConfig.model_validate(_load_yaml_section())
    overrides = _env_overrides()
    if not overrides:
        return base
    return AppSecurityConfig(security=base.security.model_copy(update=overrides))
