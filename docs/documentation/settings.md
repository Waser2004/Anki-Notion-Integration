# Settings (`src/anki_notion_integration/settings.py`)

Settings are schema-driven from `src/anki_notion_integration/docs/settings.json`.

## Categories

- `notion`: Notion API key (keyring-backed)
- `cards`:
  - `default_card_type` (`basic`, `basic_reversed`, `input`)
  - `enable_cloze_parsing` (bool)
  - `enable_image_occlusion_parsing` (bool)
- `sync`:
  - `sync_with_anki_sync_button` (bool)
  - `notion_to_anki_auto_sync` (bool)
  - `sync_notion_now` (button action)

## Behavior

- Defaults are inserted by `create_default_settings(db)` for DB-backed settings.
- Dropdown values are validated against schema options.
- Button settings are action-only and are not persisted.

## Usage

Use `SettingsStore` to read/write values with typed coercion:

- booleans stored as `"1"`/`"0"`
- dropdowns validated
- keyring-backed secrets stored per profile namespace
