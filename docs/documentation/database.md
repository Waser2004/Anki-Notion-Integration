# Database (`src/anki_notion_integration/db.py`)

## Goal
The add-on persists state locally in a per-Anki-profile SQLite database.

- **Schema definition + migrations**: `src/anki_notion_integration/db.py`
- **DB location (current default)**: `<AnkiProfile>/Anki_Notion_Integration/db/notion_integration.db`
  - Built in `src/anki_notion_integration/__init__.py` and `src/anki_notion_integration/ui/ui.py`

## How migrations work

- Migrations are defined in `MIGRATIONS` as ordered `Migration(version, statements)`.
- `Database.initialize()` creates a `schema_migrations` table and applies all migrations with `version > current_version`.
- The latest applied version is tracked via `schema_migrations.version`.

This lets you evolve the schema without deleting user data.

## Tables (migration version 1)

### `pages`
Stores which Notion pages are known/selected and how they map to Anki decks.

Columns:

- `notion_page_id` (TEXT, PK): Notion page ID.
- `anki_deck_name` (TEXT, NOT NULL): Target deck name for that page.
- `sync_enabled` (INTEGER, NOT NULL, default `1`): Whether the page should sync.
- `last_synced_at` (TEXT, nullable): Bookkeeping timestamp.

### `cards`
Stores the mapping between Notion blocks (toggles) and Anki notes/cards.

Columns:

- `notion_block_id` (TEXT, PK): Notion block ID (stable identity for sync).
- `notion_page_id` (TEXT, NOT NULL): Parent page ID.
- `anki_note_id` (INTEGER, UNIQUE, nullable): Corresponding Anki note id.
- `card_type` (TEXT, NOT NULL): Card type identifier (MVP: basic).
- `content_hash` (TEXT, NOT NULL): Hash of rendered content/settings for idempotent updates.
- `last_seen_notion_edit_time` (TEXT, nullable): Bookkeeping from Notion.
- `last_synced_at` (TEXT, nullable): Bookkeeping timestamp.
- `excluded` (INTEGER, NOT NULL, default `0`): Whether the block is excluded from sync.

Constraints / indexes:

- Foreign key `cards.notion_page_id -> pages.notion_page_id` with `ON DELETE CASCADE`.
- Index `idx_cards_page` on `cards(notion_page_id)`.

### `settings`
Stores non-secret settings values (see `docs/documentation/settings.md`).

Columns:

- `key` (TEXT, PK): Setting key (e.g., `notion_to_anki_auto_sync`).
- `value` (TEXT, NOT NULL): Serialized value (booleans stored as `"1"`/`"0"`).
- `updated_at` (TEXT, NOT NULL): Defaults to `datetime('now')`.

## API surface (`Database`)

`Database` is intentionally small:

- `connect()` opens a SQLite connection with:
  - `PRAGMA foreign_keys = ON`
  - `row_factory = sqlite3.Row` (dict-like row access)
- `initialize()` applies migrations.
- `get_setting(key)` / `set_setting(key, value)` are convenience helpers for the `settings` table.

For higher-level settings access (defaults, type coercion, keyring integration), use `SettingsStore` from `src/anki_notion_integration/settings.py`.

