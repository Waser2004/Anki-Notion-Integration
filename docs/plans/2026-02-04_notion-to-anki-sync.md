# Plan

Implement a one-way Notion → Anki sync entrypoint (`sync.py`) and wire it into Anki startup, the Anki Sync button, and the Settings “Sync Notion now” button. Reuse the existing Notion client, parser, DB mapping, and card/model helpers to keep the sync idempotent and consistent with current architecture.

## Scope
- In: Notion → Anki sync (create/update Anki notes from selected Notion pages), trigger via Anki hooks and Settings button.
- Out: Anki → Notion sync, conflict resolution UI, deletion prompting workflows beyond a safe default, and new card types beyond the current MVP mapping.

## Action items
- [ ] Locate/confirm existing Notion→card pipeline and storage contracts (e.g., `notion_client.py`, `parser.py`, `cards.py`, `db.py`, `pages.py`) to avoid duplicate logic.
- [ ] Define `src/anki_notion_integration/sync.py` public API (inputs: `mw`, `db_path`, settings store; outputs: summary/result object; errors: user-safe + logged details).
- [ ] Implement Notion→Anki sync logic: load enabled pages from the DB `pages` table (skip pages marked as excluded), fetch Notion blocks, render cards, compute stable hashes, create/update notes idempotently, persist mapping + last-sync bookkeeping.
- [ ] Add Anki hook wiring for startup auto-sync (`notion_to_anki_auto_sync`) in `src/anki_notion_integration/__init__.py` (ensure it runs after DB/model/UI init and does not block UI).
- [ ] Add Anki hook wiring for “sync with Anki sync button” (`sync_with_anki_sync_button`) using a pre-sync hook (prefer `aqt.gui_hooks.sync_will_start` if available) so Notion changes land in Anki before Anki’s own sync runs; run this path in a blocking/progress UI flow.
- [ ] Implement Settings button action (`sync_notion_now`) in `src/anki_notion_integration/ui/settings_ui.py` to call the same `sync.py` entrypoint in a blocking/progress UI flow (disable button during run; show success/error summary).
- [ ] Add unit tests for `sync.py` (mock Notion responses and Anki collection/note APIs; cover create/update/no-op cases and missing settings/api key behavior).
- [ ] Validate with `python -m unittest discover -s tests` and document expected behavior + limitations in `docs/documentation/settings.md` (and/or add a short `docs/documentation/sync.md`).

## Open questions
- None.
