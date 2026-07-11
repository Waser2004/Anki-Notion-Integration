# Settings (`src/Noteck/settings.py`)

Settings are schema-driven from `src/Noteck/docs/settings.json`.

Each setting can provide both:

- `description`: longer explanatory copy for documentation or inline helper text.
- `tooltip`: short hover text used by the Settings UI.

## Categories

- `notion`: Notion API key (keyring-backed)
- `cards`:
  - `default_card_type` (`basic`, `basic_reversed`, `input`)
  - `enable_cloze_parsing` (bool)
  - `enable_gray_toggle_cloze_parsing` (bool; enabled by default; applies only to toggles with `gray_background`)
  - `enable_image_occlusion_parsing` (bool)
  - `restore_default_card_templates` (button action)
- `sync`:
  - `sync_with_anki_sync_button` (bool)
  - `notion_to_anki_auto_sync` (bool)
  - `page_selection_behavior`: `manual` (`Manual`), `existing_descendants` (`Smart`), `dynamic_descendants` (`Dynamic`)
  - `sync_notion_now` (button action)

## Behavior

- Defaults are inserted by `create_default_settings(db)` for DB-backed settings.
- Dropdown values are validated against schema options.
- Settings UI hover text uses each setting's `tooltip` field when present, falling back to `description`.
- `page_selection_behavior` option labels, mode descriptions, and option-specific tooltips are shared from `Noteck.modules.pages` so the Settings dropdown, Settings description, and Pages status tooltip describe the same modes.
- Button settings are action-only and are not persisted.
- The card template action is hidden when templates match the bundled defaults. It is shown
  as an update action for older bundled template versions and as a restore action for
  user-modified templates.

## Usage

Use `SettingsStore` to read/write values with typed coercion:

- booleans stored as `"1"`/`"0"`
- dropdowns validated
- keyring-backed secrets stored per profile namespace
