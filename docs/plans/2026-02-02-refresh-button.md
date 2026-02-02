# Plan

Move the pages refresh control out of the tab content and attach it to the dialog footer so the button sits beside Close and only appears when the Pages tab is active and loaded.

## Scope
- In: moving the refresh action out of `pages_ui.py`, wiring a footer button inside `NotionWindow`, and ensuring its visibility/state matches the Pages tab lifecycle.
- Out: reworking other tabs, their buttons, or broader UI/architecture changes.

## Action items
- [x] Review `NotionWindow` tab construction and the current refresh logic inside `pages_ui.py` to understand how the action is triggered and where it can surface in the dialog footer.
- [x] Remove the inline refresh button from `PagesPage` so the toolbar only shows instructions, keeping `reload_pages` available for external triggers.
- [x] Add a new refresh button to the footer `QDialogButtonBox`, hook it up to `reload_pages`, and disable/enable it around the RPC exchange to avoid duplicate taps.
- [x] Update the tab-switch logic so the footer button is shown only when the Pages tab key/widget is active (hide it for Settings and before the page loads).
- [x] Double-check edge cases, e.g., the button stays hidden until the page widget is created and never fires for other tabs, and document/highlight any assumptions.
- [x] Run `python -m unittest discover -s tests` (after activating the virtual environment) or otherwise verify the UI change does not break existing tests (fails in this environment without `aqt`/`keyring`).

## Open questions
- None
