# UI pages (`src/Noteck/ui/`)

Top-level tabs are configured in `src/Noteck/docs/ui.json` and lazy-loaded by `src/Noteck/ui/ui.py`.

Current tabs:

- `pages` → `Noteck.ui.pages_ui`
- `cards` → `Noteck.ui.cards_ui`
- `image_occlusion` → `Noteck.ui.image_occlusion_ui`
- `settings` → `Noteck.ui.settings_ui`

## Navigation support

`ui.py` exposes `navigate_to_page(page_key, payload=None)` for cross-tab navigation.

- `Pages` tab uses this to open `image_occlusion` with a preselected page payload.
- Target pages can implement `on_navigation_payload(payload)` to react to navigation context.

## Adding a page

1. Add a schema entry in `ui.json` with unique `key`, `name`, `module`, and optional `factory`.
2. Implement `build_page(parent, context) -> QWidget` in the module.
3. Keep heavy work out of module import; load data on user action or `reload()`.

## Pages tab selection behavior

The Pages tab shows the active selection behavior in its status label. The dropdown for changing it is available in the Settings tab under Sync as `Pages tab selection behavior`.

- `manual` (`Manual`): checkboxes affect only the page clicked. Parent pages and child pages stay independent.
- `existing_descendants` (`Smart`): default compatibility mode. Selecting a parent with no already selected child pages also selects the child pages currently visible in the Pages tab. New child pages created in Notion later are not selected automatically.
- `dynamic_descendants` (`Dynamic`): selecting a parent keeps its whole subtree selected. Child pages discovered on later refreshes are selected automatically and cannot be unselected independently while the parent remains selected.

The row context menu action `Select Page and All Children` performs an explicit one-time subtree selection in Manual and Smart modes. In Dynamic mode, selecting the parent establishes a persistent subtree selection: its descendants remain selected and future descendants are included automatically.

The local page cache is refreshed quietly in the background whenever an Anki
profile opens when startup auto-sync is disabled. When startup auto-sync is
enabled, page discovery is part of that sync instead. The first Pages tab opened
in a session reuses whichever startup discovery ran: it displays
`Refreshing pages... loaded X` while work is active and renders the resulting
cache without starting a duplicate Notion request. Later explicit refreshes
still fetch the current page list normally.
