"""
In-memory data store.

For this sample project, persistence is intentionally out of scope. A
process-local list is sufficient to demonstrate the API contract and CI/CD
pipeline. Swap this out for a real database (e.g. RDS/DynamoDB) in a
production system.
"""
from app.models import User, UserCreate

_USERS: list[User] = [
    User(name="Ada Lovelace", email="ada@example.com"),
    User(name="Alan Turing", email="alan@example.com"),
    User(name="Kiran Patel", email="kiran@example123.com")
]


def list_users() -> list[User]:
    """Return all users currently in the store."""
    return _USERS


def add_user(payload: UserCreate) -> User:
    """Create and persist a new user in the in-memory store."""
    user = User(name=payload.name, email=payload.email)
    _USERS.append(user)
    return user
