"""Dependency helpers shared by API routes."""

from __future__ import annotations

from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import decode_access_token, parse_bearer_token
from app.db.models import AuthRepository, UserRecord


def get_settings(request: Request) -> Settings:
    """Return immutable runtime settings from application state."""
    return request.app.state.settings


def get_auth_repository(request: Request) -> AuthRepository:
    """Return the auth repository from application state."""
    return request.app.state.auth_repo


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_repo: AuthRepository = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
) -> UserRecord:
    """Resolve the authenticated user from the bearer access token."""
    token = parse_bearer_token(authorization)
    payload = decode_access_token(token, settings)
    user_id = str(payload["sub"])
    user = auth_repo.find_user_by_id(user_id)
    if user is None:
        raise UnauthorizedError("User not found")

    return user
