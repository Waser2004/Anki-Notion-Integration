# Architecture

This document describes the Anki add-on architecture for Notion → Anki sync.

## Goals
- Deterministic card generation from Notion content.
- Stable sync updates with local mapping.
- Local-first storage and settings.

## Components

### UI
- `Pages` tab: page tree, sync selection with active selection-behavior status, per-page card type override, Image Occlusion shortcut.
- `Cards` tab: per-page toggle list with exclusion control and override reset actions.
- `Image Occlusion` tab: page-scoped image candidates and IOE launcher.
- `Settings` tab: schema-driven settings editor.

### Settings
- Global default card type: `basic`, `basic_reversed`, `input`.
- Feature toggles: cloze parsing, image occlusion parsing.
- Sync toggles: startup/manual sync triggers.
- Page selection behavior: `manual` (`Manual`), `existing_descendants` (`Smart`), `dynamic_descendants` (`Dynamic`).

### Parser / Renderer
- Toggle cards: `basic`, `basic_reversed`, `input`.
- Cloze cards: top-level paragraphs with yellow highlight markers.
- Image candidate extraction: image blocks outside toggles.

### Sync Engine
- One-way Notion → Anki sync with card-type-aware payloads.
- Per-page effective card type = page override or global default.
- Auto-convert mapped notes when card type changes.
- Hash-based idempotency including card type and payload fields.
- Card parsing and recoverable repairs produce structured warnings without failing sync.
- Missing or no-longer-syncable sources detach Noteck metadata while preserving Anki notes.
- Only source access, database, Anki write, or orchestration failures are sync errors.

### Persistence
- `pages`: selection, deck mapping, hierarchy metadata, optional `default_card_type` override.
- `cards`: Notion block → note mapping, `card_type`, content hash, sync metadata.
- `settings`: DB-backed schema values + keyring secrets, including page-selection behavior and dynamic subtree-selection roots.
- `card_type_overrides`: optional per-toggle card-type overrides with page linkage.
