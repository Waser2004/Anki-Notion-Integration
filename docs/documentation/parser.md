# Parser (`src/anki_notion_integration/parser.py`)

## Goal
Convert Notion page content (as returned by `NotionClient.get_page_content(...)`) into a list of “Notion Toggle” card payloads that can later be created/updated in Anki.

In this project, a **toggle block becomes an Anki note**:

- **Front** = toggle title
- **Back** = rendered toggle children (expanded content)
- **Notion Block ID** = toggle block id (stable identity for sync + DB mapping)

The Anki note type is registered at startup by `src/anki_notion_integration/cards.py` and expects fields named:
`Front`, `Back`, `Notion Block ID`.

## Inputs

### Notion blocks
`NotionClient.get_page_content(page_id)` returns `list[NotionBlock]` where:

- `NotionBlock.block_id`: Notion block id (string)
- `NotionBlock.block_type`: Notion block type (e.g. `"toggle"`, `"paragraph"`)
- `NotionBlock.children`: nested blocks (already recursively populated)
- `NotionBlock.raw`: the original Notion API payload for that block

The parser should primarily read `block_type`, `block_id`, `children`, and `raw[block_type]` to extract rich text and type-specific properties.

## Output
The parser should produce a pure-Python structure that the sync engine can consume, for example:

- `notion_page_id` (string)
- `notion_block_id` (string, toggle id)
- `front_html` (string)
- `back_html` (string)
- optional bookkeeping (e.g. `last_edited_time`, deterministic `content_hash`)

Keep the parser free of Anki/`aqt` imports so it can be unit-tested outside Anki.

## High-level flow
1. **Walk the block tree** depth-first starting from the page’s top-level blocks.
2. **Collect toggle blocks** at any depth (`block.block_type == "toggle"`).
3. For each toggle:
   - Extract the title from `block.raw["toggle"]["rich_text"]`.
   - Render the children blocks into HTML for the back field.
   - Emit one card payload containing:
     - `Front` (HTML)
     - `Back` (HTML)
     - `Notion Block ID` (plain string id)

### Nested toggles (recommended)
Treat **every** toggle block as its own card, even when nested. This matches “toggle → card” and ensures each toggle is independently learnable/syncable.

In addition, when rendering a parent toggle’s back field, render nested toggles **inline** (e.g. as `<details><summary>…</summary>…</details>`) so the parent card visually matches Notion’s expanded view.

## Rich text → HTML rules
Notion represents formatted text as an array of `rich_text` items. Each item has:

- `plain_text` (display text)
- `href` (optional link target)
- `annotations` (bold/italic/underline/strikethrough/code/color)
- `type`:
  - `"text"` (with `text.content`)
  - `"equation"` (with `equation.expression`)

Render a `rich_text[]` array by concatenating rendered spans in order.

### Escaping
- Always HTML-escape text content (including `plain_text`) before inserting it into HTML.
- Never inject raw Notion content as HTML.

### Annotation mapping
Apply annotations in a stable nesting order (outer → inner) so output is deterministic.

Suggested mapping:

- `bold`: wrap with `<strong>…</strong>`
- `italic`: wrap with `<em>…</em>`
- `underline`: wrap with `<u>…</u>`
- `strikethrough`: wrap with `<s>…</s>`
- `code`: wrap with `<code>…</code>` (after escaping)
- `href`: wrap the final result with `<a href="…">…</a>`
- `color`: wrap with `<span class="highlight-<color>">…</span>`

### Colors and highlights
Notion’s `annotations.color` values (examples):

- `"default"`, `"gray"`, `"brown"`, `"orange"`, `"yellow"`, `"green"`, `"blue"`, `"purple"`, `"pink"`, `"red"`
- background variants: `"gray_background"`, …, `"red_background"`

The stylesheet in `src/anki_notion_integration/docs/Notion_Card_Stylesheet.css` defines matching classes:

- `.highlight-gray`, `.highlight-red`, …
- `.highlight-gray_background`, `.highlight-red_background`, …

So the renderer should emit `<span class="highlight-<color>">`.

### Inline equations
For `rich_text.type == "equation"`, render the expression as MathJax/LaTeX:

- Inline math: `\\( … \\)`

Example output:
`<span class="notion-equation">\\(E = mc^2\\)</span>`

## Block → HTML rules (required MVP)
The parser must render the following Notion block types inside toggle children.

General rules:

- Preserve block order.
- Ignore unknown blocks gracefully (render a placeholder or omit them, but keep parsing).
- Render children recursively (nested blocks inside list items, quotes, callouts, etc.).

### Paragraphs (`"paragraph"`)
- Source: `block.raw["paragraph"]["rich_text"]`
- Output: `<p>…</p>`

### Bulleted lists (`"bulleted_list_item"`)
- Source: `block.raw["bulleted_list_item"]["rich_text"]`
- Output: coalesce consecutive list items into a single `<ul>` with `<li>…</li>` entries.
- Nested list items should appear inside the parent `<li>` (render `block.children` recursively after the item’s text).

### Numbered lists (`"numbered_list_item"`)
Same as bulleted lists but using `<ol>`.

### Quotes (`"quote"`)
- Source: `block.raw["quote"]["rich_text"]`
- Output: `<blockquote><p>…</p>…children…</blockquote>`

### Callouts (`"callout"`)
- Source: `block.raw["callout"]["rich_text"]` plus optional `block.raw["callout"]["icon"]`
- Output: `<div class="callout">…</div>`
- If an icon is present:
  - Emoji icons can be rendered as a leading `<span class="notion-callout-icon">🙂</span>`
  - File/external icons can be omitted initially, or rendered as an `<img>` if/when fetching is added.

### Code blocks (`"code"`)
- Source: `block.raw["code"]["rich_text"]` and `block.raw["code"]["language"]`
- Output:
  - `<pre><code class="language-<lang>">…</code></pre>`
  - Escape code content.

### Block equations (`"equation"`)
- Source: `block.raw["equation"]["expression"]`
- Output (display math): `\\[ … \\]`

Example:
`<div class="notion-block-equation">\\[x^2 + y^2 = z^2\\]</div>`

## Determinism and hashing
Even before the full sync engine exists, the parser should be designed for deterministic output:

- Use a fixed HTML structure (consistent whitespace and tag nesting).
- Coalesce lists deterministically (single `<ul>`/`<ol>` for consecutive items).
- Avoid including volatile data (timestamps, random ids) in rendered HTML.

This enables stable `content_hash` values later for idempotent sync updates.

## Recommended module shape
When implementing `parser.py`, keep it split into small, testable helpers:

- `extract_toggle_blocks(blocks: list[NotionBlock]) -> list[NotionBlock]`
- `render_rich_text(rich_text: list[dict]) -> str`
- `render_blocks(blocks: list[NotionBlock]) -> str`
- `parse_page_to_cards(page_id: str, blocks: list[NotionBlock]) -> list[ToggleCardPayload]`

The sync engine can then:
1) fetch blocks via `NotionClient`,
2) call `parse_page_to_cards(...)`,
3) create/update Anki notes using the registered “Notion Toggle” model.

