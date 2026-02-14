"""FastAPI app entrypoint for the Noteck AI API dev milestone."""

from __future__ import annotations

import os
from fastapi import FastAPI

from app.api.v1 import active, auth, static
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.request_id import RequestIdMiddleware
from app.core.security import hash_password, validate_email, validate_password
from app.db.models import AuthRepository
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure a FastAPI app instance."""
    runtime_settings = settings or Settings.from_env()
    database = Database(runtime_settings.database_url)
    auth_repo = AuthRepository(database)

    # create the app
    app = FastAPI(title=runtime_settings.app_name, version="0.1.0")
    app.state.settings = runtime_settings
    app.state.auth_repo = auth_repo

    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.on_event("startup")
    def _initialize_database() -> None:
        """Create schema and seed the startup admin user if configured."""
        # Ensure auth tables exist before any requests.
        auth_repo.initialize()
        _seed_startup_admin(runtime_settings, auth_repo)

    @app.get("/healthz", tags=["internal"])
    def healthz() -> dict[str, str]:
        """Simple health endpoint for dev environment checks."""
        return {"status": "ok"}

    # register API routers
    app.include_router(auth.router, prefix="/v1")
    app.include_router(static.router, prefix="/v1")
    app.include_router(active.router, prefix="/v1")
    return app

def _seed_startup_admin(settings: Settings, auth_repo: AuthRepository) -> None:
    """Ensure a configured admin user exists after database initialization."""
    if not settings.enable_startup_admin_seed:
        return

    email = settings.startup_admin_email.strip().lower()
    password = settings.startup_admin_password
    if not email or not password:
        raise RuntimeError(
            "Startup admin seed enabled but credentials are missing. "
            "Set AI_API_STARTUP_ADMIN_EMAIL and AI_API_STARTUP_ADMIN_PASSWORD."
        )

    normalized_email = validate_email(email)
    validate_password(password)

    if auth_repo.find_user_by_email(normalized_email) is not None:
        return

    auth_repo.create_user(email=normalized_email, password_hash=hash_password(password))

app = create_app()