# Plan

Implement `src/anki_notion_integration/notion_client.py` as a small, testable wrapper around the Notion REST API that can (1) fetch all accessible pages and build a parent/child tree for the UI, (2) fetch a page’s block content (recursively) for toggle→card parsing, and (3) update toggle blocks so future Anki→Notion sync can write back changes.

## Scope
- In: Notion API request helper (auth headers, version header, timeouts, pagination); page discovery + hierarchy building; page block retrieval with recursion for nested blocks/toggles; toggle update functionality; unit tests with mocked HTTP transport; minimal inline comments/docstrings describing the Notion API assumptions and limitations.
- Out: UI tree rendering (`pages_ui.py`), parser implementation, sync engine logic, OAuth, or adding/changing third-party dependencies unless explicitly requested.

## Action items
- [x] Inspect existing config surfaces (`SettingsStore`, DB schema) and decide how `notion_client.py` obtains the Notion API token and (if needed) the active Anki profile name.
- [x] Define the public API and data model for Notion results (e.g., `PageNode`/`NotionPage`, `NotionBlock` dataclasses) including the fields the Pages UI will need (id, title, icon, parent id/type, children list).
- [x] Implement a minimal HTTP layer using the Python standard library (request builder, JSON decode/encode, error mapping) with an injectable “transport” so unit tests can run without network access.
- [x] Implement “list all pages” using the Notion `/search` endpoint with pagination, filtering to non-database pages for now, then build an in-memory tree using each page’s `parent` relationship (handle missing parents by treating pages as roots; keep the model extensible so database pages can be supported later).
- [x] Implement “get page content” by fetching `/blocks/{page_id}/children` recursively (respect pagination and `has_children`), returning a normalized block list that preserves toggle blocks and their nested children.
- [x] Implement “update toggle” to sync Anki edits back to Notion by updating the toggle title and making the toggle’s child blocks an exact match of the Anki back field (deterministic replace strategy).
- [x] Capture edge cases/risks in docstrings/tests (rate limiting 429, archived pages, database-owned pages, cycles/unknown parents, and large workspaces).

## Open questions
- None.
