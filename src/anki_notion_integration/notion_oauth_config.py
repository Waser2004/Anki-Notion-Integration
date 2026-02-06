"""Static OAuth configuration for the Notion public integration.

Keep these values in a dedicated module so deployment updates are centralized and
so regular users do not need to edit settings to authenticate.
"""

from __future__ import annotations

# Public integration client credentials.
# Replace with the values from your Notion public integration.
NOTION_OAUTH_CLIENT_ID = "2ffd872b-594c-80d8-8209-00376507b675"
NOTION_OAUTH_CLIENT_SECRET = "secret_uSRkvbtNKTNHQFaF5WQS6p9SqCw55FnAk1EwWlrS03z"

# The callback URL must exactly match the redirect URI configured in Notion.
NOTION_OAUTH_REDIRECT_HOST = "localhost"
NOTION_OAUTH_REDIRECT_PORT = 8765
NOTION_OAUTH_REDIRECT_PATH = "/notion/oauth/callback"

# OAuth endpoint base URL for Notion.
NOTION_OAUTH_BASE_URL = "https://api.notion.com/v1"


def redirect_uri() -> str:
    """Return the configured OAuth redirect URI."""
    return (
        f"http://{NOTION_OAUTH_REDIRECT_HOST}:"
        f"{NOTION_OAUTH_REDIRECT_PORT}{NOTION_OAUTH_REDIRECT_PATH}"
    )
