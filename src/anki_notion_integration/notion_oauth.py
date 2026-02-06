"""Notion public OAuth helpers used by settings UI and API client calls."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
from typing import Any, Callable
from urllib import parse, request
from urllib.error import HTTPError
import webbrowser

from .db import Database
from .settings import KeyringSecretStore
from . import notion_oauth_config as oauth_config

_DEFAULT_PROFILE_NAME = "default"
_DEFAULT_SERVICE_NAME = "anki_notion_integration"
_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_AUTH_TIMEOUT_SECONDS = 180.0

_SETTING_TOKEN_EXPIRES_AT = "notion_oauth_access_token_expires_at"
_SETTING_WORKSPACE_NAME = "notion_oauth_workspace_name"
_SETTING_WORKSPACE_ID = "notion_oauth_workspace_id"
_SETTING_BOT_ID = "notion_oauth_bot_id"
_SETTING_OWNER_TYPE = "notion_oauth_owner_type"

_SECRET_ACCESS_TOKEN = "notion_oauth_access_token"
_SECRET_REFRESH_TOKEN = "notion_oauth_refresh_token"


class NotionOAuthError(RuntimeError):
    """Raised when OAuth operations fail or required state is missing."""

    def __init__(self, message: str, status: int | None = None, payload: Any | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class NotionOAuthTransportError(RuntimeError):
    """Raised when a network call fails before a valid response is received."""


@dataclass(frozen=True)
class OAuthResponse:
    """HTTP response wrapper used by OAuth transports."""

    status: int
    headers: dict[str, str]
    body: bytes


OAuthTransport = Callable[[str, str, dict[str, str], bytes | None, float], OAuthResponse]


class NotionOAuthSessionStore:
    """Persist OAuth credentials and account metadata per profile."""

    def __init__(
        self,
        db: Database,
        profile_name: str | None = None,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._db = db
        self._profile_name = profile_name or _DEFAULT_PROFILE_NAME
        self._secrets = KeyringSecretStore(service_name=service_name, profile_name=self._profile_name)

    def get_access_token(self) -> str | None:
        """Return the stored OAuth access token."""
        return self._clean(self._secrets.get_secret(_SECRET_ACCESS_TOKEN))

    def get_refresh_token(self) -> str | None:
        """Return the stored OAuth refresh token."""
        return self._clean(self._secrets.get_secret(_SECRET_REFRESH_TOKEN))

    def get_token_expires_at(self) -> str | None:
        """Return the stored UTC expiration timestamp string."""
        return self._clean(self._db.get_setting(_SETTING_TOKEN_EXPIRES_AT))

    def get_workspace_name(self) -> str | None:
        """Return the connected workspace name if available."""
        return self._clean(self._db.get_setting(_SETTING_WORKSPACE_NAME))

    def is_authenticated(self) -> bool:
        """Return whether the session has both access and refresh tokens."""
        return bool(self.get_access_token() and self.get_refresh_token())

    def save_token_payload(self, payload: dict[str, Any]) -> None:
        """Persist OAuth token response fields used by the integration."""
        access_token = self._required_string(payload.get("access_token"), "OAuth access token is missing.")

        # Refresh grant responses should include a refresh token; if omitted, keep the old one.
        refresh_token = self._clean(payload.get("refresh_token")) or self.get_refresh_token()
        if not refresh_token:
            raise NotionOAuthError("OAuth refresh token is missing.")

        self._secrets.set_secret(_SECRET_ACCESS_TOKEN, access_token)
        self._secrets.set_secret(_SECRET_REFRESH_TOKEN, refresh_token)

        expires_in = payload.get("expires_in")
        expires_at_value = self._compute_expires_at(expires_in)
        self._set_optional(_SETTING_TOKEN_EXPIRES_AT, expires_at_value)

        self._set_optional(_SETTING_WORKSPACE_NAME, self._clean(payload.get("workspace_name")))
        self._set_optional(_SETTING_WORKSPACE_ID, self._clean(payload.get("workspace_id")))
        self._set_optional(_SETTING_BOT_ID, self._clean(payload.get("bot_id")))

        owner = payload.get("owner")
        owner_type = owner.get("type") if isinstance(owner, dict) else None
        self._set_optional(_SETTING_OWNER_TYPE, self._clean(owner_type))

    def clear_local_credentials(self) -> None:
        """Delete local OAuth credentials and account metadata."""
        self._secrets.delete_secret(_SECRET_ACCESS_TOKEN)
        self._secrets.delete_secret(_SECRET_REFRESH_TOKEN)
        self._set_optional(_SETTING_TOKEN_EXPIRES_AT, None)
        self._set_optional(_SETTING_WORKSPACE_NAME, None)
        self._set_optional(_SETTING_WORKSPACE_ID, None)
        self._set_optional(_SETTING_BOT_ID, None)
        self._set_optional(_SETTING_OWNER_TYPE, None)

    def auth_status_label(self) -> str:
        """Return a short user-facing status string for the Settings UI."""
        if not self.is_authenticated():
            return "Not connected"

        workspace_name = self.get_workspace_name()
        if workspace_name:
            return f"Connected to {workspace_name}"

        return "Connected"

    @staticmethod
    def _clean(value: Any) -> str | None:
        """Normalize nullable string-like values from storage and payloads."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _set_optional(self, key: str, value: str | None) -> None:
        """Persist nullable DB metadata values using empty string as null marker."""
        self._db.set_setting(key, "" if value is None else value)

    @staticmethod
    def _required_string(value: Any, message: str) -> str:
        """Return a required non-empty string or raise an OAuth error."""
        if value is None:
            raise NotionOAuthError(message)
        text = str(value).strip()
        if not text:
            raise NotionOAuthError(message)
        return text

    @staticmethod
    def _compute_expires_at(expires_in: Any) -> str | None:
        """Compute an ISO-8601 expiry timestamp from `expires_in` seconds."""
        try:
            seconds = int(expires_in)
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        return expires_at.isoformat()


def build_authorization_url(state: str, redirect_uri: str) -> str:
    """Build the Notion OAuth authorization URL for browser login."""
    if not oauth_config.NOTION_OAUTH_CLIENT_ID or oauth_config.NOTION_OAUTH_CLIENT_ID.startswith("REPLACE_"):
        raise NotionOAuthError("Notion OAuth client id is not configured.")

    params = {
        "client_id": oauth_config.NOTION_OAUTH_CLIENT_ID,
        "response_type": "code",
        "owner": "user",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    encoded = parse.urlencode(params)
    return f"https://api.notion.com/v1/oauth/authorize?{encoded}"


def wait_for_authorization_code(
    host: str,
    port: int,
    callback_path: str,
    expected_state: str,
    timeout_seconds: float = _DEFAULT_AUTH_TIMEOUT_SECONDS,
) -> str:
    """Listen on a local callback URL and return the authorization code."""

    class _CallbackHandler(BaseHTTPRequestHandler):
        """Handle one OAuth callback request and capture code or error."""

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            server = self.server
            parsed = parse.urlparse(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            query = parse.parse_qs(parsed.query)
            code = (query.get("code") or [None])[0]
            state = (query.get("state") or [None])[0]
            error = (query.get("error") or [None])[0]
            error_description = (query.get("error_description") or [None])[0]

            if error:
                server.oauth_error = error_description or error
            elif state != expected_state:
                server.oauth_error = "State verification failed."
            elif not code:
                server.oauth_error = "Authorization code is missing from callback."
            else:
                server.oauth_code = code

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Notion connection completed.</h2>"
                b"<p>You can close this tab and return to Anki.</p></body></html>"
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            # Keep callback handling silent in normal app usage and tests.
            _ = (format, args)

    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    try:
        httpd = HTTPServer((host, port), _CallbackHandler)
    except OSError as exc:
        raise NotionOAuthError(
            f"Cannot start OAuth callback server on {host}:{port}. "
            "Close any process using that port and try again."
        ) from exc

    # Attach mutable state on the server instance for the request handler.
    httpd.timeout = 0.5
    httpd.oauth_code = None
    httpd.oauth_error = None

    try:
        while datetime.now(timezone.utc) < deadline:
            httpd.handle_request()

            if httpd.oauth_error:
                raise NotionOAuthError(str(httpd.oauth_error))
            if httpd.oauth_code:
                return str(httpd.oauth_code)

    finally:
        httpd.server_close()

    raise NotionOAuthError("Timed out waiting for Notion login callback.")


def login_via_browser(
    session: NotionOAuthSessionStore,
    transport: OAuthTransport | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    auth_timeout_seconds: float = _DEFAULT_AUTH_TIMEOUT_SECONDS,
) -> None:
    """Run the full browser-based OAuth login flow and persist the resulting tokens."""
    redirect_uri = oauth_config.redirect_uri()
    state = secrets.token_urlsafe(24)
    authorize_url = build_authorization_url(state=state, redirect_uri=redirect_uri)

    # Open the browser before listening so users can immediately approve access.
    if not webbrowser.open(authorize_url):
        raise NotionOAuthError("Failed to open the browser for Notion login.")

    code = wait_for_authorization_code(
        host=oauth_config.NOTION_OAUTH_REDIRECT_HOST,
        port=oauth_config.NOTION_OAUTH_REDIRECT_PORT,
        callback_path=oauth_config.NOTION_OAUTH_REDIRECT_PATH,
        expected_state=state,
        timeout_seconds=auth_timeout_seconds,
    )

    payload = exchange_authorization_code(
        code=code,
        redirect_uri=redirect_uri,
        transport=transport,
        timeout_seconds=timeout_seconds,
    )
    session.save_token_payload(payload)


def exchange_authorization_code(
    code: str,
    redirect_uri: str,
    transport: OAuthTransport | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for access and refresh tokens."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    return _request_oauth_json("/oauth/token", payload, transport=transport, timeout_seconds=timeout_seconds)


def refresh_access_token(
    session: NotionOAuthSessionStore,
    transport: OAuthTransport | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Refresh the OAuth access token and persist the latest token payload."""
    refresh_token = session.get_refresh_token()
    if not refresh_token:
        raise NotionOAuthError("Notion OAuth refresh token is missing. Reconnect your account.")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    token_payload = _request_oauth_json(
        "/oauth/token",
        payload,
        transport=transport,
        timeout_seconds=timeout_seconds,
    )
    session.save_token_payload(token_payload)

    access_token = session.get_access_token()
    if not access_token:
        raise NotionOAuthError("Refreshed access token is missing.")
    return access_token


def ensure_valid_access_token(
    session: NotionOAuthSessionStore,
    transport: OAuthTransport | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Return a usable access token, refreshing it when close to expiry."""
    access_token = session.get_access_token()
    refresh_token = session.get_refresh_token()
    if not access_token or not refresh_token:
        raise NotionOAuthError("Notion account is not connected. Use Settings to connect your account.")

    # Refresh proactively one minute before expiration to avoid sync/page load failures.
    expires_at_text = session.get_token_expires_at()
    if expires_at_text:
        try:
            expires_at = datetime.fromisoformat(expires_at_text)
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now >= expires_at - timedelta(seconds=60):
                return refresh_access_token(session, transport=transport, timeout_seconds=timeout_seconds)
        except ValueError:
            # Invalid timestamps are ignored; 401 retry handling remains as a fallback.
            pass

    return access_token


def disconnect_notion(
    session: NotionOAuthSessionStore,
    transport: OAuthTransport | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Revoke active OAuth tokens and always clear local credentials."""
    errors: list[str] = []
    access_token = session.get_access_token()
    refresh_token = session.get_refresh_token()

    try:
        # Revoke both tokens when available to ensure the full session is invalidated.
        if access_token:
            revoke_token(access_token, transport=transport, timeout_seconds=timeout_seconds)
        if refresh_token and refresh_token != access_token:
            revoke_token(refresh_token, transport=transport, timeout_seconds=timeout_seconds)
    except Exception as exc:
        errors.append(str(exc))
    finally:
        session.clear_local_credentials()

    if errors:
        raise NotionOAuthError(
            "Disconnected locally, but token revocation failed: " + "; ".join(errors)
        )


def revoke_token(
    token: str,
    transport: OAuthTransport | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Revoke one Notion OAuth token via the revoke endpoint."""
    payload = {"token": token}
    _request_oauth_json("/oauth/revoke", payload, transport=transport, timeout_seconds=timeout_seconds)


def _request_oauth_json(
    path: str,
    payload: dict[str, Any],
    transport: OAuthTransport | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Send an OAuth API request with client basic auth and parse JSON."""
    if not oauth_config.NOTION_OAUTH_CLIENT_ID or oauth_config.NOTION_OAUTH_CLIENT_ID.startswith("REPLACE_"):
        raise NotionOAuthError("Notion OAuth client id is not configured.")
    if not oauth_config.NOTION_OAUTH_CLIENT_SECRET or oauth_config.NOTION_OAUTH_CLIENT_SECRET.startswith("REPLACE_"):
        raise NotionOAuthError("Notion OAuth client secret is not configured.")

    auth_pair = f"{oauth_config.NOTION_OAUTH_CLIENT_ID}:{oauth_config.NOTION_OAUTH_CLIENT_SECRET}".encode("utf-8")
    basic_token = base64.b64encode(auth_pair).decode("ascii")

    url = f"{oauth_config.NOTION_OAUTH_BASE_URL}{path}"
    headers = {
        "Authorization": f"Basic {basic_token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(payload).encode("utf-8")

    transport_fn = transport or _default_transport
    response = transport_fn("POST", url, headers, body, timeout_seconds)

    content = response.body.decode("utf-8") if response.body else ""
    data = json.loads(content) if content else {}

    if response.status >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        raise NotionOAuthError(message or f"Notion OAuth error ({response.status})", status=response.status, payload=data)

    if not isinstance(data, dict):
        raise NotionOAuthError("Unexpected OAuth response format.")

    return data


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> OAuthResponse:
    """Perform an HTTP request using urllib and return a normalized response."""
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return OAuthResponse(
                status=response.getcode(),
                headers=dict(response.headers.items()),
                body=response.read(),
            )
    except HTTPError as exc:
        # Preserve HTTP status/payload for upstream error handling.
        return OAuthResponse(
            status=exc.code,
            headers=dict(exc.headers.items()) if exc.headers is not None else {},
            body=exc.read(),
        )
    except Exception as exc:
        raise NotionOAuthTransportError(str(exc)) from exc
