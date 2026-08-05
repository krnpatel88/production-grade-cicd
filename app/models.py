"""Pydantic models (schemas) used across the API."""
from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, EmailStr, Field


class HealthResponse(BaseModel):
    """Response schema for the /health endpoint."""

    status: str = Field(default="ok", examples=["ok"])
    app_name: str
    app_env: str
    app_version: str


class UserCreate(BaseModel):
    """Request schema for creating a user."""

    name: str = Field(min_length=1, max_length=100, examples=["Ada Lovelace"])
    email: EmailStr = Field(examples=["ada@example.com"])


class User(UserCreate):
    """Full user representation, including a generated identifier."""

    id: UUID = Field(default_factory=uuid4)
