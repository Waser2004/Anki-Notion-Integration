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
- `callout` (emoji, uploaded, and external icons are rendered)
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

For cloze detection, the parser accepts both REST API names such as
`yellow_background` and enhanced-Markdown aliases such as `yellow_bg`.

Safety rule:

- Highlight values are accepted only when they contain lowercase letters and underscores (`[a-z_]+`).
- Invalid or empty values are treated as `default`.

### Highlight values with built-in stylesheet classes

These values have explicit CSS styles in the bundled card stylesheet:

- text colors: `default`, `gray`, `brown`, `orange`, `yellow`, `green`, `blue`, `purple`, `pink`, `red`
- background colors: `gray_background`, `brown_background`, `orange_background`, `yellow_background`, `green_background`, `blue_background`, `purple_background`, `pink_background`, `red_background`

For advanced cloze toggle detection, only the toggle block background color `gray_background` qualifies. A toggle with the `gray` text color is not converted to a cloze card for this reason.

For every cloze source, a validated root background is exported through the
managed `Notion Card Background` field and colors the complete Anki card
surface. A top-level paragraph foreground styles its visible cloze text. An
advanced toggle foreground is not rendered because its title remains a source
marker rather than card content.

## Cloze marker highlighting

Cloze parsing treats text as a cloze marker when either condition is true:

- `annotations.color == "<color>_background"`
- `annotations.background_color == "<color>"`

The supported colors are yellow, green, blue, purple, pink, orange, red, and
brown, mapped to cloze numbers `c1` through `c8` respectively.

Only marked segments are converted into cloze markup with the color's fixed number.
Consecutive marked rich-text fragments are merged into one cloze, even when Notion splits inline math into separate `equation` items.
Inline equations inside the cloze field are rendered with Anki MathJax inline delimiters (`\(...\)`).

### Block-level markers

Notion returns a whole-block background through the block payload's `color`
field. A configured value of `<color>_background` creates one cloze marker for
the block's complete text; foreground-only values such as `yellow` do not
trigger cloze conversion. A full block marker wins over any inline marker inside that
same block, preventing nested deletions.

This replacement occurs only when the background color is enabled in the
cloze-marker setting. An excluded block background is visual styling and keeps
the same semantic element and block-color classes used by basic cards.

Within an advanced cloze toggle, this behavior applies while retaining the
recognizable renderer structure for `paragraph`, `heading_1` through
`heading_3`, `bulleted_list_item`, `numbered_list_item`, `quote`, `callout`,
and nested `toggle` blocks. A marked callout retains its icon, container, and
child layout while its direct and descendant text uses the callout's cloze
number. An explicitly marked nested callout starts its own cloze scope.

The REST shape used by this add-on has no color field for `table` or
`table_row`. A cell is treated as fully colored only when every non-empty
rich-text fragment resolves to the same configured background marker. Its
content becomes one cloze, its `td` or `th` remains in place, and it reuses the
Anki `.cloze` yellow background through the renderer's
`notion-whole-cell-cloze` class. A partially highlighted or mixed-color cell
does not receive that class and continues to use normal inline cloze rendering.
A whole-cell Markdown color
excluded from marker parsing remains visible on the cell surface. Code,
equation, image, column,
and divider blocks cannot currently carry a standalone complete block color.
When nested in a marked callout, their textual content is hidden with the
callout's cloze number while their supported renderer structure remains.
