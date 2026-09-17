"""Typed configuration. One class a reviewer can read top to bottom."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Literal["local", "ci", "production"] = "local"
    debug: bool = False

    # --- database -----------------------------------------------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://insightlm:insightlm@localhost:5432/insightlm")
    )
    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_echo: bool = False

    # The application connects as a restricted role, never as the owner or a
    # superuser. This is not belt-and-braces: a SUPERUSER bypasses row-level security
    # unconditionally -- `FORCE ROW LEVEL SECURITY` does not constrain it -- so
    # connecting as one would make every policy in this system decorative.
    #
    # `database_url` is the privileged DSN and is used only by Alembic. Runtime traffic
    # goes through `app_database_url`.
    database_app_role: str = "insightlm_app"
    database_app_password: SecretStr = SecretStr("insightlm_app")

    # --- redis / queue ------------------------------------------------------
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    # --- object storage -----------------------------------------------------
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "insightlm"
    s3_access_key: SecretStr = SecretStr("minioadmin")
    s3_secret_key: SecretStr = SecretStr("minioadmin")

    # --- auth ---------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-change-me")
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_seconds: int = 900  # 15 minutes
    invitation_ttl_seconds: int = 604_800  # 7 days

    # --- models -------------------------------------------------------------
    # Absent keys are not an error. The system falls back to deterministic offline
    # adapters so that `docker compose up`, CI and a demo all work without an
    # account -- and so a dead key can never block a viva.
    anthropic_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None
    generation_model: str = "claude-opus-5"
    generation_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # --- observability ------------------------------------------------------
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    service_name: str = "insightlm"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    @property
    def app_database_url(self) -> str:
        """Runtime DSN: same database, restricted role."""
        url = make_url(str(self.database_url))
        return url.set(
            username=self.database_app_role,
            password=self.database_app_password.get_secret_value(),
        ).render_as_string(hide_password=False)

    @property
    def sync_database_url(self) -> str:
        """Alembic runs synchronously, so it needs the sync driver, not asyncpg."""
        return str(self.database_url).replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
