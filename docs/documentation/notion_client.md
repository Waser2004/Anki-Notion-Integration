# Notion Client (`src/Noteck/notion_client.py`)

## Goal
Provide a small, testable wrapper around the Notion REST API that supports the MVP needs of this add-on:

- List all accessible pages and build a parent/child page tree for the Pages UI.
- Fetch a page’s block content recursively so the parser can turn toggles into Anki cards.
- Update toggle blocks so future Anki → Notion sync can write changes back.

- Fetch page bodies as enhanced Markdown for deterministic content change
  detection.

This module intentionally uses only the Python standard library (no extra HTTP dependencies).

## Notion API version

The client sends `Notion-Version: 2026-03-11` by default. The upgrade is
compatible with the endpoints currently used by Noteck: search, page
retrieval, block retrieval, and block-child operations. The 2026-03-11
breaking changes (`after` → `position`, `archived` → `in_trash`, and
`transcription` → `meeting_notes`) do not apply to any request or response
fields used by this client.

## How authentication works

`NotionClient.from_settings(db, profile_name=...)` reads the API token from `SettingsStore` using the `notion_api_key` setting (stored in the OS keychain via `keyring`).

If the token is missing, `NotionApiError` is raised.

## Data model

The client normalizes Notion payloads into a few dataclasses:

- `NotionPage`: minimal page metadata needed by the UI (`page_id`, `title`, `icon`, `parent_id`, `parent_type`, plus `raw`).
- `PageNode`: hierarchical wrapper for tree rendering (`page` + `children`).
- `NotionBlock`: normalized block with nested `children` (the parser can walk this tree).
- `NotionPageSyncData`: page metadata, enhanced Markdown, and shallow roots
  prepared for sync without any Anki or database mutation.
- `NotionPageFetchResult`: either prepared data or the isolated fetch error for
  one page.

The original API payload is always retained as `raw` for future feature growth.

## Public API

### `NotionClient`

#### `NotionClient.from_settings(...)`
- **Use when**: you want a client configured like the add-on (token pulled from settings).
- **Raises**: `NotionApiError` if the token is empty/unset.

#### `list_pages(include_database_pages: bool = False) -> list[NotionPage]`
- Uses the Notion `/search` endpoint and handles pagination.
- Current default behavior excludes database rows whose parent is a database (`parent.type == "database_id"`) or data source (`parent.type == "data_source_id"`), and also excludes pages nested inside those rows (pages whose raw parent is a `block_id` that resolves to a database row), because database navigation is not yet implemented.
- Returns a flat list of normalized pages with titles extracted from `properties[*].type == "title"`.

#### `list_pages_tree(include_database_pages: bool = False) -> list[PageNode]`
- Builds an in-memory tree using only `parent.type == "page_id"` as a parent-child relationship.
- Pages with missing/unknown parents are treated as roots.
- Cycles are guarded against (a repeated page id stops recursion for that branch).

#### `get_page_content(page_id: str) -> list[NotionBlock]`
- Fetches `/blocks/{page_id}/children` and expands every block where
  `has_children == True` through a bounded asynchronous worker queue.
- Uses four workers, a bounded eight-job pending queue, and `page_size=100` for
  every block-children request.
- All workers share one limiter that starts at most three requests per second.
  HTTP 429 responses defer the shared limiter for the server-provided
  `Retry-After` interval and are retried up to five times.
- Returns a tree of `NotionBlock` instances.

#### `get_pages_sync_data(page_ids) -> dict[str, NotionPageFetchResult]`

- Places selected page IDs into a bounded asynchronous queue with four workers
  and waits for completion with `queue.join()`.
- Each worker retrieves page metadata, enhanced Markdown, and paginated shallow
  root blocks for one page.
- The optional progress callback receives completed-page and total-page counts
  after every job, including isolated page failures.
- Workers share the same global request limiter and HTTP 429 retry behavior used
  by recursive block retrieval.
- A failed page receives its own error result instead of canceling preparation
  for the remaining pages.

#### `get_page_markdown(page_id: str) -> NotionMarkdownSnapshot`

- Calls `GET /pages/{page_id}/markdown`.
- Uses the shared request limiter and HTTP 429 retry handling.
- When Notion reports `truncated=true`, requests every `unknown_block_id`
  through the same endpoint and replaces the matching `<unknown>` tag at its
  original position when the subtree returns non-empty Markdown.
- Keeps inaccessible or unsupported subtrees as `<unknown>` tags, records their
  IDs, and returns the attempted snapshot with `truncated=false` so sync can
  continue without switching to the block-tree fallback solely because of
  truncation.
- During advanced-cloze parsing, the Markdown body also recovers table cell,
  row, and column colors that are absent from the block API response.

#### `get_page_blocks_shallow(page_id: str) -> list[NotionBlock]`

- Retrieves only direct page children with `page_size=100`.
- Supplies stable root block IDs and ordering without expanding descendants.
- Uses the same shared request limiter and retry behavior as tree retrieval.

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

The queue behavior follows Notion's official guidance to recursively retrieve
children, request up to 100 results per page, average no more than three requests
per second, and honor `Retry-After` after a 429 response:

- [Retrieve block children](https://developers.notion.com/reference/get-block-children)
- [Pagination](https://developers.notion.com/reference/pagination)
- [Request limits](https://developers.notion.com/reference/request-limits)

### Testing
The client accepts an injectable `transport` callable, which is how unit tests avoid real network calls.

See `tests/test_notion_client.py` for examples of:
- Pagination behavior (`/search`)
- Recursive block fetching (`/blocks/*/children`)
- Toggle update request payloads and ordering

