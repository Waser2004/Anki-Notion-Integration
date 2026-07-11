# Notion Blocks and Text Highlighting Support

This reference documents what the parser currently supports when converting Notion content to card HTML.

## Supported Notion block types

The renderer supports these block types:

- `paragraph`
- `heading_1`
- `heading_2`
- `heading_3`
- `bulleted_list_item` (coalesced into `<ul>` sequences)
- `numbered_list_item` (coalesced into `<ol>` sequences)
- `quote`
- `callout` (emoji icons are rendered)
- `code` (language-aware highlighting for known languages)
- `equation` (block equation rendering, rendered via Ankis MathJax)
- `image` (safe HTTP/HTTPS URLs only)
- `table` (from `table` + `table_row` children)
- `column_list`
- `column`
- `toggle` (rendered inline as nested `<details>`)
- `divider`

Notes:

- Unknown/unsupported block types are ignored by the renderer.
- Basic, Basic+Reversed, and Input card extraction is based on top-level `toggle` blocks.
- Optional cloze extraction is based on top-level marked `paragraph` blocks and recognized top-level cloze toggles.

## Supported rich-text formatting and highlights

The rich-text renderer supports:

- `bold`
- `italic`
- `underline`
- `strikethrough`
- `code` (inline code)
- `href` links (unsafe schemes like `javascript:` are dropped)
- inline `equation` (rendered via Ankis MathJax)

### Highlight annotations

The parser reads both Notion annotation fields:

- `color` -> rendered as class `highlight-<color>`
- `background_color` -> rendered as class `highlight-<background_color>_background`

Safety rule:

- Highlight values are accepted only when they contain lowercase letters and underscores (`[a-z_]+`).
- Invalid or empty values are treated as `default`.

### Highlight values with built-in stylesheet classes

These values have explicit CSS styles in the bundled card stylesheet:

- text colors: `default`, `gray`, `brown`, `orange`, `yellow`, `green`, `blue`, `purple`, `pink`, `red`
- background colors: `gray_background`, `brown_background`, `orange_background`, `yellow_background`, `green_background`, `blue_background`, `purple_background`, `pink_background`, `red_background`

For advanced cloze toggle detection, only the toggle block background color `gray_background` qualifies. A toggle with the `gray` text color is not converted to a cloze card for this reason.

## Cloze marker highlighting

Cloze parsing treats text as a cloze marker when either condition is true:

- `annotations.color == "<color>_background"`
- `annotations.background_color == "<color>"`

The supported colors are yellow, green, blue, purple, pink, orange, red, and
brown, mapped to cloze numbers `c1` through `c8` respectively.

Only marked segments are converted into cloze markup with the color's fixed number.
Consecutive marked rich-text fragments are merged into one cloze, even when Notion splits inline math into separate `equation` items.
Inline equations inside the cloze field are rendered with Anki MathJax inline delimiters (`\(...\)`).
