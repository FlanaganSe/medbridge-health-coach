"""Application settings via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration with environment variable binding."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment ---
    environment: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = False

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./health_coach.db"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- LangGraph ---
    langgraph_pool_size: int = 3

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    # --- LLM Provider ---
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    default_model: str = "claude-sonnet-4-6"
    safety_classifier_model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024
    fallback_phi_approved: bool = False

    # --- Scheduling ---
    quiet_hours_start: int = Field(default=21, ge=0, le=23)
    quiet_hours_end: int = Field(default=8, ge=0, le=23)
    default_timezone: str = "America/New_York"
    scheduler_poll_interval_seconds: int = 30
    delivery_poll_interval_seconds: int = 5

    # --- MedBridge Go Integration ---
    medbridge_api_url: str = ""
    medbridge_api_key: SecretStr = SecretStr("")
    medbridge_webhook_secret: str = ""

    # --- App ---
    app_mode: Literal["api", "worker", "all"] = "all"
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("database_url")
    @classmethod
    def normalize_postgres_scheme(cls, v: str) -> str:
        """Normalize database URL to use psycopg async driver."""
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg://", 1)
        return v

    @property
    def is_postgres(self) -> bool:
        """Check if using PostgreSQL."""
        return self.database_url.startswith("postgresql")

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite."""
        return self.database_url.startswith("sqlite")
