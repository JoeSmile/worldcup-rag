"""Load post-chat queue and summary compression settings from config.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class QueueConfig(BaseModel):
    enabled: bool = True
    stream_key: str = "worldcup-rag:post-chat"
    consumer_group: str = "post-chat-workers"
    defer_cache_write: bool = True
    block_ms: int = 5000
    batch_size: int = 10


class SummaryConfig(BaseModel):
    enabled: bool = True
    compress_after_turns: int = 6
    token_threshold: int = 2500
    keep_recent_turns: int = 4
    model_name: str = "qwen-turbo"


class AppQueueConfig(BaseModel):
    queue: QueueConfig = Field(default_factory=QueueConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)


def _load_yaml_sections() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return {}
    return {
        "queue": data.get("queue") or {},
        "summary": data.get("summary") or {},
    }


@lru_cache(maxsize=1)
def get_queue_config() -> AppQueueConfig:
    sections = _load_yaml_sections()
    return AppQueueConfig.model_validate(sections)
