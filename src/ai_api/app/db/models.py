"""Schema setup and low-level queries for the AI API service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import sqlite3

from app.db.session import Database


@dataclass(frozen=True)
class UserRecord:
    """Persisted user data for authentication and profile responses."""

    user_id: str
    email: str
    password_hash: str
    plan: str
    created_at: str


def utc_now_iso() -> str:
    """Return a stable UTC timestamp string for persistence fields."""
    return datetime.now(timezone.utc).isoformat()


def make_user_id(email: str) -> str:
    """Generate a deterministic dev-friendly user ID prefix from an email."""
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
    return f"usr_{digest}"


class AuthRepository:
    """Data access for users and refresh tokens."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def initialize(self) -> None:
        """Create required auth tables and indexes if they do not exist."""
        with self._database.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    plan TEXT NOT NULL DEFAULT 'free',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    rotated_from_hash TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id
                ON refresh_tokens(user_id);
                """
            )

    def create_user(self, email: str, password_hash: str) -> UserRecord:
        """Insert a new user row and return the created user record."""
        record = UserRecord(
            user_id=make_user_id(email),
            email=email,
            password_hash=password_hash,
            plan="free",
            created_at=utc_now_iso(),
        )

        with self._database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (user_id, email, password_hash, plan, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (record.user_id, record.email, record.password_hash, record.plan, record.created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("email already exists") from exc

        return record

    def find_user_by_email(self, email: str) -> UserRecord | None:
        """Return a user record for the given email if it exists."""
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT user_id, email, password_hash, plan, created_at
                FROM users
                WHERE email = ?
                """,
                (email,),
            ).fetchone()

        if row is None:
            return None

        return UserRecord(
            user_id=row["user_id"],
            email=row["email"],
            password_hash=row["password_hash"],
            plan=row["plan"],
            created_at=row["created_at"],
        )

    def find_user_by_id(self, user_id: str) -> UserRecord | None:
        """Return a user record for the given user ID if it exists."""
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT user_id, email, password_hash, plan, created_at
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

        if row is None:
            return None

        return UserRecord(
            user_id=row["user_id"],
            email=row["email"],
            password_hash=row["password_hash"],
            plan=row["plan"],
            created_at=row["created_at"],
        )

    def store_refresh_token(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
        rotated_from_hash: str | None = None,
    ) -> None:
        """Persist a refresh token hash for future token rotation."""
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO refresh_tokens (token_hash, user_id, expires_at, revoked_at, created_at, rotated_from_hash)
                VALUES (?, ?, ?, NULL, ?, ?)
                """,
                (token_hash, user_id, expires_at, utc_now_iso(), rotated_from_hash),
            )

    def find_active_refresh_token(self, token_hash: str) -> dict[str, str] | None:
        """Look up a refresh token row when it is currently active."""
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT token_hash, user_id, expires_at, revoked_at
                FROM refresh_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()

        if row is None:
            return None

        return {
            "token_hash": row["token_hash"],
            "user_id": row["user_id"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
        }

    def revoke_refresh_token(self, token_hash: str) -> None:
        """Mark a refresh token as revoked so it can no longer be used."""
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE refresh_tokens
                SET revoked_at = ?
                WHERE token_hash = ?
                """,
                (utc_now_iso(), token_hash),
            )
