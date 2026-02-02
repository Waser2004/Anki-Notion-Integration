# Plan

Implement the Pages selection feature by adding a small persistence/helper layer (`pages.py`) for the `pages` table and a Qt-based Pages tab (`pages_ui.py`) that displays the user’s Notion pages as an expandable tree with per-page checkboxes that persist selection to the local SQLite DB.

## Scope
- In: Read/write selected page state in the `pages` table (deselect keeps the row and sets `sync_enabled = 0`); compute/update `anki_deck_name` for selected pages using `Notion::<Parent>::<Child>`; render a hierarchical tree view with checkbox selection rules from `docs/user_interface.md`; load pages from `NotionClient.list_pages_tree()` with user-visible errors.
- Out: Sync engine implementation, card parsing/preview counts, database schema changes, OAuth, or adding new dependencies.

## Action items
- [x] Review `docs/user_interface.md`, `docs/documentation/database.md`, and `src/anki_notion_integration/notion_client.py` to confirm required page fields and the exact selection behavior.
- [x] Create `src/anki_notion_integration/pages.py` with a small, well-documented API for the `pages` table (e.g., load selection map, upsert page rows, enable/disable selection, and compute deck names from a page tree).
- [x] Add pure helper functions (no Qt) for the asymmetric parent/child selection rules so the logic can be unit-tested independently of the UI.
- [x] Implement `src/anki_notion_integration/ui/pages_ui.py` as a lazy-loaded tab that renders a hierarchical tree with per-page checkboxes and persists selection via `pages.py`.
- [x] Add Pages-tab load/refresh behavior: fetch the tree via `NotionClient.from_settings(...).list_pages_tree()` and show clear user-visible errors (missing API key, API/network errors).
- [x] Wire checkbox interactions to the documented asymmetric selection rules (including “select parent selects all descendants only when none are selected”), and persist the resulting selection set to the DB.
- [x] Implement deck naming for selected pages as `Notion::<Parent>::<Child>`, and update stored deck names when the page tree changes (rename/move) while keeping changes minimal.
- [x] Add `tests/test_pages.py` to cover `pages.py` behavior using a temporary SQLite DB: selection persistence, recursive selection rule outcomes, and deck-name computation stability.
- [x] Run `python -m unittest discover -s tests` and fix any issues; do a quick manual smoke check in Anki (open Notion window → Pages tab → select/deselect parent/child pages).
- [x] Capture edge cases in code comments and basic UI messaging (rate limits, large workspaces, missing titles, unknown parents/cycles, and network failures).

## Open questions
- None.
