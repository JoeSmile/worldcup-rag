"""Centralized application settings (env + .env)."""

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM (chat) ---
    model_name: str = Field(default="qwen3-max", validation_alias="MODEL_NAME")
    api_base: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="API_BASE",
    )
    api_key: str | None = Field(default=None, validation_alias="API_KEY")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")

    # --- Embeddings ---
    embedding_model: str = Field(default="text-embedding-v4", validation_alias="EMBEDDING_MODEL")
    embedding_base_url: str | None = Field(default=None, validation_alias="EMBEDDING_BASE_URL")
    embedding_dimensions: int = Field(default=1024, validation_alias="EMBEDDING_DIMENSIONS")

    # --- PostgreSQL ---
    pg_host: str = Field(default="localhost", validation_alias="PG_HOST")
    pg_port: int = Field(default=5432, validation_alias="PG_PORT")
    pg_database: str = Field(default="memoryos", validation_alias="PG_DATABASE")
    pg_user: str = Field(default="memoryos", validation_alias="PG_USER")
    pg_password: str = Field(default="memoryos", validation_alias="PG_PASSWORD")

    # --- Agent / workflow ---
    agent_debug: bool = Field(default=False, validation_alias="AGENT_DEBUG")
    agent_max_iterations: int = Field(default=10, validation_alias="AGENT_MAX_ITERATIONS")
    default_workflow: str = Field(default="simple_qa", validation_alias="DEFAULT_WORKFLOW")
    workflow_auto_route: bool = Field(default=True, validation_alias="WORKFLOW_AUTO_ROUTE")

    # --- Observability (LangSmith) ---
    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_api_key: str | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")

    # --- API server ---
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    benchmark_api_base: str = Field(
        default="http://localhost:8000",
        validation_alias="BENCHMARK_API_BASE",
    )

    @field_validator("agent_debug", "workflow_auto_route", "langsmith_tracing", mode="before")
    @classmethod
    def _parse_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @property
    def llm_api_key(self) -> str | None:
        return self.api_key or self.openai_api_key

    @property
    def llm_base_url(self) -> str:
        return self.api_base or self.openai_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def resolved_embedding_base_url(self) -> str:
        return self.embedding_base_url or self.openai_base_url or self.llm_base_url

    @property
    def agent_recursion_limit(self) -> int:
        return max(self.agent_max_iterations + 4, 14)

    def pg_connection_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.pg_host,
            "port": self.pg_port,
            "database": self.pg_database,
            "user": self.pg_user,
            "password": self.pg_password,
        }

    def apply_langsmith_env(self) -> None:
        """Disable tracing when LANGSMITH_TRACING=true but no API key is set."""
        import os

        if self.langsmith_tracing and not self.langsmith_api_key:
            os.environ["LANGSMITH_TRACING"] = "false"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
