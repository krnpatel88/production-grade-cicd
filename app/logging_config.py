"""
Logging configuration.

Configures structured, JSON-friendly logging suitable for CloudWatch Logs
ingestion when running in ECS. Log level is configurable via environment
variable so it can be tuned per-environment without code changes.
"""
import logging
import sys

from app.config import get_settings


def configure_logging() -> None:
    """Configure the root logger with a consistent format across the app."""
    settings = get_settings()

    log_format = (
        '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
        '"logger":"%(name)s","message":"%(message)s"}'
    )

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=log_format,
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )

    # Quiet down noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper to fetch a named logger."""
    return logging.getLogger(name)
