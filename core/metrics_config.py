"""Load Prometheus metrics settings from config.yaml with optional env overrides."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

_ENV_FIELD_MAP: dict[str, str] = {
    "METRICS_ENABLED": "enabled",
    "METRICS_PUBLIC_ENDPOINT": "public_endpoint",
    "METRICS_AUTH_TOKEN": "auth_token",
}


class MetricsConfig(BaseModel):
    enabled: bool = True
    public_endpoint: bool = True
    auth_token: str | None = None


class AppMetricsConfig(BaseModel):
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)


def _load_yaml_section() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return {}
    return {"metrics": data.get("metrics") or {}}


def _parse_env_value(field_name: str, raw: str) -> Any:
    if field_name == "enabled" or field_name == "public_endpoint":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
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
def get_metrics_config() -> AppMetricsConfig:
    base = AppMetricsConfig.model_validate(_load_yaml_section())
    overrides = _env_overrides()
    if not overrides:
        return base
    return AppMetricsConfig(metrics=base.metrics.model_copy(update=overrides))
