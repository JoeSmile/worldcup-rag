"""Load cache tuning from config.yaml with safe defaults."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class L1CacheConfig(BaseModel):
    maxsize: int = 100
    ttl: int = 60


class L2CacheConfig(BaseModel):
    ttl: int = 86400


class SemanticCacheConfig(BaseModel):
    enabled: bool = True
    threshold: float = 0.88
    ttl: int = 86400
    knn: int = 3
    max_entries: int = 1_000_000
    vector_algorithm: str = "HNSW"


class NullCacheConfig(BaseModel):
    ttl: int = 600
    marker: str = "NULL_RESULT"


class CacheConfig(BaseModel):
    enabled: bool = True
    l1: L1CacheConfig = Field(default_factory=L1CacheConfig)
    l2: L2CacheConfig = Field(default_factory=L2CacheConfig)
    semantic: SemanticCacheConfig = Field(default_factory=SemanticCacheConfig)
    null: NullCacheConfig = Field(default_factory=NullCacheConfig)


def _load_yaml_cache_section() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    section = data.get("cache")
    return section if isinstance(section, dict) else {}


@lru_cache(maxsize=1)
def get_cache_config() -> CacheConfig:
    return CacheConfig.model_validate(_load_yaml_cache_section())
