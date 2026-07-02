# Noteck

Anki add-on that syncs Notion content into Anki notes with deterministic, local-first behavior.

## What it does

- Syncs selected Notion pages into Anki.
- Supports `basic`, `basic_reversed`, `input`, and `cloze` card generation.
- Provides per-page defaults and per-toggle controls in the UI.
- Offers an Image Occlusion workflow for image blocks.

## Installation and deployment

Noteck is currently deployed as a folder-based Anki add-on.

1. Clone this repository.
2. Ensure Anki is closed.
3. Run `deploy/deploy_anki_addon.ps1` in PowerShell.
4. Start Anki and verify the `Notion` toolbar button is visible.

The deployment script mirrors `src/Noteck` to:
`%APPDATA%\Anki2\addons21\Noteck`

## Dependency behavior (`keyring`)

- `keyring` is required for secure storage of the Notion API key.
- The deployment scripts install Python dependencies into the add-on `_vendor` folder.
- At startup, the add-on only checks whether `keyring` is importable and asks you to redeploy if it is missing.
- The current Python requirement is `keyring>=25.7.0` (see `requirements.txt`).

## Running tests

From repository root:

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests
```
