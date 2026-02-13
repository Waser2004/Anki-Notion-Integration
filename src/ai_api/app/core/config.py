"""Configuration helpers for the AI API service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_ENV_FILE = ".env"


def _load_env_file(env_file: Path) -> None:
    """Load KEY=VALUE pairs from a local .env file if it exists."""
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _read_bool(value: str | None, default: bool) -> bool:
    """Parse a boolean value from env vars using common string forms."""
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings for local/dev execution."""

    app_name: str
    environment: str
    database_url: str
    jwt_secret: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    enable_startup_admin_seed: bool
    startup_admin_email: str
    startup_admin_password: str

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables and optional .env file."""
        env_file = Path(os.getenv("AI_API_ENV_FILE", DEFAULT_ENV_FILE))
        _load_env_file(env_file)

        return cls(
            app_name=os.getenv("AI_API_APP_NAME", "Noteck AI API"),
            environment=os.getenv("AI_API_ENV", "dev"),
            database_url=os.getenv("AI_API_DATABASE_URL", "sqlite:///./noteck_ai_api_dev.db"),
            jwt_secret=os.getenv("AI_API_JWT_SECRET", "dev-change-me"),
            access_token_ttl_seconds=int(os.getenv("AI_API_ACCESS_TOKEN_TTL_SECONDS", "3600")),
            refresh_token_ttl_seconds=int(os.getenv("AI_API_REFRESH_TOKEN_TTL_SECONDS", "604800")),
            enable_startup_admin_seed=_read_bool(
                os.getenv("AI_API_ENABLE_STARTUP_ADMIN_SEED"),
                default=False,
            ),
            startup_admin_email=os.getenv("AI_API_STARTUP_ADMIN_EMAIL", ""),
            startup_admin_password=os.getenv("AI_API_STARTUP_ADMIN_PASSWORD", ""),
        )
