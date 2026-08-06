"""Health check endpoint, used by the Docker HEALTHCHECK and ECS/ALB probes."""
from fastapi import APIRouter

from app.config import get_settings
from app.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Application health check")
def health_check() -> HealthResponse:
    """
    Return the current health status of the application.

    This endpoint is intentionally lightweight (no downstream dependency
    checks) so it can be safely used as an ECS task health check and an
    ALB target group health check without adding load to dependencies.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok Kiran",
        app_name=settings.app_name,
        app_env=settings.app_env,
        app_version=settings.app_version,
    )
