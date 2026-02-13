"""Security primitives used by auth flows and auth dependencies."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest, new as hmac_new
import json
import re
import secrets

from app.core.config import Settings
from app.core.errors import ApiError, UnauthorizedError


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _b64url_encode(raw: bytes) -> str:
    """Return URL-safe base64 without trailing padding."""
    return urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    """Decode URL-safe base64 with restored padding."""
    padding = "=" * (-len(raw) % 4)
    return urlsafe_b64decode(raw + padding)


def _now_utc() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash."""
    try:
        _, salt_raw, digest_raw = encoded.split("$", 2)
    except ValueError:
        return False

    salt = _b64url_decode(salt_raw)
    expected = _b64url_decode(digest_raw)
    candidate = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return compare_digest(expected, candidate)


def create_access_token(user_id: str, settings: Settings) -> str:
    """Create an HS256-signed JWT-like access token."""
    header = {"alg": "HS256", "typ": "JWT"}
    now_ts = int(_now_utc().timestamp())
    payload = {
        "sub": user_id,
        "iat": now_ts,
        "exp": now_ts + settings.access_token_ttl_seconds,
    }

    encoded_header = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac_new(settings.jwt_secret.encode("utf-8"), signing_input, sha256).digest()
    encoded_signature = _b64url_encode(signature)
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def decode_access_token(token: str, settings: Settings) -> dict[str, int | str]:
    """Validate an access token and return its payload."""
    parts = token.split(".")
    if len(parts) != 3:
        raise UnauthorizedError("Invalid access token")

    encoded_header, encoded_payload, encoded_signature = parts
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    expected_signature = hmac_new(settings.jwt_secret.encode("utf-8"), signing_input, sha256).digest()

    if not compare_digest(_b64url_encode(expected_signature), encoded_signature):
        raise UnauthorizedError("Invalid access token")

    payload_raw = _b64url_decode(encoded_payload)
    payload = json.loads(payload_raw)

    exp = int(payload.get("exp", 0))
    if exp <= int(_now_utc().timestamp()):
        raise UnauthorizedError("Access token expired")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise UnauthorizedError("Invalid access token subject")

    return payload


def issue_refresh_token() -> str:
    """Generate a new opaque refresh token string."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(refresh_token: str) -> str:
    """Hash a refresh token before storing it in the database."""
    return sha256(refresh_token.encode("utf-8")).hexdigest()


def refresh_token_expiry(settings: Settings) -> str:
    """Return an ISO timestamp for refresh token expiration."""
    return (_now_utc() + timedelta(seconds=settings.refresh_token_ttl_seconds)).isoformat()


def validate_email(email: str) -> str:
    """Normalize and validate an email string."""
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.match(normalized):
        raise ApiError(status_code=400, code="INVALID_EMAIL", message="Invalid email format")
    return normalized


def validate_password(password: str) -> None:
    """Apply basic password validation for dev auth flows."""
    if len(password) < 8:
        raise ApiError(
            status_code=400,
            code="WEAK_PASSWORD",
            message="Password must contain at least 8 characters",
        )


def parse_bearer_token(authorization: str | None) -> str:
    """Extract a bearer token from the Authorization header."""
    if authorization is None:
        raise UnauthorizedError()

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise UnauthorizedError("Invalid authorization scheme")

    token = authorization[len(prefix) :].strip()
    if not token:
        raise UnauthorizedError("Missing bearer token")
    return token
