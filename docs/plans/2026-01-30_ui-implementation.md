# Plan

Implement `ui.py` and `ui.json` to add a "Notion" button to Anki's top toolbar and a dedicated add-on window with a top navigation bar whose tabs are driven by `ui.json`. The window acts as a shell: it routes each tab to a specialized UI module (e.g., `settings_ui.py`, `pages_ui.py`) to render the content area.

## Scope
- In: Define `ui.json` structure and validation; implement an Anki-safe UI bootstrap (no-op outside Anki); add toolbar action; implement the add-on window + top nav; implement dynamic page loading with graceful fallbacks when page modules are missing; add unit tests for schema loading/validation.
- Out: Implementing the actual page contents (`settings_ui.py`, `pages_ui.py`), Notion API calls, or sync behavior.

## Action items
- [x] Create a new feature branch for the UI work (keep commits scoped to `ui.py`/`ui.json` + minimal wiring/tests).
- [x] Inspect Anki hook points available in this repo's runtime (decide between `gui_hooks.main_window_did_init`, `profile_did_open`, etc.) and choose where to register the toolbar action.
- [x] Design `src/anki_notion_integration/docs/ui.json` schema (at minimum: `pages` list with stable `key`, display `name`, and a target `module` plus optional `factory` function name); document expected schema invariants in code.
- [x] Standardize the page-module contract: each page module exposes a factory function (default name: `build_page`) that returns a `QWidget` for the content area, accepting `(parent, context)` so the shell can pass shared dependencies.
- [x] Implement `src/anki_notion_integration/ui/ui.py` to load/validate `ui.json`, register the toolbar `QAction`, open a single-instance window with a top nav + swappable content area, and dynamically import/call the page `factory` to obtain the content `QWidget` (show a placeholder error widget if the module/factory is missing).
- [x] Wire UI initialization into add-on startup (minimal change, likely `src/anki_notion_integration/__init__.py` importing/calling an `initialize_ui()` function guarded behind Anki imports).
- [x] Add `tests/test_ui_schema.py` (or similar) to cover: happy-path parsing, missing/invalid `pages` key, and invalid page entries (missing keys/incorrect types).
- [x] Run the unit test suite (`python -m unittest`) and fix any import/packaging issues (ensure UI code remains importable outside Anki).
- [ ] When requested, open a PR describing the feature branch changes and how `ui.json` drives navigation.

## Open questions
- None.
