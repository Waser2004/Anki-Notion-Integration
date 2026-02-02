# Plan

Fetch Notion pages in the background and update the Pages tree incrementally so users can see steady progress (and start interacting) while large workspaces are still loading.

## Scope
- In: Add an incremental/streaming page fetch path, update the Pages tab to render and update the tree as pages arrive, show load progress, and handle cancellation/refresh safely without changing dependencies.
- In: Use a progressive full-tree build (no “load children on expand”), and apply “select parent selects descendants” auto-selection immediately as descendants arrive during loading.
- Out: Sync-engine work, broader UI redesign, database schema changes, or new third-party dependencies.

## Action items
- [ ] Audit the current load path (`PagesPage.reload_pages()` → `NotionClient.list_pages_tree()`) and refactor it into a progressive full-tree build that updates the UI continuously as pages arrive.
- [ ] Extend `src/anki_notion_integration/notion_client.py` with an incremental API (e.g., `iter_pages(...)` or a callback-based `list_pages_progress(...)`) that yields pages as `/search` pagination advances, while keeping `list_pages_tree()` intact for compatibility.
- [ ] Add a pure, unit-testable “incremental tree state” helper in `src/anki_notion_integration/pages.py` that can accept pages in any order and emit stable parent/child relationships plus “move/reparent” updates when parents arrive late.
- [ ] Update `src/anki_notion_integration/ui/pages_ui.py` to start the fetch off the UI thread (using Anki’s background facilities or a small worker + Qt polling) and apply incremental UI updates only on the Qt thread.
- [ ] Implement incremental rendering rules: create items as they arrive, temporarily attach unknown-parent pages at root, and re-parent/move items when their parent later arrives; update tooltips/deck names when ancestor paths become known.
- [ ] Add user-visible progress: update the instruction label with “Loaded X pages…” during loading, show a final count on completion, and surface transport/API errors without leaving the tab in a broken intermediate state.
- [ ] Add cancellation and debouncing: cancel/ignore in-flight updates when the user refreshes, switches tabs, or closes the window; ensure late worker callbacks cannot touch destroyed widgets.
- [ ] Batch persistence: apply existing selection state to newly created items immediately, but batch `PagesStore.upsert_page_selection(...)` writes (e.g., every N pages and on completion) to avoid excessive SQLite churn.
- [ ] Update selection behavior during load: allow users to toggle checkboxes immediately, and when a selected parent’s descendants arrive later, auto-select them immediately (preserving the documented asymmetric rules).
- [ ] Extend `tests/test_pages.py` to cover the new incremental-tree helper (out-of-order arrivals, re-parenting correctness, deck-name updates, and selection behavior during/after load) and run `python -m unittest discover -s tests`.

## Open questions
- None
