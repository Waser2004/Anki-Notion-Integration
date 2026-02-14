"""Request ID middleware for request tracing."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to each request and response."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("X-Request-Id", str(uuid4()))
        request_id_var.set(request_id)

        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
