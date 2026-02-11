# UI pages (`src/anki_notion_integration/ui/`)

Top-level tabs are configured in `src/anki_notion_integration/docs/ui.json` and lazy-loaded by `src/anki_notion_integration/ui/ui.py`.

Current tabs:

- `pages` → `anki_notion_integration.ui.pages_ui`
- `image_occlusion` → `anki_notion_integration.ui.image_occlusion_ui`
- `settings` → `anki_notion_integration.ui.settings_ui`

## Navigation support

`ui.py` exposes `navigate_to_page(page_key, payload=None)` for cross-tab navigation.

- `Pages` tab uses this to open `image_occlusion` with a preselected page payload.
- Target pages can implement `on_navigation_payload(payload)` to react to navigation context.

## Adding a page

1. Add a schema entry in `ui.json` with unique `key`, `name`, `module`, and optional `factory`.
2. Implement `build_page(parent, context) -> QWidget` in the module.
3. Keep heavy work out of module import; load data on user action or `reload()`.
