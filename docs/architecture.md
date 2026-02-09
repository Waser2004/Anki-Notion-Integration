# Architecture

This document describes a scalable MVP architecture for the **Anki add-on** variant (runs inside Anki, sync at startup + manual sync).

## Goals
- Deterministic conversion of Notion content into Anki cards.
- Reliable updates via stable identifiers (Notion block IDs).
- Local-first storage (no server required for MVP).

## High-Level Components

### 1) UI (Anki Add-on)
- Page selection (single + bulk selection via search/filter).
- Preview (MVP: card count per page).
- Actions: Sync now, manage settings, review conflicts/deletions (future).

### 2) Settings
- Notion API token (stored via encrypted local storage).
- Selected Notion pages to sync.
- Sync toggles:
  - Notion → Anki auto-sync (startup)
  - Manual sync button
  - (Future) Anki → Notion sync
  - (Future) Auto keep-latest conflict policy
- Behaviour policies:
  - Deletion handling (prompt user; delete/suspend/keep)

### 3) Notion Client
- Fetch selected pages and their blocks (MVP: toggles + text; future: images for image occlusion).
- Normalizes Notion block structures into an internal representation for parsing.

### 4) Parser / Renderer
- Converts Notion blocks into card content.
- MVP rules:
  - Toggle title → front
  - Toggle content → back
- Future:
  - Image blocks → image occlusion notes (mask created/edited by user).
  - Extend supported block types (lists, tables, etc.) behind feature flags.

### 5) Sync Engine
Responsibilities:
- Decide what to create/update/delete in Anki based on Notion state + stored mapping.
- Ensure idempotency (running sync twice yields no extra changes).

Recommended MVP policy:
- **Notion is the source of truth** (one-way Notion → Anki).
- Track changes using `last_edited_time` and/or a stable content hash of the rendered front/back.

Future policies:
- Bi-directional sync (Anki ↔ Notion) with explicit conflict handling.
- Optional “keep-latest” automatic resolution.

### 6) Anki Adapter
- Creates/updates notes/cards and places them into decks.
- Deck mapping:
  - Each Notion page → one deck
  - Subpages → subdecks of the parent page deck

### 7) Local Persistence (SQL Database)
Stores:
- Notion page selection + settings.
- Mapping between Notion blocks and Anki notes/cards.
- Sync bookkeeping (timestamps/hashes) to enable incremental sync and conflict detection.

## Data Model (Proposed)

### Tables

**`pages`**
- `notion_page_id` (PK)
- `anki_deck_name`
- `sync_enabled` (bool)
- `last_synced_at` (timestamp)

**`cards`**
- `notion_block_id` (PK)
- `notion_page_id` (FK → pages)
- `anki_note_id` (unique)
- `card_type` (e.g., basic; future: cloze, etc.)
- `content_hash` (hash of rendered front/back + relevant settings)
- `last_seen_notion_edit_time` (timestamp/string)
- `last_synced_at` (timestamp)
- `excluded` (bool)

**`conflicts`** (future)
- `notion_block_id`
- `anki_note_id`
- `notion_version_hash`
- `anki_version_hash`
- `created_at`
- `resolution` (enum)

## Sync Flow (MVP: Notion → Anki)
1. Load selected pages + settings from DB.
2. For each page:
   - Fetch blocks from Notion.
   - Extract toggles (MVP) and render front/back.
3. For each rendered toggle:
   - Lookup `notion_block_id` in `cards`.
   - If missing: create Anki note, write mapping.
   - If present and `content_hash` changed: update Anki note, update hash/timestamps.
4. For blocks previously mapped but not found anymore:
   - Mark as deleted/missing and prompt user (MVP policy: keep cards by default).
5. Update `last_synced_at` bookkeeping.

## Notes On “Full Scan” vs Incremental (MVP)
- MVP uses a simple **full scan of the selected pages** each time sync runs.
- Updates are still “incremental” in effect because the add-on only creates/updates notes when the rendered `content_hash` differs from what’s stored in the local DB.

## Error Handling & Observability (MVP)
- Show a simple user-facing error message in the UI.
- Keep a local log file for debugging (future: expose “copy debug info” in UI).

## Incremental Delivery Plan (Aligned With `docs/overview.md`)
1. Define internal data model + SQL schema.
2. Implement Notion client + toggle parsing.
3. Implement one-way sync engine + idempotency.
4. Add minimal UI (page selection + preview count + sync button).
5. Add settings, deletion prompt, and optional incremental optimizations.
6. Extend block support and add (future) bi-directional sync + conflicts.
