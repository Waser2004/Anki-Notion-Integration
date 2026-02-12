# User Interface

### User Interface Overview

The primary way to interact with the add-on is via a toolbar button labeled **"Notion"**, located in Anki’s top toolbar alongside

`Decks · Add · Browse · Stats · Notion · Sync`.

Clicking the **“Notion”** button opens a dedicated add-on window with tabs.

The top navigation tabs are:

- **Pages**
- **Cards**
- **Image Occlusion**
- **Settings**

---

### Pages

The **Pages** tab provides a hierarchical tree of accessible Notion pages.

- Column 0: page checkbox + title (sync selection)
- Column 1: image button opens the **Image Occlusion** tab preselected for that page
- Column 2: `...` button opens per-page default card-type override (`basic`, `basic_reversed`, `input`)

**Selection behavior** remains asymmetric:

- Selecting a parent selects descendants only when none of its descendants are already selected.
- Deselecting a parent affects only that parent.
- Child toggles do not change parent selection.

---

### Cards

The **Cards** tab provides page-scoped toggle card controls.

- Shows toggle-derived cards for the selected page.
- Allows per-toggle exclusion from sync.
- Supports resetting toggle card-type overrides back to default behavior.

---

### Image Occlusion

The **Image Occlusion** tab lists image candidates from the currently selected synced page.

- Only images outside toggle blocks are listed.
- Clicking an item opens Image Occlusion Enhanced with that image.
- If Image Occlusion Enhanced is unavailable, the list stays empty and an install link is shown.

---

### Settings

The **Settings** tab is schema-driven and includes:

- Notion authentication settings
- Cards settings (`default_card_type`, cloze parsing toggle, image occlusion parsing toggle)
- Sync settings (startup/manual hooks)
