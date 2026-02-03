# Plan

Implement `src/anki_notion_integration/parser.py` to convert Notion API block trees (from `NotionClient.get_page_content(...)`) into deterministic HTML for the “Notion Toggle” note type, using `docs/Essentials.html` as the reference for required blocks/styles and updating the card CSS for reliable Anki rendering.

## Scope
- In: Implementing `src/anki_notion_integration/parser.py`, updating `src/anki_notion_integration/docs/Notion_Card_Stylesheet.css`, and adding focused unit tests for parsing/rendering.
- Out: Full Notion→Anki sync engine, DB mapping/persistence logic beyond what the parser needs, and support for non-MVP Notion block types (tables, images, etc.).

## Action items
- [ ] Inspect `docs/Essentials.html` to catalog the required block types and the expected HTML structure/classes for each (annotations, lists, code, quote, callout, equations).
- [ ] Validate whether all Notion equations can be rendered robustly in Anki with MathJax; if not, plan and document shipping KaTeX assets + a minimal render hook for both inline and block equations.
- [ ] Update `src/anki_notion_integration/docs/Notion_Card_Stylesheet.css` to be “Anki-safe” (no page margins/max-width, predictable typography, and light/dark support via `prefers-color-scheme` and `body.nightMode`).
- [ ] Define the parser’s output payload shape (e.g., a `ToggleCardPayload` dataclass) and deterministic rendering rules (HTML escaping, stable nesting order, list coalescing).
- [ ] Implement `src/anki_notion_integration/parser.py` helpers for: root-toggle extraction (top-level toggles only), rich-text rendering (annotations, links, colors, inline equations), and block rendering for the required MVP blocks (including nesting).
- [ ] Ensure nested toggles render inline in the parent toggle’s back HTML (e.g., `<details><summary>…</summary>…</details>`) but do not emit separate card payloads for them.
- [ ] Add `tests/test_parser.py` with unit tests built from minimal Notion API payload fixtures to validate HTML output (especially equations, escaping, and list coalescing).
- [ ] Validate by running the test suite (`python -m unittest discover -s tests`) in the project venv / Anki Python environment and iterating until green.
- [ ] Capture edge cases and fallbacks (unknown blocks, empty rich_text, nested lists/toggles) and document any intentionally unsupported behavior.

## Open questions
- None.
