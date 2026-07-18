# Database (`src/Noteck/db.py`)

The add-on persists per-profile state in SQLite.

## Migrations

- A single baseline migration lives in `MIGRATIONS`.
- `Database.initialize()` applies pending migrations and records applied versions in `schema_migrations`.
- Current latest schema version: **1**.
- The baseline schema is intentionally squashed for the first public release.
- Compatibility with pre-release database variants is intentionally unsupported.

## Tables

### `pages`

- `notion_page_id` (TEXT, PK)
- `anki_deck_name` (TEXT, NOT NULL)
- `anki_deck_id` (INTEGER, nullable)
- `sync_enabled` (INTEGER, NOT NULL)
- `content_hash` (TEXT, legacy/unused)
- `last_seen_notion_edit_time` (TEXT, nullable)
- `last_synced_at` (TEXT, nullable)
- `parent_id` (TEXT, nullable)
- `parent_type` (TEXT, nullable)
- `default_card_type` (TEXT, nullable; page-level override for default toggle card type)

### `cards`

- `notion_block_id` (TEXT, PK)
- `notion_page_id` (TEXT, FK → pages)
- `anki_note_id` (INTEGER, UNIQUE, nullable)
- `card_type` (TEXT, NOT NULL)
- `content_hash` (TEXT, NOT NULL)
- `last_seen_notion_edit_time` (TEXT, nullable)
- `last_synced_at` (TEXT, nullable)
- `excluded` (INTEGER, NOT NULL)

When a mapped source block is confirmed missing or no longer syncable, Noteck deletes the `cards` row and matching `card_type_overrides` row in one transaction. The associated Anki note is deliberately preserved and becomes unmanaged.

### `settings`

- `key` (TEXT, PK)
- `value` (TEXT, NOT NULL)
- `updated_at` (TEXT, NOT NULL)

### `card_type_overrides`

- `notion_block_id` (TEXT, PK)
- `notion_page_id` (TEXT, FK → pages)
- `card_type` (TEXT, NOT NULL)
- `updated_at` (TEXT, NOT NULL)

## Helpers

- `Database.get_setting()` / `set_setting()` for low-level key/value access.
- `PagesStore` for page rows and selection/override persistence.
- `SettingsStore` for typed settings and keyring-backed secrets.
