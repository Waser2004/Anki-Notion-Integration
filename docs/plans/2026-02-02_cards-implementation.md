# Plan

Implement only the Anki-side model registration: add `src/anki_notion_integration/cards.py` to create/ensure a dedicated “Notion Toggle” Anki note type at startup, using the provided Notion-like CSS, and make the operation idempotent so it won’t duplicate or clobber user changes on restart.

## Scope
- In: Create/ensure a dedicated Anki note type for Notion toggle cards (fields, card template, and CSS); load CSS from `docs/Notion_Card_Stylesheet.css`; register the model during Anki startup/profile open so it exists before any later sync work.
- Out: Notion→HTML rendering, sync engine logic, UI integration for creating notes/cards, deck naming/mapping, and any new dependencies.

## Action items
- [ ] Inspect the current startup hook (`src/anki_notion_integration/__init__.py`) and pick the exact place to call `ensure_notion_toggle_model()` so it runs on profile open.
- [ ] Define the “Notion Toggle” note type contract (model name, fields, one card template, CSS source path) and the idempotency rules (how to detect existing model; what is safe to update).
- [ ] Implement `src/anki_notion_integration/cards.py` with a single entrypoint `ensure_notion_toggle_model(mw)` that uses Anki’s models API to create the model if missing.
- [ ] Implement “ensure” behavior: if the model exists, ensure required fields/templates exist; only set/replace CSS when it matches the add-on-managed marker (avoid overwriting user edits).
- [ ] Wire the call into startup so the model is guaranteed present before any later card creation logic is added.
- [ ] Manually validate in Anki: restart Anki, confirm the model exists in `Tools → Manage Note Types`, and that its CSS matches `docs/Notion_Card_Stylesheet.css` on first install.

## Open questions
- None.
