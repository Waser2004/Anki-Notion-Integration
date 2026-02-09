# Plan

Update the Pages tab refresh flow so it renders immediately from persisted `pages` table data, then incrementally reconciles the tree as fresh Notion pages arrive. This removes the blank-loading phase while preserving existing selection rules, deck-name updates, and progressive status feedback.

## Scope
- In: `src/anki_notion_integration/ui/pages_ui.py` refresh lifecycle, DB preload of known pages, incremental merge/update behavior during fetch, parent metadata persistence in the local DB, and tests for the new loading semantics.
- Out: Notion API client behavior, sync engine logic, and non-Pages-tab UI redesign.

## Action items
- [x] Inspect `PagesPage.reload()` and identify clear separation points for “preload from DB” vs “refresh from Notion” phases in `src/anki_notion_integration/ui/pages_ui.py`.
- [x] Extend the `pages` table schema and persistence helpers (`src/anki_notion_integration/db.py`, `src/anki_notion_integration/pages.py`) to store parent metadata (`parent_id`, `parent_type`), using `NULL` parent values for root pages.
- [x] Add a DB-backed preload step that reads stored pages via `PagesStore.get_pages()` and populates `_pages_by_id`/tree items before starting the background fetch.
- [x] Introduce a lightweight derived page model builder for DB rows (id + title inferred from stored deck path + persisted parent metadata), keeping this representation internal to `pages_ui.py`.
- [x] Refactor incremental page handling so incoming Notion pages overwrite/upgrade preloaded placeholders and reparent tree items when real parent data becomes available.
- [x] Adjust reload state transitions and group-box titles so the UI indicates “showing cached pages, refreshing…” instead of presenting an empty tree.
- [x] Preserve existing selection semantics by reapplying `_selected_ids`, cascade intent, and `_persist_selection_state()` across both preload and live-update phases.
- [x] Add/extend tests (new UI-focused tests under `tests/`) to validate: cached pages render immediately, tree is not cleared to blank, incoming pages update titles/parents progressively, and checked states remain stable.
- [x] Remove stale DB-only pages from both UI and DB state immediately after a successful refresh when those page IDs are not returned by Notion.
- [x] Verify edge cases and risks: out-of-order parent/child arrivals, root pages with `NULL` parent metadata, and fetch errors after preload; define expected final UI state for each.
- [x] Run `python -m unittest discover -s tests` and confirm no regressions in `tests/test_pages.py` selection/deck-name behavior (full run blocked locally by missing `keyring`/`aqt`; targeted `tests.test_db_migrations` and `tests.test_pages` pass).
