# Settings (`src/anki_notion_integration/settings.py`)

## Goal
This project uses a schema-driven settings system:

- **Metadata** (name/description/type/default/storage) lives in `src/anki_notion_integration/docs/settings.json`.
- **Values** are stored either in the local SQLite DB (`settings` table) or in the OS keychain via `keyring`.

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

### Secret settings (`storage: "keyring"`)

If a setting definition has `storage: "keyring"`, `SettingsStore` reads/writes using `KeyringSecretStore`:

- `get_value(key)` returns the keychain secret, or the schema default when missing.
- `set_value(key, "")` (or `None`) deletes the secret.

Secret values are stored per profile name using a `username` formatted as: `<profile_name>:<setting_key>`.

Note: secret storage requires the `keyring` dependency. If it is missing at runtime, `SettingsError` is raised.

## Initialize defaults

Call `create_default_settings(db)` once after DB initialization to ensure all **DB-backed** settings exist:

- Only settings with `storage != "keyring"` are inserted.
- Existing DB values are not overwritten.

In the add-on flow this is done in `src/anki_notion_integration/__init__.py` when the Anki profile opens.

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
