# Plan

Implement a settings metadata file (`settings.json`) plus a Python settings layer (`settings.py`) that reads/writes user-configurable values via the existing SQLite `settings` table, and seeds default values when a new DB is created. For the Notion API key, use OS-backed secret storage (`keyring`) so the key is encrypted at rest without us having to manage an encryption key.

## Scope
- In: Define a JSON schema for settings metadata (grouped categories, display labels, types, descriptions, defaults); implement typed read/write accessors backed by `Database.get_setting`/`Database.set_setting`; store `notion_api_key` in the system keychain via `keyring`; add a default-seeding hook during DB initialization; add minimal `unittest` coverage for default seeding + round-trips; add/update dependency tracking for the chosen secret-storage library.
- Out: Implementing `settings_ui.py` rendering, OAuth flow, or broader UI/UX beyond what `settings.py` needs to support.

## Action items
- [ ] Confirm initial setting keys + defaults and normalize naming: `notion_api_key` (default empty), `notion_to_anki_auto_sync` (default true), `anki_to_notion_sync` (default false).
- [ ] Choose secret storage approach for `notion_api_key`: use `keyring` and store the API key under a stable service/user identifier (e.g., per Anki profile), while keeping non-secret settings in SQLite.
- [ ] Add dependency tracking for secrets storage: add `keyring` to a repo-level `requirements.txt` (and plan to vendor it into the add-on bundle per Anki’s add-on packaging guidance).
- [ ] Define and add `src/anki_notion_integration/docs/settings.json` with grouped categories and per-setting metadata (`key`, `type`, `name`, `description`, `default`, and `options` for dropdowns where needed).
- [ ] Implement `src/anki_notion_integration/settings.py` to (a) load `docs/settings.json`, (b) provide typed `get_*`/`set_*` APIs, (c) validate/coerce DB string values into expected Python types, and (d) read/write `notion_api_key` via `keyring`.
- [ ] Add `create_default_settings(db: Database)` (or equivalent) in `settings.py` that inserts missing non-secret keys with defaults (from `settings.json`) without overwriting existing user values.
- [ ] Wire default seeding into DB creation by calling the settings-default function from `Database.initialize()` (or from the profile hook in `src/anki_notion_integration/__init__.py`) after migrations run.
- [ ] Add minimal tests in `tests/` using `unittest` and a temporary SQLite file to verify: defaults are created on fresh DB, existing values are preserved, typed getters/setters round-trip correctly, and `keyring` access is exercised using an in-memory keyring backend during tests.

## Open questions
- None.
