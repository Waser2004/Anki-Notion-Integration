# Plan

Build an Anki add-on that syncs Notion toggles into Basic Anki notes, using a local SQLite mapping for idempotent updates. Start with one-way Notion → Anki sync (startup + button), minimal UI, and a small, testable core.

## Scope
- In: Anki add-on skeleton, Notion API token auth, page selection UI, toggle→Basic note generation, local SQLite mapping, startup + manual sync, simple errors, deletion prompt.
- Out: OAuth, bi-directional sync/conflicts, image occlusion implementation, rich block rendering, cloze/typed cards, cloud service.

## Action items
[ ] Define add-on package layout under `src/` (e.g., `src/anki_notion_integration/` with `__init__.py`, `ui.py`, `sync.py`, `notion_client.py`, `parser.py`, `db.py`, `settings.py`).
[ ] Design the SQLite schema (pages + cards mapping) and DB location in the Anki profile folder; implement `db.py` with migrations/versioning.
[ ] Implement `notion_client.py` (stdlib HTTP) for: page search, fetch blocks, traverse child pages, retry/backoff on 429/5xx, and required Notion headers/versioning.
[ ] Implement `parser.py` (MVP): extract toggle blocks, render front/back text deterministically, compute `content_hash`, and return per-page card candidates + counts.
[ ] Implement `anki_adapter.py` (or in `sync.py`) to: ensure deck exists, create/update Basic notes, tag notes with `notion:<block_id>` (for traceability), and keep note IDs in SQLite.
[ ] Implement `sync.py` as an idempotent “full scan of selected pages”: compare rendered `content_hash` vs DB, create/update notes, and mark missing blocks for deletion review.
[ ] Build `ui.py` MVP: settings dialog (token + parent deck name + page picker/search), preview showing card count per selected page, and a “Sync now” button.
[ ] Hook into Anki lifecycle (`aqt.gui_hooks`): run auto-sync at profile open (with a lightweight progress dialog and cancel), and add a Tools menu entry for manual sync/settings.
[ ] Add deletion handling flow: when previously-mapped blocks disappear, show a prompt listing affected pages/count and allow delete/suspend/keep; persist the decision per run.
[ ] Add validation: `tests/` with `unittest` for parsing + sync decision logic (`python -m unittest`), plus a manual QA checklist in `docs/` (install add-on, first sync, edit toggle, resync, delete toggle, resync).

## Open questions
- How should the Notion token be stored on disk for MVP: OS keychain via `keyring` (if available) with fallback to Anki add-on config, or config-only with a clear warning?
- For page selection, do you want “search by title” only, or also “paste page URL/ID” as a guaranteed MVP fallback?
- What is the default parent deck name (e.g., `Notion`) and how should deck name collisions be handled (append short page id vs prompt user)?
