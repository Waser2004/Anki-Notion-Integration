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

- The active behavior is shown in the Pages tab status label.
- The behavior is configured in Settings under Sync as **Pages tab selection behavior**.
- **Manual**: checking a page affects only that page.
- **Smart**: default compatibility mode; checking a parent with no selected child pages also selects the currently visible child pages.
- **Dynamic**: checking a parent keeps its subtree selected, including child pages discovered on later refreshes.
- The context menu action **Select Page and All Children** remains available for explicit one-time subtree selection.

---

### Cards

The **Cards** tab provides page-scoped controls for toggle-derived and paragraph-cloze cards.

- Shows top-level toggles and marked top-level paragraphs for the selected page.
- Identifies `[cloze]` and enabled gray-background toggles as cloze cards.
- Allows individual cards to be excluded from sync.
- Supports resetting card-type overrides for selectable toggle cards back to default behavior; cloze cards do not use these overrides.

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
- Cards settings (`default_card_type`, cloze parsing toggles, image occlusion parsing toggle)
- Sync settings (startup/manual hooks)

### Release notes

The footer of the Noteck window includes a **Release Notes** button beside **Close**
only while the **Settings** tab is active. It opens the complete bundled release
history in a separate scrollable window. Release notes support Markdown titles,
sections, lists, links, and optional offline images.

After an existing installation is updated, Noteck opens only the newest release entry
once on the next profile startup. Fresh installations do not show an automatic popup.
The release-notes window includes a **Hide after updates** button beside **Close**.
After automatic display is disabled, the same button becomes **Show after updates**
so the preference can be re-enabled at any time. Test deployments still force their
one-shot popup to keep the release QA workflow deterministic.
