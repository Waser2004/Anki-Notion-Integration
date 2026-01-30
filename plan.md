# Plan

Build an Anki add-on that syncs Notion toggles into Basic Anki notes, using a local SQLite mapping for idempotent updates. Start with one-way Notion → Anki sync (startup + button), minimal UI, and a small, testable core.

## Scope
- In: Anki add-on skeleton, Notion API token auth, page selection UI, toggle→Basic note generation, local SQLite mapping, startup + manual sync, simple errors, deletion prompt.
- Out: OAuth, bi-directional sync/conflicts, image occlusion implementation, rich block rendering, cloze/typed cards, cloud service.

## Action items
- [x] Design SQLLite schema (pages, cards, settings)
- [x] Create db at startup if it does not yet exist
- [ ] Implement settings.py / settings.json
    - settings.json stores relevant information for each setting i.e. `type` (boolean, checkbox, text, dropdown, etc.), `name` (display name), `description` (short consice description). The settings are grouped into subcategoires.
    - settings.py provides an interface for settings_ui.py to read settings. It reads and provides values from db and updates db entrys if changes to settings happen.
    - settings.py also provides a function to create default settings when db is created.
- [ ] Implement ui.py / ui.json
    - ui.py implements:
        - the Notion button in Anki’s top toolbar. 
        - the seperate add-on window including the top navigation bar (reads available pages from ui.json under the `pages` key)
        The content area is filled by specialised files like `settings_ui.py` and `pages_ui.py`
    - ui.json provides infromation about the ui structure.
- [ ] Implement settings_ui.py, handles the visualisation of the settings and forwards update requests by the user
- [ ] Implement notion_client.py (only interface for listing pages)
- [ ] Implement pages_ui.py, visualiese the pages from notion which the user can select to be converted.
- ...