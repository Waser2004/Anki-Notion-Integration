# Plan

Implement the schema-driven Settings tab UI (`settings_ui.py`) so users can view and update settings defined in `docs/settings.json`. The page will render one `QGroupBox` per settings category and map each setting type to an appropriate Qt input widget, persisting changes via `SettingsStore`.

## Scope
- In: Build `src/anki_notion_integration/ui/settings_ui.py` with widgets for `text`, `checkbox`/`boolean`, and `dropdown`; load current values from `SettingsStore`; save user changes back to SQLite/keyring; wrap each category in a `QGroupBox`; add basic error handling and feedback in the UI.
- Out: Implementing other UI pages (e.g. `pages_ui.py`), adding new settings schema fields/types, or changing persistence behavior in `settings.py` / DB schema.

## Action items
- [x] Review `src/anki_notion_integration/docs/settings.json` and `src/anki_notion_integration/settings.py` to confirm supported types and storage rules.
- [x] Implement `build_page()` in `src/anki_notion_integration/ui/settings_ui.py` that renders categories as `QGroupBox` sections inside a scrollable layout.
- [x] Map schema setting types to widgets (`QLineEdit`, `QCheckBox`, `QComboBox`) and prefill with current values from `SettingsStore`.
- [x] Add a Save action that reads widget values, persists via `SettingsStore.set_value()`, and shows success/error feedback.
- [x] Handle secret (`storage: "keyring"`) text settings with a password-style input and clear-to-delete behavior.
- [x] Add minimal validation/edge-case handling (unknown types, missing keyring, empty schema) with user-visible errors instead of crashes.
- [x] Run existing unit tests (`python3 -m unittest`) and do a quick import smoke test for `anki_notion_integration.ui.settings_ui` in an Anki-like environment (or guard imports when running outside Anki).

## Open questions
- None.
