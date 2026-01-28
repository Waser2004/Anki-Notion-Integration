# Anki-Notion Integration

## Intention

Provide a straightforward, robust, and easy way to integrate Notion with Anki. Enable users to convert Notion toggles and content into Anki cards with automatic updating capabilities.

## Core Features

### Phase 1: MVP
- **Authentication**: Use Notion API key for initial access; design for OAuth integration in future
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
- **Supported content (MVP)**: text only (expand block support over time).
- **Toggle → card (MVP)**: toggle title = front, toggle content = back.
- **Metadata (MVP)**: store only the Notion toggle/block ID (tags/properties later).

### Deck Organization
- Each Notion page maps to its **own Anki deck**.
- If a selected page has subpages: subpages become **subdecks** of the parent deck.

### Sync Behaviour (Planned Settings)
- Default behaviour: **automatic sync at Anki startup** + **manual sync button**.
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

## Open Questions (Remaining)

### Architecture & Data Flow
- **Data Structure**: How should we structure Notion content to optimal Anki cards? Should we enforce a specific Notion template/format?
- **Sync Strategy**: How do we handle updates—full sync, incremental sync, conflict resolution if cards are edited in both Notion and Anki?
  - MVP: full sync vs incremental sync?
  - Conflict resolution rules for bi-directional sync (future).

### User Experience
- **Page Selection**: Should users select individual pages or enable bulk integration from a workspace? How do we handle nested pages?
- **Card Preview**: What information should be shown in the preview before card generation (content, count, estimated time)?
- **Error Handling**: How should the system communicate failures to users (e.g., failed API calls, malformed content)?
  - For MVP, keep it simple; later: add actionable guidance + logs.

### Card Generation Strategy
- **Content Parsing**: What Notion block types should we support initially (text, tables, lists, images)? How do we handle nested content?
- **Metadata**: Should we preserve Notion metadata (tags, properties) as Anki tags or fields?

### Integration Scope
- **Anki Deck Organization**: Should we create new decks, add to existing ones, or let users choose? How do we organize cards from multiple Notion pages?
- **Anki Field Customization**: Should we support custom Anki note types beyond the default, or stick to standard fields?
  - MVP: standard note type; later: user-configurable mapping.

### Maintenance & Updates
- **Change Detection**: How frequently should we check for Notion updates? Should it be manual or automatic?
- **Deletion Handling**: What happens when content is deleted in Notion—should corresponding cards be removed from Anki?
- **Version Compatibility**: Which versions of Anki and Notion API versions should we target?

### Non-Functional Requirements
- **Performance**: What's the acceptable limit for the number of pages/cards to sync in one operation?
- **Testing**: What test coverage target and which testing frameworks should we use?
- **Development Priority**: What should be the implementation sequence—backend logic, UI, sync engine, or testing infrastructure?
  - Suggested sequence:
    1. Define data model + local SQL persistence (mapping + settings)
    2. Implement Notion client + parsing for toggles/text (MVP)
    3. Implement one-way sync engine (Notion → Anki) + idempotency
    4. Implement minimal UI (page selection + preview count + sync button)
    5. Add settings and incremental sync (optional) + deletion prompts
    6. Add tests alongside (unit tests for parsing + sync decision logic; integration tests later)
