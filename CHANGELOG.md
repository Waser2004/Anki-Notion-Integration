# Changelog

## 1.3.0 - 2026-07-14

### Release notes

- Added a polished release-notes window that supports Markdown titles, sections, links, and optional bundled images.
- Added a `Release Notes` button beside the main add-on window's close action.
- Release notes now open once after an existing installation is updated, while fresh installations remain uninterrupted.
- Test deployments force the latest notes to appear once on the next Anki startup for straightforward manual verification.

## 1.2.0 - 2026-07-04

- Added dynamic page-selection behavior so selecting or deselecting a parent page now keeps its descendant pages in sync.

## 1.1.0 - 2026-07-04

- Preserved user-customized Noteck card template HTML and styling during normal startup and sync setup.
- Added a Settings action to restore default card templates when templates have been customized.
- Added card template version tracking so bundled template updates can be offered separately from restoring user edits.
- Added an update card templates action for older bundled template versions.
- Improved Settings action button focus handling after button clicks.

## 1.0.0 - 2026-02-12

- First public release.
- Added schema-driven Notion sync UI with tabs for Pages, Cards, Image Occlusion, and Settings.
- Added deterministic Notion -> Anki sync with card-type-aware mapping and content hashing.
- Added page-level default card-type overrides and toggle-level controls.
- Added Image Occlusion candidate workflow integration.
- Squashed database migrations into a single baseline migration for fresh installs.
