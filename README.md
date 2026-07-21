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
- The settings layer reports a clear error if `keyring` is unavailable at runtime.
- The current Python requirement is `keyring>=25.7.0` (see `requirements.txt`).

## Running tests

From repository root:

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests
```

## Writing release notes

`CHANGELOG.md` is the single source for both repository history and the add-on's
release-notes window. Put each new release first and use this format:

```markdown
## 1.4.0 - 2026-08-01

### Highlights

- Describe the user-visible change in plain language.
- Add more sections or links when they help.

![Optional interface overview](docs/release-notes-assets/1.4.0-overview.png)
```

Every level-two heading must contain a release version and ISO date. Lower-level
headings, lists, emphasis, and links use normal Markdown. Optional local images belong
in `docs/release-notes-assets/`; all deploy and ZIP scripts bundle them automatically.
No Python version constant or structured data file needs to be updated.

## Diagnostic logs

Noteck records detailed sync diagnostics (page decisions, excluded cards, card creation,
updates, recreation after a missing Anki note, type-change invalidation, and errors) in the
active Anki profile at `Noteck/logs/noteck.log`. The file rotates at 1 MB and keeps four
backups, limiting storage to roughly 5 MB per profile. Logs intentionally contain identifiers
and sync metadata, but never the Notion API key or card field content.
