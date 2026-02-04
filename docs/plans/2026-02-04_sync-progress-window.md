# Plan

Implement a Notion→Anki sync progress window that matches Anki’s native sync-progress dialog by reusing Anki’s own progress/task APIs instead of custom-styled widgets. The approach is to route manual and pre-sync runs through a shared progress-enabled execution path, make the dialog cancelable, and target Anki 25.x behavior (validated first on 25.09.2).

## Scope
- In: Add a native-looking, cancelable progress flow for Notion sync, wire it into manual sync (`Settings` button) and “sync with Anki sync button”, and update tests/docs for Anki 25.x behavior.
- Out: Changes to sync business logic (card create/update rules), broader UI redesign, and any dependency/build/CI changes.

## Action items
- [ ] Review current sync entrypoints and call sites in `src/anki_notion_integration/sync.py`, `src/anki_notion_integration/ui/settings_ui.py`, and `src/anki_notion_integration/__init__.py` to map where progress UI must be introduced.
- [ ] Inspect Anki-native progress patterns in the local Anki API used by this project (task manager + progress manager) and choose the same API path used by Anki sync dialogs so appearance and behavior stay native.
- [ ] Add a shared progress-enabled runner in `src/anki_notion_integration/sync.py` that executes Notion sync via Anki task/progress hooks, publishes stage text and counts, supports cancellation, and guarantees cleanup/reset on success/failure/cancel.
- [ ] Refactor `trigger_sync_with_anki_button()` to use the new progress-enabled runner and preserve current guardrails (single-run lock, setting toggle, safe fallback when task/progress API is unavailable).
- [ ] Refactor manual sync in `src/anki_notion_integration/ui/settings_ui.py` to call the same runner so both paths show the same native progress window and keep the existing completion/error summaries.
- [ ] Add/adjust tests in `tests/test_sync.py` (and UI tests if needed) to cover progress runner invocation, lock/reset behavior, cancellation behavior, and fallback behavior when progress APIs are missing.
- [ ] Run `python -m unittest discover -s tests` from repo root in the activated environment and fix regressions related to the new progress flow.
- [ ] Validate compatibility on Anki 25.09.2 first, then keep APIs and fallbacks constrained to Anki 25.x semantics.
- [ ] Update documentation (`docs/documentation/settings.md` and, if needed, `docs/user_interface.md`) to describe when the native progress window appears, how cancellation behaves, and how errors are surfaced.

## Open questions
- None.
