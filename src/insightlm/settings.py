"""Typed configuration. One class a reviewer can read top to bottom."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
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
    # `auto` picks the first provider with a key configured, in the order listed in
    # `bootstrap._build_generator`, and falls back to the offline extractive adapter.
    # Naming a provider explicitly makes a missing key an error rather than a silent
    # downgrade -- which is what you want in CI and when running an evaluation, where
    # answering with the wrong engine invalidates the whole run.
    generation_provider: Literal["auto", "anthropic", "gemini", "offline"] = "auto"

    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None

    @field_validator("anthropic_api_key", "gemini_api_key", "voyage_api_key", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: object) -> object:
        """An empty value means "not configured", not "configured as empty".

        The two are easy to conflate and they fail very differently. A commented-out
        line in `.env`, or compose's `${VAR:-}` default, both produce an empty string
        rather than an unset variable -- and an empty string is not None, so the
        provider adapter gets constructed with a blank key and the whole service dies
        on startup instead of quietly using the offline path it was meant to.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Per provider, because the identifiers are not interchangeable.
    generation_model: str = "claude-opus-5"
    gemini_model: str = "gemini-2.5-pro"
    gemini_embedding_model: str = "gemini-embedding-001"
    generation_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # --- retrieval knobs ----------------------------------------------------
    # Exposed as configuration so the ablation is a config sweep rather than a
    # rewrite, which is the whole reason the pipeline sits behind ports.
    chunk_enrich_with_title: bool = True
    # Keep text a reader can't see (white-on-white, 1pt, transparent) out of the index.
    # Off only to measure or demonstrate the undefended baseline.
    exclude_concealed_text: bool = True
    # Rejected candidates are erased (with verification) after this long without activity.
    candidate_retention_days: int = 365

    embedding_cache_path: str | None = None
    """When set, embeddings are cached on disk by content. The evaluation harness sets
    it; the API does not -- see retrieval/adapters/embedding_cache.py for why a
    cross-tenant cache stays out of the request path."""
    rrf_k: int = 60
    """The rank-fusion constant. 60 is the value from the original RRF paper and is
    what nearly everyone uses; it damps the influence of top ranks enough that one
    arm cannot dominate the fusion."""

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
