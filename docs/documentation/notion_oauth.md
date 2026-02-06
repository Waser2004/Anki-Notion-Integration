# Notion OAuth (`src/anki_notion_integration/notion_oauth.py`)

## Goal
Provide public-integration OAuth for Notion so users authenticate through Notion's standard login/consent flow instead of manually entering API tokens.

## Configuration
Static OAuth configuration lives in `src/anki_notion_integration/notion_oauth_config.py`:

- `NOTION_OAUTH_CLIENT_ID`
- `NOTION_OAUTH_CLIENT_SECRET`
- Redirect URI pieces (`host`, `port`, `path`)

The configured redirect URI must match the URI registered in the Notion integration settings.

## Storage model
`NotionOAuthSessionStore` persists credentials per Anki profile:

- Keyring: access token + refresh token
- SQLite settings table: expiry timestamp + workspace/account metadata

## Core flows
- `login_via_browser(...)`:
  - Builds authorization URL with `response_type=code`, `owner=user`, `redirect_uri`, and `state`
  - Opens browser
  - Waits for callback on local loopback server
  - Exchanges code for tokens via `/oauth/token`
  - Persists token payload

- `refresh_access_token(...)`:
  - Uses refresh token with `/oauth/token` (`grant_type=refresh_token`)
  - Stores latest token payload

- `disconnect_notion(...)`:
  - Revokes access and refresh tokens via `/oauth/revoke`
  - Clears local credentials even if revocation fails

- `ensure_valid_access_token(...)`:
  - Returns current token
  - Refreshes proactively when expiry metadata is close

## Integration points
- Settings UI uses `login_via_browser(...)` and `disconnect_notion(...)` for connect/disconnect buttons.
- `NotionClient.from_settings(...)` uses OAuth session storage, and API requests auto-refresh/retry once on `401`.
