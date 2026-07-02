# Parser (`src/Noteck/parser.py`)

## Goal
Convert Notion page blocks into typed card payloads for sync.

## Card outputs

`parse_page_to_cards(page_id, blocks, *, default_card_type, enable_cloze)` emits:

- Toggle-based cards using effective default card type:
  - `basic`
  - `basic_reversed`
  - `input`
- Optional cloze cards (`cloze`) from top-level paragraphs when `enable_cloze=True`.

Each payload includes:

- `notion_page_id`
- `notion_block_id`
- `card_type`
- `model_name`
- `fields` (model-specific field values)
- `content_hash`
- `last_edited_time`

## Cloze rules

- Source scope: top-level paragraph blocks only.
- Marker: yellow highlight annotation (`color: yellow_background` or `background_color: yellow`).
- Only paragraphs containing at least one marker emit a cloze payload.
- Inline equations inside cloze text are rendered with Anki MathJax delimiters (`\(...\)`).
- Consecutive highlighted rich-text fragments are combined into one cloze deletion, even when inline equations split the rich-text items.
- Optional `Extra` field source: the immediate next top-level paragraph whose plain text starts with `Extra:` (case-insensitive).
- The `Extra:` prefix is removed before rendering and the remaining rich text is kept as sanitized HTML.
- The matched `Extra:` paragraph is consumed and is not emitted as a separate cloze payload, even if it also has yellow markers.
- If no adjacent `Extra:` paragraph exists, `Extra` defaults to an empty string.

### Cloze example

Given top-level paragraphs in this order:

1. `Paris is the capital of ` + highlighted `France`
2. `Extra: Remember Eiffel Tower`

The cloze payload fields become:

- `Text`: `Paris is the capital of {{c1::France}}`
- `Extra`: `Remember Eiffel Tower`

## Input rules

`input` cards include `Expected Answer` normalized from back content by:

- stripping HTML,
- lowercasing,
- collapsing whitespace,
- removing punctuation noise.

## Image Occlusion candidates

`collect_image_occlusion_candidates(blocks)` returns image candidates:

- includes image blocks outside toggle trees,
- excludes image blocks inside toggles,
- keeps caption HTML/plain metadata for UI rendering.
