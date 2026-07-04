# Settings (`src/Noteck/settings.py`)

Settings are schema-driven from `src/Noteck/docs/settings.json`.

## Categories

- `notion`: Notion API key (keyring-backed)
- `cards`:
  - `default_card_type` (`basic`, `basic_reversed`, `input`)
  - `enable_cloze_parsing` (bool)
  - `enable_image_occlusion_parsing` (bool)
  - `restore_default_card_templates` (button action)
- `sync`:
  - `sync_with_anki_sync_button` (bool)
  - `notion_to_anki_auto_sync` (bool)
  - `sync_notion_now` (button action)

## Behavior

- Defaults are inserted by `create_default_settings(db)` for DB-backed settings.
- Dropdown values are validated against schema options.
- Button settings are action-only and are not persisted.
- The card template action is hidden when templates match the bundled defaults. It is shown
  as an update action for older bundled template versions and as a restore action for
  user-modified templates.

## Usage

Use `SettingsStore` to read/write values with typed coercion:

- booleans stored as `"1"`/`"0"`
- dropdowns validated
- keyring-backed secrets stored per profile namespace
