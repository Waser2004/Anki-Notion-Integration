"""Auth endpoints for registration, login, refresh, and profile."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.dependencies import get_auth_repository, get_current_user, get_settings
from app.core.errors import ApiError, UnauthorizedError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    issue_refresh_token,
    refresh_token_expiry,
    validate_email,
    validate_password,
    verify_password,
)
from app.db.models import AuthRepository, UserRecord


router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    """Request payload for user registration."""

    email: str
    password: str


class RegisterResponse(BaseModel):
    """Response payload for successful user registration."""

    user_id: str


class TokenRequest(BaseModel):
    """Request payload for password login."""

    email: str
    password: str


class RefreshRequest(BaseModel):
    """Request payload for refresh-token rotation."""

    refresh_token: str


class TokenResponse(BaseModel):
    """Response payload for auth token issuance."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class QuotaResponse(BaseModel):
    """Quota payload returned by /auth/me."""

    month_tokens_left: int
    reset_at: str


class MeResponse(BaseModel):
    """Authenticated user profile payload."""

    user_id: str
    email: str
    plan: str
    quota: QuotaResponse


def _next_month_start_iso() -> str:
    """Return the first day of the next month in UTC ISO format."""
    now = datetime.now(timezone.utc)
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return datetime(year, month, 1, tzinfo=timezone.utc).isoformat()


def _token_response(user_id: str, auth_repo: AuthRepository, settings: Settings, rotated_from_hash: str | None = None) -> TokenResponse:
    """Create and persist a new token pair for the given user."""
    access_token = create_access_token(user_id, settings)
    refresh_token = issue_refresh_token()
    refresh_hash = hash_refresh_token(refresh_token)

    auth_repo.store_refresh_token(
        token_hash=refresh_hash,
        user_id=user_id,
        expires_at=refresh_token_expiry(settings),
        rotated_from_hash=rotated_from_hash,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/register", response_model=RegisterResponse)
def register_user(
    request: RegisterRequest,
    auth_repo: AuthRepository = Depends(get_auth_repository),
) -> RegisterResponse:
    """Register a user account using email and password credentials."""
    email = validate_email(request.email)
    validate_password(request.password)

    try:
        user = auth_repo.create_user(email=email, password_hash=hash_password(request.password))
    except ValueError as exc:
        raise ApiError(status_code=409, code="EMAIL_ALREADY_EXISTS", message="Email already exists") from exc

    return RegisterResponse(user_id=user.user_id)


@router.post("/token", response_model=TokenResponse)
def create_token(
    request: TokenRequest,
    auth_repo: AuthRepository = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Exchange valid credentials for an access+refresh token pair."""
    email = validate_email(request.email)
    user = auth_repo.find_user_by_email(email)
    if user is None or not verify_password(request.password, user.password_hash):
        raise ApiError(status_code=401, code="INVALID_CREDENTIALS", message="Invalid credentials")

    return _token_response(user.user_id, auth_repo, settings)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token_pair(
    request: RefreshRequest,
    auth_repo: AuthRepository = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Rotate a refresh token and issue a new token pair."""
    current_hash = hash_refresh_token(request.refresh_token)
    token_record = auth_repo.find_active_refresh_token(current_hash)
    if token_record is None:
        raise UnauthorizedError("Invalid refresh token")

    if token_record["revoked_at"]:
        raise UnauthorizedError("Refresh token is revoked")

    expires_at = datetime.fromisoformat(token_record["expires_at"])
    if expires_at <= datetime.now(timezone.utc):
        raise UnauthorizedError("Refresh token expired")

    auth_repo.revoke_refresh_token(current_hash)
    return _token_response(token_record["user_id"], auth_repo, settings, rotated_from_hash=current_hash)


@router.get("/me", response_model=MeResponse)
def current_user_profile(current_user: UserRecord = Depends(get_current_user)) -> MeResponse:
    """Return user identity details and a dev quota placeholder."""
    # Quota is a fixed dev placeholder in this milestone; real usage tracking comes later.
    quota = QuotaResponse(month_tokens_left=1_000_000, reset_at=_next_month_start_iso())
    return MeResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        plan=current_user.plan,
        quota=quota,
    )
