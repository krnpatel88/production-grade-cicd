"""
FastAPI application entrypoint.

Wires together configuration, logging, routers, and global exception
handling. Run locally with:

    uvicorn app.main:app --reload
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.routers import health, users

configure_logging()
logger = get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Sample FastAPI service demonstrating a production CI/CD pipeline to AWS ECS.",
)

app.include_router(health.router)
app.include_router(users.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler so unexpected errors return a clean JSON body instead
    of leaking a stack trace, while still logging full details server-side.
    """
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """Redirect-style root response pointing users at the docs."""
    return {"message": f"{settings.app_name} is running. See /docs for API documentation."}
