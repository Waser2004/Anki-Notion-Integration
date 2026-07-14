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

1. Add the newest `## <version> - YYYY-MM-DD` entry at the top of `CHANGELOG.md`.
2. Put optional local images in `docs/release-notes-assets/` and reference them with relative Markdown paths.
3. Ensure the working tree contains only intended release changes.
4. Run `deploy/deploy_anki_addon_test.ps1`, restart Anki, and verify the latest notes open once and render correctly.
5. Verify the **Release Notes** footer button opens the complete history and a second restart does not repeat the popup.
6. Run `deploy/deploy_anki_addon.ps1`.
7. Verify `CHANGELOG.md` and optional assets exist under `%APPDATA%\Anki2\addons21\Noteck`.
8. Restart Anki and perform the remaining smoke test from the installed location.

## Known non-goals for v1.0

- No migration path from internal pre-release database schema variants.
- No two-way sync from Anki back to Notion.
- No automatic generation of Image Occlusion notes during sync.
