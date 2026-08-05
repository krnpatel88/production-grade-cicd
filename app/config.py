"""
Application configuration.

Centralizes all environment-driven configuration using pydantic-settings.
This keeps configuration out of code and makes the app 12-factor compliant,
which is required for containerized deployments across dev/test/stage/prod.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, sourced from environment variables or .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "production-grade-cicd"
    app_env: str = "local"  # local | dev | test | stage | prod
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    api_prefix: str = ""
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (avoids re-parsing env on every request)."""
    return Settings()
