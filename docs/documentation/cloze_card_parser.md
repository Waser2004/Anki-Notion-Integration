# Cloze card parser

This document describes the developer-facing architecture and behavior of
`src/Noteck/modules/parser/cloze_card_parser.py`. It complements `parser.md`,
which is the shorter reference for all card types.

## Responsibilities and boundaries

`ClozeCardParser` has three responsibilities:

1. Recognize the two supported cloze source conventions.
2. Convert those sources into deterministic `ToggleCardPayload` objects.
3. Validate the generated Anki cloze markup before sync writes a note.

It deliberately does not fetch Notion blocks, choose sync candidates, honor the
database exclusion flag, or write to Anki. Those concerns live in the Notion
client, `parser/parser.py`, and `modules/sync.py` respectively.

The main data flow is:

```text
NotionBlock trees
    -> parse_page_to_cards(...)
        -> ClozeCardParser source recognition
        -> normal or advanced rendering
        -> ToggleCardPayload + deterministic content hash
    -> sync candidate/exclusion handling
    -> ClozeCardParser.validate(...)
    -> Anki create/update
```

Validation occurs in `_sync_one_payload()` immediately before any Anki lookup,
creation, or update. Parsing and validation are intentionally separate: an
advanced container without a marker still produces a stable payload, but the
sync boundary rejects it because Anki cannot generate a card from it.

## Entry points

### `parse_page_to_cards()`

`parser/parser.py` is the public coordinator. It materializes the supplied
block iterable once, parses root toggles first, and appends paragraph clozes
afterwards. Output order is therefore root-toggle cards in source order,
followed by paragraph clozes in source order; it is not the original mixed
block order.

The relevant feature flags are:

- `enable_cloze`: enables top-level paragraph clozes and toggle-based clozes.
- `enable_gray_toggle_cloze`: controls whether a `gray_background` toggle is
  recognized as an advanced cloze container. It defaults to true.
- `include_block_ids`: filters source card blocks before rendering. For a
  normal cloze, it filters the cloze paragraph, not its adjacent `Extra:`
  paragraph.

Recognition and conversion are distinct. A root toggle recognized as an
advanced cloze container is never passed to `BasicCardParser`. If cloze
parsing is disabled, the recognized toggle is skipped rather than falling
back to Basic, Basic (Reversed), or Input. Consequently, card-type overrides do
not apply to recognized advanced cloze toggles.

### `ClozeCardParser` methods

- `is_advanced_container()` recognizes eligible root toggle conventions. The
  method itself only checks block shape and annotations; root-level scope is
  enforced by the coordinator.
- `parse_top_level_paragraphs()` creates color-numbered paragraph cards and
  consumes an optional adjacent `Extra:` paragraph.
- `parse_advanced()` renders one advanced toggle's children into `Text` and
  `Extra`.
- `validate()` checks the final payload and Anki cloze syntax.

## Normal paragraph clozes

A normal source is a top-level `paragraph` containing at least one rich-text
marker. A block background by itself is not a top-level cloze marker because it
would create a deletion without surrounding context. Rich-text
markers use either supported Notion representation:

- `annotations.color == "<color>_background"`
- `annotations.background_color == "<color>"`

Normal paragraphs use the same fixed color-to-cloze-number mapping as advanced
toggle clozes: yellow through brown map to `c1` through `c8`.
Adjacent marked rich-text items are merged into a single deletion. An unmarked
item ends that run, so two highlighted regions separated by ordinary text
become two separate `c1` deletions. Empty rendered fragments are ignored and do
not themselves create a boundary.

Block-level markers remain supported for blocks inside advanced cloze
containers, where the surrounding container supplies the card context.

Normal cloze rendering is intentionally lighter than the shared HTML renderer:

- text is HTML-escaped but rich-text styles and links are not rendered;
- inline equations become escaped Anki MathJax `\(...\)` fragments;
- the paragraph is stored without a wrapping `<p>` element;
- unmarked content remains outside cloze markup.

The no-wrapper rule remains true for default and background-colored source
paragraphs. A validated foreground block color adds the shared semantic `<p>`
wrapper so the visible cloze text receives the same foreground styling as a
basic-card title. A source background is stored separately as the card-surface
field rather than being duplicated on the paragraph.

These rendering limits apply to the inline-marker path. A block-level paragraph
marker uses the shared rich-text item renderer inside its single deletion so
the revealed answer retains safe formatting and links.

For example, rich-text fragments `The `, highlighted `area`, ` is `, and a
highlighted inline equation `pi r^2` produce:

```text
The {{c1::area}} is {{c1::\(pi r^2\)}}
```

### Adjacent `Extra:` paragraph

After accepting a source paragraph, the parser inspects exactly the next
top-level block. If it is a paragraph whose combined plain text begins with
`Extra:` (case-insensitive, with surrounding prefix whitespace allowed), that
block supplies `Extra` and is consumed.

The prefix is removed from the first text rich-text item that contains it. The
remaining items are rendered with the shared rich-text HTML renderer, so their
formatting is preserved. A colored Extra paragraph uses the shared block
renderer as well, retaining its foreground or background class. The parser does
not search past an intervening block.
A consumed extra paragraph cannot also become its own cloze card, even if it
contains a supported marker. An `include_block_ids` filter does not prevent a
selected source paragraph from reading its adjacent extra block.

## Advanced toggle clozes

An advanced source is a root `toggle` matching either convention:

- its plain-text title starts with `[cloze]`, ignoring leading whitespace and
  case; or
- its block payload has `color == "gray_background"` and gray-toggle parsing
  is enabled.

The title is only a recognition marker. It is not included in `Text`. A gray
foreground text annotation is not equivalent to a gray block background.
The root toggle background is stored as the complete card surface, including
the gray background used for container recognition. A root foreground has no
visible target because the title remains excluded.

All direct child blocks normally render into `Text` through the shared block
renderer. This preserves its supported paragraphs, headings, lists, tables,
columns, images, code, equations, callouts, and nested toggles. The parser
injects a cloze-aware rich-text renderer, so marker conversion also works in
rich text nested inside those supported child structures.

For colorable renderer-supported blocks (`paragraph`, `heading_1` through
`heading_3`, `bulleted_list_item`, `numbered_list_item`, `quote`, `callout`,
and nested `toggle`), a configured `<color>_background` payload replaces the
complete direct text with one marker while retaining the block's recognizable
HTML. A colored callout keeps its icon, container, and child layout while its
direct and descendant text uses the callout's cloze number. An explicitly
colored nested callout starts its own cloze scope. The block-level marker wins
over inner inline markers, preventing nested deletions.
If a block background is not among the configured marker colors, the override
does not apply and the shared renderer keeps its normal semantic color classes.

Notion's block API has no table-cell color field. During cloze sync, the add-on
also reads the page's enhanced Markdown representation and overlays its cell,
row, and column colors onto the matching block-API table rows. Table cells are
treated as block-level only when every non-empty rich-text fragment uses the
same effective configured marker. The cell contents become one deletion while
their `td`/`th` and header semantics remain. CSS highlights only the cell
containing Anki's dynamically rendered hidden `.cloze` child, and keeps the
child text itself transparent.
Whole-cell colors excluded from the marker setting remain visible as
theme-aware background classes on their `td` or `th`, including empty cells.
Partial or mixed-color cells use the existing inline behavior. Code, equation,
image, table/container, column, and divider blocks have no standalone complete
block-color strategy. If they are descendants of a marked callout, their
textual content is hidden with the callout's cloze number while their supported
HTML structure is retained.

A direct child paragraph whose text starts with `Extra:` is rendered into
`Extra`, with the case-insensitive prefix removed. Unlike normal paragraph
clozes, the extra paragraph is inside the advanced cloze toggle rather than the
next top-level block. Toggles titled `[extra]` are rendered normally in `Text`.

### Color-to-number mapping

Advanced mode assigns stable numbers by color rather than encounter order:

| Marker background | Anki deletion |
| --- | --- |
| yellow | `c1` |
| green | `c2` |
| blue | `c3` |
| purple | `c4` |
| pink | `c5` |
| orange | `c6` |
| red | `c7` |
| brown | `c8` |

Both `annotations.color = "<color>_background"` and
`annotations.background_color = "<color>"` (with an optional `_background`
suffix) are recognized. Consecutive fragments with the same resolved number
are merged. A change of number, or a transition to/from unmarked text, starts a
new run. Repeated numbers and gaps are valid.

For a supported block payload, only `color = "<color>_background"` is a
block-level marker. A foreground `color = "<color>"` does not trigger cloze
conversion.

Before the shared rich-text renderer runs, the annotation that acted as a
marker is reset to `default`. This prevents the marker background from leaking
into card HTML while retaining bold, italic, links, inline equations, and
unrelated foreground colors.

## Payload contract and determinism

Both modes produce a `ToggleCardPayload` with:

- `card_type = "cloze"`
- `model_name = "Notion (Cloze)"`
- fields `Text`, `Extra`, `Notion Block ID`, and `Notion Card Background`
- the source block's `last_edited_time`, normalized to an optional string
- a content hash computed from page ID, source block ID, card type, model name,
  and the complete fields mapping

The cloze back template conditionally renders its styled extra container only
when `Extra` is populated. Empty extras therefore add no padding or height to
the back of the card.

The source block ID is the paragraph ID for normal cards and the outer toggle
ID for advanced cards. The extra paragraph does not receive a
separate mapping. Deterministic rendering and hashing let sync distinguish real
content changes from unchanged notes.

## Validation contract

`validate()` returns an immutable `ClozeValidationResult`; it does not raise for
invalid card content. A valid payload must:

- have cloze card type and the registered `Notion (Cloze)` model name;
- contain a non-empty string `Text` field;
- contain at least one `{{cN::...}}` marker where `N` is a positive integer;
- have balanced opening and closing delimiters;
- hide non-empty text after HTML tags are stripped and entities decoded; and
- nest no deeper than three cloze deletions.

Stray `{{` sequences are treated as malformed cloze openings, and stray `}}`
sequences as unmatched closings. The validator checks the final rendered
string, which protects both parser-generated cards and payloads assembled by
other code paths. It does not validate the `Extra` field because Anki generates
cloze cards from `Text`.

## Sync-specific behavior worth knowing

Sync first shallow-fetches the page. Root toggles are recursively expanded only
when their mapping is new, changed, or missing in Anki. Normal paragraph clozes
come from the shallow top-level block list and need no recursive expansion.

Database exclusions are applied before parsing: excluded toggles are not
expanded, and excluded paragraph IDs are omitted with `include_block_ids`.
Validation failures are reported against the source block ID and do not produce
a partial Anki write.

Sync also stores internal revision markers for one-time cloze reparsing after
parser behavior changes and for changes to the gray-toggle option. If output
semantics change again, updating the relevant refresh revision in
`modules/sync.py` may be necessary so otherwise unchanged mapped notes are
regenerated.

The cloze note type uses `Notion Card Background` in both bundled template
wrappers. Model setup adds and collapses the field on existing note types but
does not overwrite custom HTML or CSS. The template version advertises the
bundled update when an older managed template is installed.

## Extension checklist

When changing cloze behavior:

1. Keep source recognition in `ClozeCardParser` and page-level orchestration in
   `parse_page_to_cards()`.
2. Use the shared block renderer for advanced content so supported block
   behavior remains consistent with toggle cards.
3. Decide explicitly whether a new annotation is a visual style, a cloze
   marker, or both, and remove only marker styling from exported HTML.
4. Preserve deterministic numbering and output ordering; changing either can
   change content hashes and update existing notes.
5. Add parser tests for run boundaries, escaping/formatting, extras, filtering,
   and invalid markup. Add sync tests when exclusion, refresh, or write-boundary
   behavior changes.
6. Consider bumping the cloze refresh revision when existing unchanged cards
   need their fields regenerated.
