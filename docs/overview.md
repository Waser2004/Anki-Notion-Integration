# Anki-Notion Integration

## Intention

Provide a robust Notion → Anki workflow that supports multiple card-generation modes while keeping sync deterministic and local-first.

## Current feature set

- Authentication via Notion API key (keyring storage)
- Page selection and hierarchy in `Pages` tab
- Card types:
  - `basic`
  - `basic_reversed`
  - `input`
  - `cloze` (parsed from top-level highlighted paragraphs)
- Page-level default card-type override (`basic` / `basic_reversed` / `input`)
- Image Occlusion workflow tab (user-driven, via Image Occlusion Enhanced)
- One-way Notion → Anki sync with stable block mapping + content hash checks

## Key behavior

- Notion is source of truth for generated content.
- Existing mapped notes auto-convert when card type changes.
- Image occlusion notes are not auto-generated during sync; users launch IOE manually from image candidates.

## Persistence

- `pages`: selection, deck mapping, hierarchy metadata, page card-type override
- `cards`: block → note mapping, card type, content hash
- `settings`: schema-driven values + keyring secrets
