# Noteck: User Documentation

This document explains how to use Noteck, what content it can parse, and how cards are generated.

## 1. What the add-on does

The add-on syncs supported content from selected Notion pages into Anki notes.

- You choose which pages are synced.
- The add-on parses supported Notion blocks into card content.
- Existing mapped notes are updated on later syncs.

Sync is one-way: changes flow from Notion to Anki.

## 2. Setup and prerequisites

You need:

- Anki installed
- A Notion integration key
- The relevant Notion pages shared with your integration

Setup flow:

1. Install the add-on and restart Anki.
2. Open the `Notion` window.
3. In `Settings`, enter your Notion API key.
4. In Notion, share pages with your integration.
5. In `Pages`, select pages to sync.
6. Run `Sync Notion now` from the `Settings` page.

## 3. Main screens

### Pages

- Shows your accessible Notion pages in a tree.
- Lets you choose which pages are synced.
- Lets you set a default card type per page (or inherit the global default).

### Cards

- Shows toggle-derived and paragraph-cloze cards for the selected page.
- Lets you exclude individual cards from sync.
- Lets you reset card-type overrides for selectable toggle cards back to default behavior.

### Image Occlusion

- Shows image candidates found on the selected page.
- Lets you open images in Image Occlusion workflow manually.
- If Image Occlusion is not available, launching is unavailable.

### Settings

- Stores your Notion API key.
- Controls parser and sync behavior, including default card type and cloze parsing.
- Includes manual sync action.

## 4. Card generation model

### 4.1 Toggle-based cards

Top-level Notion toggle blocks are the main source of cards.

- Toggle title becomes the card front.
- Toggle content becomes the card back.
- Nested toggles are rendered inside the parent card back.
- Nested toggles are not created as separate cards by themselves.

### 4.2 Available card types

#### Basic

- Front: toggle title
- Back: toggle content

#### Basic (Reversed)

- Same source content as Basic
- Creates forward and reverse review direction

#### Input

- Front and back come from the toggle, like Basic
- Includes typed-answer behavior using parsed answer text from the toggle body

#### Cloze

- Created from top-level paragraph blocks or recognized advanced cloze toggles
- Requires highlighted cloze markers (see cloze rules below)

## 5. Supported Notion content

When a toggle is converted to card content, the add-on supports these block types in the rendered output:

- Paragraph
- Heading 1, Heading 2, Heading 3
- Bulleted lists
- Numbered lists
- Quote
- Callout (including emoji icon)
- Code block (with language-aware formatting where possible)
- Mermaid code block (diagram workflow support)
- Equation block
- Table
- Column layouts
- Divider
- Nested toggle rendering
- Image blocks with safe HTTP/HTTPS image URLs

Rich-text formatting support includes:

- Bold
- Italic
- Underline
- Strikethrough
- Inline code
- Links
- Inline equations
- Foreground and background highlight colors

Block-level foreground and background colors are rendered for paragraphs, Heading 1–3,
bulleted and numbered list items, quotes, callouts, and nested toggles. Colored list-item
surfaces include their bullet or number, while quote backgrounds retain square corners.

For toggle-derived cards, a background color on the top-level toggle colors the complete
Notion card surface in Anki. A foreground-only toggle color applies to the card title instead.
Child blocks with their own background color remain visible above the card surface.
Code blocks, Mermaid diagrams, and tables use translucent surfaces on root-colored cards so
the root backdrop remains visible; their normal uncolored-card appearance is unchanged.
The same card-surface behavior applies to cloze source blocks. A top-level paragraph-cloze
foreground colors its visible text, while an advanced cloze toggle's title and foreground
remain hidden. Inside advanced clozes, configured marker backgrounds create deletions and
lose their visual marker color; backgrounds excluded from cloze parsing render as ordinary
block colors.
The managed `Notion Block ID` and `Notion Card Background` metadata fields are collapsed in Anki's note editor by default.
After upgrading, Noteck performs one successful parser refresh of existing toggle-derived
and cloze cards so color changes are applied even when their Notion edit timestamps have not
changed. The bundled cloze template must be updated to display the new background field;
custom templates are preserved and can add the same managed class manually.

Unsupported or unknown block types are ignored in rendered card content.

## 6. Cloze parsing rules

Cloze cards, including toggle-based cloze cards, are generated only when cloze parsing is enabled.

Rules:

- Source scope: top-level paragraph blocks only.
- A paragraph can use a supported marker in part of its rich text. A block background alone is not a top-level cloze marker because it provides no context.
- An inline marker hides only its highlighted text using the marker color's fixed `c1` through `c8` number. Block backgrounds remain supported inside advanced cloze toggles.
- A background on the source paragraph or advanced toggle colors the complete card surface;
  it does not create a deletion. A foreground on a source paragraph styles its visible text.
- Non-highlighted text remains normal text.

Optional `Extra` behavior:

- If the very next top-level paragraph starts with `Extra:`, it is used as the cloze `Extra` field.
- `Extra:` is case-insensitive.
- The `Extra:` prefix is removed.
- Formatting in the remaining text is preserved.
- This extra paragraph is consumed and not turned into another cloze card.

Nested cloze-like content inside toggles is ignored for cloze card generation.

Toggle-based cloze behavior:

- Uses the same cloze parsing option as paragraph cloze cards.
- Top-level toggles whose title starts with `[cloze]` become cloze cards.
- Top-level toggles with a gray background (`gray_background`) become cloze cards only when gray-toggle cloze parsing is enabled; gray text color alone does not qualify.
- Child blocks render into `Text` with cloze-aware rich-text parsing.
- A configured background on a supported child block hides its complete direct text while retaining its HTML structure: paragraphs, headings, nested toggles, bulleted/numbered list items, quotes, and callouts. A marked callout keeps its icon, container, and child layout; its direct and descendant text uses that marker's cloze number, unless a nested callout has its own configured block background.
- Tables do not have a block color. A cell becomes a whole-cell cloze only when all of its non-empty rich-text fragments use the same configured marker; its `td`/`th` structure is retained and uses the same yellow styling as Anki cloze markers. Partial cell highlighting keeps the normal inline-cloze behavior.
- Direct child paragraphs starting with `Extra:` render into `Extra`; `[extra]` toggles are rendered normally in `Text`.
- Marker background colors map to fixed cloze numbers: yellow -> c1, green -> c2, blue -> c3, purple -> c4, pink -> c5, orange -> c6, red -> c7, brown -> c8.
- Selected marker background colors are removed from exported HTML while normal formatting is preserved; inline and block colors excluded in Settings remain ordinary styling.
- Advanced containers without marker colors remain deterministic cloze payloads and do not fall back to another card type.
- Code, equation, image, table/container, column, and divider blocks cannot carry a standalone complete block color in the current Notion API shape. When nested inside a marked callout, their textual content is hidden with the callout's cloze number while their supported surrounding structure remains.

## 7. Image Occlusion candidate rules

The Image Occlusion list contains:

- Image blocks outside toggle trees

The list excludes:

- Image blocks inside toggles

Images are launched manually into Image Occlusion workflow; they are not automatically converted during normal sync.

## 8. Sync behavior details

- Sync processes only selected pages.
- Existing note mappings are reused where possible.
- Every run reads the selected pages' root blocks and recursively reads toggle
  contents, so descendant edits are detected even when an ancestor's Notion
  `last_edited_time` has not changed.
- Content hashes prevent unnecessary Anki writes when parsed card content is unchanged.
- If a mapped card changes card type, the mapped note is converted to the new type on sync.
- Excluded cards remain excluded.

## 9. Expected behavior and limitations

- Notion is the source of truth for generated cards.
- The add-on does not perform full two-way content editing workflows.
- Pages that are inaccessible to the integration in Notion cannot be synced.
- Image Occlusion depends on an available Image Occlusion workflow in your Anki environment.

## 10. Troubleshooting checklist

If sync does not work as expected:

1. Confirm your Notion API key is set.
2. Confirm the page is shared with your Notion integration.
3. Confirm the page is selected in `Pages`.
4. Run manual sync again.
5. Check that your content uses supported Notion blocks.
6. For cloze cards, confirm a supported marker color is present; toggle clozes may use a `[cloze]` title or, when enabled, a gray block background.
7. For image occlusion, confirm images are outside toggles.
