"""User endpoints: list and create."""
from fastapi import APIRouter, status

from app.logging_config import get_logger
from app.models import User, UserCreate
from app.store import add_user, list_users

router = APIRouter(prefix="/users", tags=["users"])
logger = get_logger(__name__)


@router.get("", response_model=list[User], summary="List all users")
def get_users() -> list[User]:
    """Return the list of sample users currently stored in memory."""
    logger.info("Listing users")
    return list_users()


@router.post(
    "",
    response_model=User,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
)
def create_user(payload: UserCreate) -> User:
    """Create a new user record in the in-memory store."""
    user = add_user(payload)
    logger.info("Created user %s", user.id)
    return user
