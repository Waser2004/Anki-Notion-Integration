# Database (`src/Noteck/db.py`)

The add-on persists per-profile state in SQLite.

## Migrations

- Ordered schema migrations live in `MIGRATIONS`.
- `Database.initialize()` applies pending migrations and records applied versions in `schema_migrations`.
- Current latest schema version: **3**.
- The baseline schema is intentionally squashed for the first public release.
- Compatibility with pre-release database variants is intentionally unsupported.

## Tables

### `pages`

- `notion_page_id` (TEXT, PK)
- `anki_deck_name` (TEXT, NOT NULL)
- `anki_deck_id` (INTEGER, nullable)
- `sync_enabled` (INTEGER, NOT NULL)
- `content_hash` (TEXT, nullable; canonical full-page enhanced-Markdown hash)
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

### `notion_toggle_snapshots`

- `notion_block_id` (TEXT, PK)
- `notion_page_id` (TEXT, FK â†’ pages)
- `source_hash` (TEXT, NOT NULL; canonical enhanced-Markdown hash)
- `updated_at` (TEXT, NOT NULL)

Snapshots are independent from `cards` rows. This lets Noteck remember a
successfully parsed empty or otherwise non-card-producing toggle after its card
mapping is detached, preventing the same unchanged warning and recursive fetch
from repeating on every sync.

### `notion_card_controls`

- `notion_block_id` (TEXT, PK)
- `notion_page_id` (TEXT, FK → pages)
- `excluded` (INTEGER, NOT NULL; whether Notion explicitly excludes the card)
- `filtered` (INTEGER, NOT NULL; whether page-level cherry-pick mode filters the card)
- `card_type` (TEXT, nullable; card type selected by a Notion marker)
- `warning` (TEXT, NOT NULL; marker conflict or compatibility warning)
- `marker_icons` (TEXT, NOT NULL; normalized source markers for the Cards page)

These rows store Notion-authored controls separately from local exclusions and
card-type overrides. Each successful page parse replaces that page's control
snapshot so removed source markers cannot remain active.

## Helpers

- `Database.get_setting()` / `set_setting()` for low-level key/value access.
- `PagesStore` for page rows and selection/override persistence.
- `SettingsStore` for typed settings and keyring-backed secrets.
