# Notion Client (`src/Noteck/notion_client.py`)

## Goal
Provide a small, testable wrapper around the Notion REST API that supports the MVP needs of this add-on:

- List all accessible pages and build a parent/child page tree for the Pages UI.
- Fetch a page’s block content recursively so the parser can turn toggles into Anki cards.
- Update toggle blocks so future Anki → Notion sync can write changes back.

This module intentionally uses only the Python standard library (no extra HTTP dependencies).

## How authentication works

`NotionClient.from_settings(db, profile_name=...)` reads the API token from `SettingsStore` using the `notion_api_key` setting (stored in the OS keychain via `keyring`).

If the token is missing, `NotionApiError` is raised.

## Data model

The client normalizes Notion payloads into a few dataclasses:

- `NotionPage`: minimal page metadata needed by the UI (`page_id`, `title`, `icon`, `parent_id`, `parent_type`, plus `raw`).
- `PageNode`: hierarchical wrapper for tree rendering (`page` + `children`).
- `NotionBlock`: normalized block with nested `children` (the parser can walk this tree).

The original API payload is always retained as `raw` for future feature growth.

## Public API

### `NotionClient`

#### `NotionClient.from_settings(...)`
- **Use when**: you want a client configured like the add-on (token pulled from settings).
- **Raises**: `NotionApiError` if the token is empty/unset.

#### `list_pages(include_database_pages: bool = False) -> list[NotionPage]`
- Uses the Notion `/search` endpoint and handles pagination.
- Current default behavior excludes pages whose parent is a database (`parent.type == "database_id"`), because database navigation is not yet implemented.
- Returns a flat list of normalized pages with titles extracted from `properties[*].type == "title"`.

#### `list_pages_tree(include_database_pages: bool = False) -> list[PageNode]`
- Builds an in-memory tree using only `parent.type == "page_id"` as a parent-child relationship.
- Pages with missing/unknown parents are treated as roots.
- Cycles are guarded against (a repeated page id stops recursion for that branch).

#### `get_page_content(page_id: str) -> list[NotionBlock]`
- Fetches `/blocks/{page_id}/children` and recursively expands any blocks where `has_children == True`.
- Returns a tree of `NotionBlock` instances.

#### `update_toggle(block_id: str, title: str, body: str) -> None`
Per the current project decision, this is an **exact match** update strategy:

1. Updates the toggle title via `PATCH /blocks/{block_id}`.
2. Replaces the toggle’s child blocks by:
   - Listing existing children (`GET /blocks/{block_id}/children`)
   - Deleting each child (`DELETE /blocks/{child_id}`)
   - Appending new paragraph blocks to match `body` (`PATCH /blocks/{block_id}/children`)

`body` is converted into paragraph blocks by splitting on blank lines (`"\n\n"`). Formatting (bold/italic, lists, etc.) is not preserved in the current implementation.

## Errors and testing strategy

### Errors
- `NotionApiError`: the Notion API returned an error (HTTP status ≥ 400) or required configuration is missing.
- `NotionTransportError`: the HTTP layer failed before a response was received.

### Testing
The client accepts an injectable `transport` callable, which is how unit tests avoid real network calls.

See `tests/test_notion_client.py` for examples of:
- Pagination behavior (`/search`)
- Recursive block fetching (`/blocks/*/children`)
- Toggle update request payloads and ordering

