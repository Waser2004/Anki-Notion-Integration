# Settings (`src/anki_notion_integration/settings.py`)

## Goal
This project uses a schema-driven settings system for user-facing options:

- **Metadata** (name/description/type/default/storage) lives in `src/anki_notion_integration/docs/settings.json`.
- **Values** are stored either in the local SQLite DB (`settings` table) or in the OS keychain via `keyring`.

Notion OAuth credentials are managed by `src/anki_notion_integration/notion_oauth.py` and are not exposed as editable text settings.

This document explains how to read/write settings and how to add new ones.

## How settings are loaded

- `load_settings_schema()` reads `src/anki_notion_integration/docs/settings.json` and validates it.
- The schema is grouped into categories (`SettingsCategory`) for UI presentation.
- Each setting is represented by `SettingDefinition`.

Supported setting types are currently:

- `text`
- `checkbox` / `boolean` (stored as `"1"`/`"0"` in the DB)
- `dropdown` (requires an `options` list)
- `button` (UI action only; no value stored in DB/keyring)

## Read & write settings values (code)

Use `SettingsStore` as the main entry point. It needs a `Database` instance and (optionally) a profile name.

```py
from pathlib import Path

from anki_notion_integration.db import Database
from anki_notion_integration.settings import SettingsStore

# Use the same per-profile DB location as the add-on.
profile_folder = Path(".../your/anki/profile")  # e.g. mw.pm.profileFolder() inside Anki
db_path = profile_folder / "Anki_Notion_Integration" / "db" / "notion_integration.db"

db = Database(db_path)

store = SettingsStore(db, profile_name="default")

auto_sync = store.get_value("notion_to_anki_auto_sync")   # bool
store.set_value("notion_to_anki_auto_sync", False)        # persists to SQLite
```

### Secret storage

When a setting definition has `storage: "keyring"`, `SettingsStore` reads/writes using `KeyringSecretStore`.

OAuth code also uses `KeyringSecretStore` directly for access/refresh tokens, with per-profile usernames in the format `<profile_name>:<setting_key>`.

Note: secret storage requires the `keyring` dependency. If it is missing at runtime, `SettingsError` is raised.

## Initialize defaults

Call `create_default_settings(db)` once after DB initialization to ensure all **DB-backed** settings exist:

- Only settings with `storage != "keyring"` are inserted.
- Existing DB values are not overwritten.

In the add-on flow this is done in `src/anki_notion_integration/__init__.py` when the Anki profile opens.

## Sync-related settings behavior

The Sync category currently drives Notion → Anki behavior:

- `notion_to_anki_auto_sync`: runs a background Notion → Anki sync after profile startup.
- `sync_with_anki_sync_button`: runs Notion → Anki sync before Anki sync starts and shows Anki's native progress dialog.
- `sync_notion_now`: runs Notion → Anki sync from the Settings tab button and shows the same native progress dialog.
- `anki_to_notion_sync`: stored setting only (no active sync implementation yet).

The Notion category provides account actions:

- `notion_login`: launches browser-based public OAuth login.
- `notion_logout`: revokes OAuth tokens and clears local credentials.

Progress/cancel behavior for the two manual sync triggers above:

- Progress uses Anki's built-in dialog (`mw.progress`) so the look and behavior match Anki 25.x.
- The dialog is cancelable; closing it (or pressing Escape) requests cancellation.
- Cancellation returns a "Sync canceled." result and preserves partial stats up to the last completed unit of work.
- If progress APIs are unavailable, the add-on falls back to the existing non-progress background/blocking behavior.

Current source-of-truth for what gets synced:

- Enabled pages come from the `pages` table (`sync_enabled = 1`).
- Deck names come from `pages.anki_deck_name`.

## Provide settings to a UI

`SettingsStore.get_grouped_settings()` returns a UI-friendly structure:

- categories: `key`, `name`, `description`
- settings per category: includes current `value`, plus metadata (`type`, `default`, optional `options`)

This is intended to drive a Settings tab/page without hardcoding UI labels.

## Add a new setting

1. Edit `src/anki_notion_integration/docs/settings.json`.
2. Add a new entry under the appropriate category:
   - `key` (unique)
   - `type` (`text`, `checkbox`/`boolean`, `dropdown`, `button`)
   - `name`, `description`
   - `default`
   - optional `options` (required for `dropdown`)
   - optional `storage` (`"db"` default, or `"keyring"` for secrets)
3. If you added a DB-backed setting, ensure `create_default_settings(db)` is called for your DB (it already is in the Anki startup hook).

### Naming guidance

- Use stable, snake_case keys (these become DB primary keys and keychain lookup keys).
- Treat keys as part of your “public API”: renaming a key is a migration (values would otherwise be lost).
