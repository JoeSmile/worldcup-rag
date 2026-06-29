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

    # --- Redis Stack (RediSearch + vector search) ---
    redis_host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_db: int = Field(default=0, validation_alias="REDIS_DB")
    redis_password: str | None = Field(default=None, validation_alias="REDIS_PASSWORD")
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    redis_insight_port: int = Field(default=8001, validation_alias="REDIS_INSIGHT_PORT")
    redis_stack_version: str = Field(default="7.4.0-v1", validation_alias="REDIS_STACK_VERSION")
    redis_vector_index: str = Field(default="worldcup:vectors", validation_alias="REDIS_VECTOR_INDEX")

    # --- Agent / workflow ---
    agent_debug: bool = Field(default=False, validation_alias="AGENT_DEBUG")
    agent_max_iterations: int = Field(default=10, validation_alias="AGENT_MAX_ITERATIONS")
    default_workflow: str = Field(default="simple_qa", validation_alias="DEFAULT_WORKFLOW")
    workflow_auto_route: bool = Field(default=True, validation_alias="WORKFLOW_AUTO_ROUTE")

    # --- Router (small LLM for ambiguous session turns) ---
    router_llm_enabled: bool = Field(default=False, validation_alias="ROUTER_LLM_ENABLED")
    router_model_name: str = Field(default="qwen-turbo", validation_alias="ROUTER_MODEL_NAME")

    # --- Complex flow (SQL replan / summarize; defaults to router model for cost) ---
    complex_flow_model_name: str | None = Field(
        default=None, validation_alias="COMPLEX_FLOW_MODEL_NAME"
    )
    router_confidence_threshold: float = Field(
        default=0.7, validation_alias="ROUTER_CONFIDENCE_THRESHOLD"
    )

    # --- Session memory ---
    memory_ttl_days: int = Field(default=7, validation_alias="MEMORY_TTL_DAYS")
    memory_max_turns: int = Field(default=10, validation_alias="MEMORY_MAX_TURNS")
    memory_max_tokens: int = Field(default=4000, validation_alias="MEMORY_MAX_TOKENS")

    # --- Logging ---
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # --- Observability (LangSmith) ---
    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_api_key: str | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="worldcup-rag", validation_alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str | None = Field(default=None, validation_alias="LANGSMITH_ENDPOINT")

    # --- API server ---
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    benchmark_api_base: str = Field(
        default="http://localhost:8000",
        validation_alias="BENCHMARK_API_BASE",
    )

    @field_validator(
        "agent_debug", "workflow_auto_route", "langsmith_tracing", "router_llm_enabled", mode="before"
    )
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
    def resolved_complex_flow_model_name(self) -> str:
        return self.complex_flow_model_name or self.router_model_name

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

    @property
    def resolved_redis_url(self) -> str:
        if self.redis_url:
            return self.redis_url
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def redis_connection_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": self.redis_host,
            "port": self.redis_port,
            "db": self.redis_db,
            "decode_responses": True,
        }
        if self.redis_password:
            kwargs["password"] = self.redis_password
        return kwargs

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_tracing and bool(self.langsmith_api_key)

    def configure_langsmith(self) -> None:
        """Sync LangSmith env vars for LangChain auto-tracing."""
        import os

        if not self.langsmith_enabled:
            os.environ["LANGSMITH_TRACING"] = "false"
            os.environ.pop("LANGCHAIN_TRACING_V2", None)
            return

        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGSMITH_API_KEY"] = self.langsmith_api_key or ""
        os.environ["LANGSMITH_PROJECT"] = self.langsmith_project
        if self.langsmith_endpoint:
            os.environ["LANGSMITH_ENDPOINT"] = self.langsmith_endpoint

    def langsmith_run_config(
        self,
        run_name: str,
        *,
        trace_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runnable config for LangChain agent/tool invocations."""
        run_metadata: dict[str, Any] = {"workflow": run_name}
        if trace_id:
            run_metadata["trace_id"] = trace_id
        if metadata:
            run_metadata.update(metadata)

        config: dict[str, Any] = {
            "run_name": run_name,
            "tags": ["worldcup-rag", run_name] + (tags or []),
            "metadata": run_metadata,
            "recursion_limit": self.agent_recursion_limit,
        }
        return config


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
settings.configure_langsmith()
