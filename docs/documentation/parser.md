# Parser (`src/Noteck/modules/parser/`)

## Goal
Convert Notion page blocks into typed card payloads for sync.

## Services

- `parser/parser.py` coordinates card parsing and payload assembly.
- `parser/renderer.py` owns deterministic Notion block and rich-text HTML rendering.
- `parser/basic_card_parser.py` creates Basic, Basic+Reversed, and Input payloads from toggles.
- `parser/cloze_card_parser.py` creates normal/advanced cloze payloads and validates them before sync writes to Anki.

See `cloze_card_parser.md` for the detailed cloze-parser architecture, control flow, rendering differences, payload contract, and extension guidance.

## Card outputs

`parse_page_to_cards(page_id, blocks, *, default_card_type, enable_cloze, enable_gray_toggle_cloze)` emits:

- Toggle-based cards using effective default card type:
  - `basic`
  - `basic_reversed`
  - `input`
- Optional cloze cards (`cloze`) from top-level paragraphs when `enable_cloze=True`.
- Optional toggle-based cloze cards from top-level cloze containers when `enable_cloze=True`.

Callers may pass a warning list to collect structured `CardParseWarning` values. A toggle with no usable title or body is skipped with a warning, and a rendering failure is isolated to that block so other cards can still be parsed.

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
- Markers: yellow, green, blue, purple, pink, orange, red, and brown background annotations, mapped to `c1` through `c8` respectively. A paragraph block background alone is not a marker.
- Only paragraphs containing at least one marker emit a cloze payload.
- Block-level backgrounds remain supported for content inside advanced cloze containers, where the container supplies context.
- A marked paragraph that produces no usable cloze text is skipped with a warning.
- Inline equations inside cloze text are rendered with Anki MathJax delimiters (`\(...\)`).
- Consecutive highlighted rich-text fragments are combined into one cloze deletion, even when inline equations split the rich-text items.
- Optional `Extra` field source: the immediate next top-level paragraph whose plain text starts with `Extra:` (case-insensitive).
- The `Extra:` prefix is removed before rendering and the remaining rich text is kept as sanitized HTML.
- The matched `Extra:` paragraph is consumed and is not emitted as a separate cloze payload, even if it also has supported cloze markers.
- If no adjacent `Extra:` paragraph exists, `Extra` defaults to an empty string.

### Cloze example

Given top-level paragraphs in this order:

1. `Paris is the capital of ` + highlighted `France`
2. `Extra: Remember Eiffel Tower`

The cloze payload fields become:

- `Text`: `Paris is the capital of {{c1::France}}`
- `Extra`: `Remember Eiffel Tower`

## Advanced cloze rules

Toggle-based cloze parsing uses the same `enable_cloze` option as paragraph cloze parsing:

- Source scope: top-level toggle blocks whose title starts with `[cloze]`, plus top-level toggle blocks with Notion block color `gray_background` when gray-toggle cloze parsing is enabled. The `gray` text color does not qualify.
- These containers are emitted as `cloze` cards and are not emitted as normal toggle cards.
- If cloze parsing is disabled, recognized cloze toggle containers are ignored instead of being parsed as another card type.
- The toggle title is a marker only; child blocks render into the cloze `Text` field.
- Direct child paragraphs starting with `Extra:` are excluded from `Text` and render into `Extra`; `[extra]` toggles render normally.
- Nested `[extra]` toggles are normal nested toggles and have no special meaning.
- Supported child blocks use the same HTML rendering as toggle cards, but rich text is cloze-aware.
- A configured block background hides direct text in one deletion while retaining the structure of paragraphs, headings, nested toggles, bulleted/numbered lists, quotes, and callouts. A marked callout preserves its icon, container, and child layout while its direct and descendant text uses the same cloze number; an explicitly colored nested callout starts its own cloze scope.
- Tables are handled per cell: a cell is whole-cell clozed only if every non-empty fragment has the same configured marker. The stylesheet highlights only the `td`/`th` containing Anki's currently hidden cloze child; partial cell highlights retain inline behavior.
- Marker background colors map to fixed cloze numbers: yellow -> c1, green -> c2, blue -> c3, purple -> c4, pink -> c5, orange -> c6, red -> c7, brown -> c8.
- Repeated cloze numbers and skipped numbers are valid.
- Marker background colors are removed from exported HTML, while normal formatting and non-marker highlight colors are preserved.
- Containers with no marker colors still emit deterministic `cloze` payloads instead of falling back to another card type; sync validation then rejects them before any Anki write because Anki needs at least one deletion.
- Code, equation, image, table/container, column, and divider blocks cannot carry a standalone complete block color in the current Notion API shape. Inside a marked callout, their textual content is hidden with that callout's cloze number while their supported renderer structure remains.

## Cloze validation before Anki writes

Every cloze payload is validated in the sync path immediately before Anki can create or update its note. A valid payload must use the `Notion (Cloze)` note type, have a non-empty `Text` field, and contain balanced Anki cloze markup with at least one positive cloze number and non-empty hidden text. The validator rejects malformed/stray delimiters and nesting deeper than Anki's documented three-level limit. Invalid cards are reported as sync errors and are not written to Anki.

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
