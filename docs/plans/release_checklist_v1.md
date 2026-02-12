# Release Checklist v1.0

## Test run steps

1. Activate the virtual environment.
2. Run:
   `./.venv/Scripts/python.exe -m unittest discover -s tests`
3. Confirm all tests pass.

## Manual QA smoke list

1. Start Anki and open the `Notion` window from the toolbar.
2. Verify tabs exist: `Pages`, `Cards`, `Image Occlusion`, `Settings`.
3. In `Settings`, save and reload:
   - Notion API key
   - `default_card_type`
   - sync toggles
4. In `Pages`, select at least one page for sync and verify hierarchy actions.
5. Run manual sync (`Sync Notion now`) and verify notes are created/updated in Anki.
6. In `Cards`, exclude one toggle and verify exclusion persists.
7. In `Image Occlusion`, verify candidates load for a page with images and launcher behavior works.

## Packaging / export steps

1. Ensure working tree contains only intended release changes.
2. Run `deploy/deploy_anki_addon.ps1`.
3. Verify deployed folder under `%APPDATA%\Anki2\addons21\Noteck`.
4. Restart Anki and perform smoke test from installed location.

## Known non-goals for v1.0

- No migration path from internal pre-release database schema variants.
- No two-way sync from Anki back to Notion.
- No automatic generation of Image Occlusion notes during sync.
