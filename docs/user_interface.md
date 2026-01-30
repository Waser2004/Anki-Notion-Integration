# User Interface

### User Interface Overview

The primary way to interact with the add-on is via a toolbar button labeled **“Notion”**, located in Anki’s top toolbar alongside

`Decks · Add · Browse · Stats · Notion · Sync`.

Clicking the **“Notion”** button opens a **dedicated add-on window**. This window is visually and structurally similar to Anki’s **Preferences** dialog.

The window consists of:

- A **top navigation bar** for switching between different sections
- A **main content area** that updates based on the selected section

The top navigation bar contains the following tabs:

---

### Pages

The **Pages** tab provides an overview of the user’s Notion pages.

- Pages are displayed in a **hierarchical tree structure**, analogous to folders and files in a file explorer.
- Each page entry includes a **checkbox** that allows the user to select whether the page should be included for Anki card generation or synchronization.
- Parent–child relationships between pages are visually reflected through indentation or expandable/collapsible nodes.

**Selection Behavior**

Page selection follows explicit, asymmetric parent–child rules:

- Selecting a parent page
    - If all child pages are currently unselected, selecting the parent will select the parent and all of its child pages recursively.
    - If at least one child page is currently selected, selecting the parent affects only the parent itself and leaves all children unchanged.
- Deselecting a parent page affects only the parent. All child pages retain their current selection state.
- Selecting or deselecting a child page does not affect its parent.

This behavior ensures that parent selection acts as a **convenience shortcut** for bulk selection when needed, without enforcing strict bidirectional coupling between parent and child pages.

This tab is the primary interface for choosing which Notion content is relevant for Anki.

---

### Settings

The **Settings** tab is used for configuration and account management.

- All add-on–specific settings are grouped and editable here.
- Notion authentication is handled in this tab:
    - Users can log in to or log out of their Notion account.
    - The current authentication status is clearly indicated.
- Any settings that affect synchronization behavior, card generation, or page handling are configured here.