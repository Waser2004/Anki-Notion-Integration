# UI pages (`src/anki_notion_integration/ui/`)

## Goal
The add-on UI is configured via a small schema file and lazy-loaded page modules.

- **UI schema**: `src/anki_notion_integration/docs/ui.json`
- **UI runtime**: `src/anki_notion_integration/ui/ui.py`
- **Page modules** (to implement): `src/anki_notion_integration/ui/pages_ui.py`, `src/anki_notion_integration/ui/settings_ui.py`, etc.

This document explains how to add a new top-level UI page (tab).

## How the UI is wired

1. `initialize_ui()` loads the schema from `ui.json` via `load_ui_schema()`.
2. A toolbar link labeled “Notion” is injected using `gui_hooks.top_toolbar_did_init_links`.
3. Clicking “Notion” opens `NotionWindow`, which creates one tab per page definition.
4. Tabs are **lazy-loaded**: the first time a tab is selected, the configured module is imported and a factory builds the tab widget.

## Add a new UI page (tab)

### 1) Add the page to `ui.json`

Edit `src/anki_notion_integration/docs/ui.json` and add an entry:

```json
{
  "key": "my_page",
  "name": "My Page",
  "module": "anki_notion_integration.ui.my_page_ui",
  "factory": "build_page"
}
```

Rules enforced by `load_ui_schema()`:

- `pages` must be a non-empty list
- each page needs `key`, `name`, and `module`
- `key` must be unique
- `factory` defaults to `"build_page"` when omitted

### 2) Create the page module

Create `src/anki_notion_integration/ui/my_page_ui.py` and implement the factory referenced in `ui.json`.

To match Anki’s look and feel, prefer the standard Qt widgets provided by Anki (imported from `aqt.qt`) and stick to Anki’s default styling/layout patterns rather than custom palettes/stylesheets.

Expected factory signature:

```py
from aqt.qt import QWidget
from anki_notion_integration.ui.ui import UiContext

def build_page(parent: QWidget, context: UiContext) -> QWidget:
    ...
```

Notes:

- The factory **must** return a `QWidget` instance (validated at runtime in `NotionWindow._load_page_widget()`).
- Import Qt widgets from `aqt.qt` (this code runs inside Anki).
- Use `context` for shared resources such as:
  - `context.mw` (Anki main window)
  - `context.profile_folder` (active profile directory)
  - `context.db_path` (SQLite DB path for the current profile)

### 3) Keep imports Anki-safe

`src/anki_notion_integration/ui/ui.py` imports `aqt` modules at import time, so it should only be imported inside Anki.

For page modules, follow the same expectation:

- Don’t make page modules importable in a plain Python environment unless you guard Anki imports.
- Keep heavy work out of module import; do work in `build_page()` or in response to user actions.

## Example page skeleton

```py
from __future__ import annotations

from aqt.qt import QLabel, QVBoxLayout, QWidget

from anki_notion_integration.ui.ui import UiContext


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.addWidget(QLabel(f"DB: {context.db_path}", widget))
    return widget
```

## Where to put page code

- Top-level tabs live as modules in `src/anki_notion_integration/ui/`.
- Keep page modules small and UI-focused; push persistence or domain logic into non-UI modules (e.g., `db.py`, sync logic modules, Notion client modules).
