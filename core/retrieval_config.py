"""Load vector retrieval tuning from config.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class RetrievalConfig(BaseModel):
    min_similarity: float = 0.7


def _load_yaml_retrieval_section() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    section = data.get("retrieval")
    return section if isinstance(section, dict) else {}


@lru_cache(maxsize=1)
def get_retrieval_config() -> RetrievalConfig:
    return RetrievalConfig.model_validate(_load_yaml_retrieval_section())
