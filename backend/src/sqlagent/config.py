"""Application settings, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Warehouse -------------------------------------------------------------------------
    # Local DuckDB file path, or a MotherDuck URL such as "md:tpcds" (DuckDB reads
    # MOTHERDUCK_TOKEN from the environment itself).
    warehouse_url: str = str(BACKEND_DIR / "data" / "tpcds_sf1.duckdb")
    catalog_path: Path = BACKEND_DIR / "semantic" / "tpcds.catalog.json"
    semantic_path: Path = BACKEND_DIR / "semantic" / "tpcds.yaml"

    # Guardrails for executing generated SQL.
    max_result_rows: int = 500
    query_timeout_s: float = 20.0
    duckdb_threads: int = 4
    duckdb_memory_limit: str = "2GB"

    # --- Agent behaviour -------------------------------------------------------------------
    max_sql_attempts: int = 3
    max_clarifications: int = 2
    retrieval_top_tables: int = 5

    # --- App state (LangGraph checkpoints) ------------------------------------------------
    # Postgres URL (e.g. Neon). Empty -> in-memory checkpointer (local dev only).
    database_url: str = ""

    # --- LLM providers ---------------------------------------------------------------------
    # Ordered fallback chains per role, as comma-separated "provider:model" entries.
    # Providers without credentials are skipped automatically.
    router_models: str = "groq:openai/gpt-oss-20b,cerebras:gpt-oss-120b,ollama:gpt-oss:20b"
    sql_models: str = "groq:openai/gpt-oss-120b,cerebras:gpt-oss-120b,ollama:gpt-oss:20b"

    groq_api_key: str = ""
    cerebras_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_base_url: str = ""  # e.g. http://localhost:11434 ; empty disables Ollama

    # Claude: used for BYO-key requests, and for the owner's key only if explicitly enabled.
    anthropic_api_key: str = ""
    owner_anthropic_enabled: bool = False
    claude_router_model: str = "claude-haiku-5-5"
    claude_sql_model: str = "claude-opus-5-5"

    llm_timeout_s: float = 60.0

    # --- API -------------------------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    rate_limit_per_minute: int = 10  # anonymous requests per IP; BYO-key requests are exempt


@lru_cache
def get_settings() -> Settings:
    return Settings()
