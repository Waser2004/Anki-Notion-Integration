# Anki-Notion Integration

## Intention

Provide a straightforward, robust, and easy way to integrate Notion with Anki. Enable users to convert Notion toggles and content into Anki cards with automatic updating capabilities.

## Core Features

### Phase 1: MVP
- **Authentication**: Use Notion public OAuth integration with standard Notion login flow
- **UI Overview**: Simple GUI displaying Notion pages with card generation preview
- **Card Types**: Support standard front-back cards
- **Card Sync**: Auto-update mechanism to keep Anki cards in sync with Notion

### Phase 2: Enhancements
- Additional card types: cloze, typed, image occlusion
- AI-powered features with unique question identification for improved recognition

## Decisions (Current)

These decisions reflect the current best guess and can be revised as we learn more during implementation.

### Delivery Form
- Build as an **Anki add-on** (runs inside Anki; can sync at startup and via a button).

### Card Mapping & Persistence
- Persist a mapping between **Notion block ID ↔ Anki note/card** in a local **SQL database**.
- Store additional per-block/per-page settings in the database (e.g., excluded toggles, page sync enabled, chosen card type, future metadata).

### Card Generation
- **Supported content (MVP)**: text + toggles (expand block support over time).
- **Toggle → card (MVP)**: toggle title = front, toggle content = back.
- **Metadata (MVP)**: store only the Notion toggle/block ID (tags/properties later).
- **Images (future)**: detect images as a basis for image occlusion; user provides/edits the mask.

### Deck Organization
- Each Notion page maps to its **own Anki deck**.
- If a selected page has subpages: subpages become **subdecks** of the parent deck.

### Sync Behaviour (Planned Settings)
- Default behaviour: **automatic sync at Anki startup** + **manual sync button**.
- Sync implementation (MVP): re-scan the selected Notion pages on each sync run and apply changes idempotently using stored mappings and hashes (simple “full scan”, incremental updates).
- Planned toggles:
  - **Notion → Anki auto-sync** (update Anki if Notion changed).
  - **Anki → Notion sync** (bi-directional; future feature).
  - **Auto keep-latest** on conflicts (if disabled: warn user and keep both versions until resolved).

### Deletions
- If content is deleted in Notion: keep Anki cards by default and **prompt the user** whether to delete/suspend them.

### Security
- Store API keys in **encrypted local storage** (noting that add-ons run on the user's machine, so secrets are ultimately accessible to that user).

### Compatibility
- Target the **latest available** Notion API and Anki versions at the time of implementation.

### Preview & Errors (MVP)
- Preview shows **card count per page** (expand later to show diffs/details).
- Error handling: simple, user-visible error messages (improve later with better diagnostics).

## Development Priority (Proposed)
1. Define data model + local SQL persistence (mapping + settings).
2. Implement Notion client + parsing for toggles/text (MVP).
3. Implement one-way sync engine (Notion → Anki) + idempotency.
4. Implement minimal UI (page selection + preview count + sync button).
5. Add settings, deletion prompt, and optional incremental optimizations.
6. Add tests alongside (unit tests for parsing + sync decision logic; integration tests later).
