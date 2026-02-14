"""Error envelope types and handlers for consistent API errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.request_id import request_id_var


@dataclass(frozen=True)
class ApiError(Exception):
    """Represents an API error returned in the shared error envelope."""

    status_code: int
    code: str
    message: str
    details: dict[str, Any] | None = None


class UnauthorizedError(ApiError):
    """Raised when a request is not authenticated."""

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(status_code=401, code="UNAUTHORIZED", message=message)


class NotImplementedFeatureError(ApiError):
    """Raised by shell endpoints not implemented yet."""

    def __init__(self, feature: str, target_phase: str = "next milestone") -> None:
        super().__init__(
            status_code=501,
            code="NOT_IMPLEMENTED",
            message="Endpoint scaffolded but not implemented",
            details={"feature": feature, "target_phase": target_phase},
        )


def _error_body(error: ApiError) -> dict[str, Any]:
    """Build the standard API error payload."""
    details = error.details if error.details is not None else {}
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": details,
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    """Register exception handlers that emit the standard error envelope."""

    @app.exception_handler(ApiError)
    async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        response = JSONResponse(status_code=exc.status_code, content=_error_body(exc))
        request_id = request_id_var.get()
        if request_id:
            response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Unexpected server error",
                    "details": {},
                }
            },
        )
        request_id = request_id_var.get()
        if request_id:
            response.headers["X-Request-Id"] = request_id
        return response
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        response = JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed",
                    "details": {"errors": exc.errors()},
                }
            },
        )
        request_id = request_id_var.get()
        if request_id:
            response.headers["X-Request-Id"] = request_id
        return response
