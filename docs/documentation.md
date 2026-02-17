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

- Shows toggle-derived cards for the selected page.
- Lets you exclude individual cards from sync.
- Lets you reset card-type overrides back to default behavior.

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

- Created from top-level paragraph blocks, not from toggles
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

Unsupported or unknown block types are ignored in rendered card content.

## 6. Cloze parsing rules

Cloze cards are generated only when cloze parsing is enabled.

Rules:

- Source scope: top-level paragraph blocks only.
- A paragraph must contain highlighted cloze markers (yellow highlight).
- Only highlighted text becomes `{{c1::...}}`.
- Non-highlighted text remains normal text.

Optional `Extra` behavior:

- If the very next top-level paragraph starts with `Extra:`, it is used as the cloze `Extra` field.
- `Extra:` is case-insensitive.
- The `Extra:` prefix is removed.
- Formatting in the remaining text is preserved.
- This extra paragraph is consumed and not turned into another cloze card.

Nested cloze-like content inside toggles is ignored for cloze card generation.

## 7. Image Occlusion candidate rules

The Image Occlusion list contains:

- Image blocks outside toggle trees

The list excludes:

- Image blocks inside toggles

Images are launched manually into Image Occlusion workflow; they are not automatically converted during normal sync.

## 8. Sync behavior details

- Sync processes only selected pages.
- Existing note mappings are reused where possible.
- If source content is unchanged, unnecessary updates are skipped.
- If a mapped card changes card type, the mapped note is converted to the new type on sync.
- Excluded cards remain excluded.
- AI variant-enabled cards use a deterministic UTC-day variant index (`block id + direction + date`) so daily variant selection is consistent across synced devices.
- AI TTS-enabled cards attempt front-side autoplay and render a manual `Play audio` fallback button if client autoplay is blocked.

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
6. For cloze cards, confirm yellow highlight markers are present.
7. For image occlusion, confirm images are outside toggles.
