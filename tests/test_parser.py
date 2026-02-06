"""Tests for Notion block parsing into toggle card payloads."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.notion_client import NotionBlock
from anki_notion_integration.parser import parse_page_to_cards, render_blocks, render_rich_text


def _block(
    block_id: str,
    block_type: str,
    payload: dict,
    *,
    children: tuple[NotionBlock, ...] = (),
    has_children: bool | None = None,
) -> NotionBlock:
    """Create a minimal test `NotionBlock` payload."""
    raw = {
        "id": block_id,
        "type": block_type,
        block_type: payload,
    }
    return NotionBlock(
        block_id=block_id,
        block_type=block_type,
        has_children=bool(children) if has_children is None else has_children,
        parent_id=None,
        parent_type=None,
        raw=raw,
        children=children,
    )


def _text_item(
    text: str,
    *,
    annotations: dict | None = None,
    href: str | None = None,
) -> dict:
    """Create a rich-text item for tests."""
    return {
        "type": "text",
        "text": {"content": text},
        "plain_text": text,
        "href": href,
        "annotations": annotations
        or {
            "bold": False,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        },
    }


class ParserTests(unittest.TestCase):
    """Validate parser rendering and toggle-card extraction rules."""

    def test_parse_page_to_cards_only_emits_root_toggles(self) -> None:
        nested_toggle = _block(
            "nested-toggle",
            "toggle",
            {"rich_text": [_text_item("Nested toggle")]},
            children=(_block("nested-p", "paragraph", {"rich_text": [_text_item("Nested body")]}),),
        )
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Root toggle")]},
            children=(
                _block("p1", "paragraph", {"rich_text": [_text_item("Parent body")]}),
                nested_toggle,
            ),
        )
        non_toggle = _block("paragraph-root", "paragraph", {"rich_text": [_text_item("Ignore me")]})

        payloads = parse_page_to_cards("page-1", [root_toggle, non_toggle])

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].notion_block_id, "root-toggle")
        self.assertIn("<summary>Nested toggle</summary>", payloads[0].back_html)
        self.assertNotIn("nested-toggle", [payload.notion_block_id for payload in payloads])
        self.assertTrue(payloads[0].content_hash)

    def test_render_rich_text_supports_annotations_links_and_equations(self) -> None:
        rendered = render_rich_text(
            [
                _text_item(
                    "Styled",
                    annotations={
                        "bold": True,
                        "italic": True,
                        "strikethrough": True,
                        "underline": True,
                        "code": True,
                        "color": "blue_background",
                    },
                    href="https://example.com",
                ),
                {
                    "type": "equation",
                    "equation": {"expression": r"\text{E = mc^2}"},
                    "annotations": {
                        "bold": False,
                        "italic": False,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "default",
                    },
                    "href": None,
                },
                _text_item("BadLink", href="javascript:alert(1)"),
            ]
        )

        self.assertIn('<span class="highlight-blue_background">', rendered)
        self.assertIn('<a href="https://example.com">', rendered)
        self.assertIn("<code>", rendered)
        self.assertIn(r'<span class="notion-equation">\(\text{E = mc^2}\)</span>', rendered)
        self.assertNotIn('href="javascript:alert(1)"', rendered)

    def test_render_rich_text_supports_foreground_and_background_highlights(self) -> None:
        rendered = render_rich_text(
            [
                _text_item(
                    "DualHighlight",
                    annotations={
                        "bold": False,
                        "italic": False,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "orange",
                        "background_color": "blue",
                    },
                )
            ]
        )

        self.assertIn('class="highlight-orange"', rendered)
        self.assertIn('class="highlight-blue_background"', rendered)

    def test_render_blocks_coalesces_list_items(self) -> None:
        blocks = [
            _block("b1", "bulleted_list_item", {"rich_text": [_text_item("One")]}),
            _block("b2", "bulleted_list_item", {"rich_text": [_text_item("Two")]}),
            _block("n1", "numbered_list_item", {"rich_text": [_text_item("Three")]}),
            _block("n2", "numbered_list_item", {"rich_text": [_text_item("Four")]}),
        ]

        rendered = render_blocks(blocks)

        self.assertEqual(rendered.count("<ul>"), 1)
        self.assertEqual(rendered.count("</ul>"), 1)
        self.assertEqual(rendered.count("<ol>"), 1)
        self.assertEqual(rendered.count("</ol>"), 1)
        self.assertIn("<li>One</li><li>Two</li>", rendered)
        self.assertIn("<li>Three</li><li>Four</li>", rendered)

    def test_render_blocks_handles_code_equation_quote_and_callout(self) -> None:
        blocks = [
            _block(
                "code-1",
                "code",
                {
                    "language": "Java",
                    "rich_text": [_text_item('if (x < 1) { return "&"; }')],
                },
            ),
            _block(
                "eq-1",
                "equation",
                {"expression": r"\text{Block Equation}"},
            ),
            _block("quote-1", "quote", {"rich_text": [_text_item("Quoted text")]}),
            _block(
                "callout-1",
                "callout",
                {
                    "icon": {"type": "emoji", "emoji": "📌"},
                    "rich_text": [_text_item("Callout text")],
                },
            ),
        ]

        rendered = render_blocks(blocks)

        self.assertIn('<code class="language-java">', rendered)
        self.assertIn('<span class="notion-syn-keyword">if</span>', rendered)
        self.assertIn('<span class="notion-syn-string">&quot;&amp;&quot;</span>', rendered)
        self.assertIn(r'<div class="notion-block-equation">\[\text{Block Equation}\]</div>', rendered)
        self.assertIn("<blockquote><p>Quoted text</p></blockquote>", rendered)
        self.assertIn('<p class="notion-callout-icon">📌</p>', rendered)

    def test_render_blocks_renders_heading_levels_1_2_3(self) -> None:
        blocks = [
            _block("h1", "heading_1", {"rich_text": [_text_item("Heading One")]}),
            _block(
                "h2",
                "heading_2",
                {"rich_text": [_text_item("Heading Two")]},
                children=(_block("h2-p", "paragraph", {"rich_text": [_text_item("Under heading two")]}),),
            ),
            _block("h3", "heading_3", {"rich_text": [_text_item("Heading Three")]}),
        ]

        rendered = render_blocks(blocks)

        self.assertIn("<h1>Heading One</h1>", rendered)
        self.assertIn("<h2>Heading Two</h2>", rendered)
        self.assertIn("<p>Under heading two</p>", rendered)
        self.assertIn("<h3>Heading Three</h3>", rendered)

    def test_render_blocks_code_fallback_for_unknown_language(self) -> None:
        blocks = [
            _block(
                "code-2",
                "code",
                {
                    "language": "unknownlang",
                    "rich_text": [_text_item('function hello() { return "<ok>"; }')],
                },
            )
        ]

        rendered = render_blocks(blocks)

        self.assertIn('<code class="language-unknownlang">', rendered)
        self.assertIn("function hello() { return &quot;&lt;ok&gt;&quot;; }", rendered)
        self.assertNotIn("notion-syn-keyword", rendered)

    def test_render_blocks_normalizes_language_aliases(self) -> None:
        blocks = [
            _block(
                "code-3",
                "code",
                {
                    "language": "c++",
                    "rich_text": [_text_item("int main() { return 0; }")],
                },
            )
        ]

        rendered = render_blocks(blocks)

        self.assertIn('<code class="language-cpp">', rendered)
        self.assertIn('<span class="notion-syn-type">int</span>', rendered)

    def test_render_blocks_renders_table_with_header_semantics(self) -> None:
        table = _block(
            "table-1",
            "table",
            {
                "table_width": 3,
                "has_column_header": True,
                "has_row_header": True,
            },
            children=(
                _block(
                    "table-row-1",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Header 1")],
                            [_text_item("Header 2")],
                            [_text_item("Header 3")],
                        ]
                    },
                ),
                _block(
                    "table-row-2",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Row 1 Header")],
                            [_text_item("R1C2")],
                            [_text_item("R1C3")],
                        ]
                    },
                ),
            ),
        )

        rendered = render_blocks([table])

        self.assertIn("<table><thead><tr>", rendered)
        self.assertIn('<th scope="col">Header 1</th>', rendered)
        self.assertIn('<th scope="row">Row 1 Header</th>', rendered)
        self.assertIn("<td>R1C2</td>", rendered)

    def test_render_blocks_renders_image_figure(self) -> None:
        image_block = _block(
            "image-1",
            "image",
            {
                "type": "external",
                "external": {"url": "https://example.com/test.png"},
                "caption": [_text_item("Figure caption")],
            },
        )

        rendered = render_blocks([image_block])

        self.assertIn('<figure class="notion-image">', rendered)
        self.assertIn('src="https://example.com/test.png"', rendered)
        self.assertIn('<figcaption>Figure caption</figcaption>', rendered)
        self.assertIn('alt="Figure caption"', rendered)

    def test_render_blocks_drops_unsafe_image_url(self) -> None:
        image_block = _block(
            "image-unsafe",
            "image",
            {
                "type": "external",
                "external": {"url": "javascript:alert(1)"},
                "caption": [_text_item("Unsafe image")],
            },
        )

        rendered = render_blocks([image_block])

        self.assertNotIn("<img", rendered)
        self.assertIn("<figcaption>Unsafe image</figcaption>", rendered)

    def test_render_blocks_mermaid_code_uses_sync_placeholder(self) -> None:
        blocks = [
            _block(
                "code-mermaid",
                "code",
                {
                    "language": "mermaid",
                    "rich_text": [_text_item("graph TD\nA --> B")],
                    "caption": [_text_item("Mermaid caption")],
                },
            )
        ]

        rendered = render_blocks(blocks)

        self.assertIn('<figure class="notion-mermaid">', rendered)
        self.assertIn('class="notion-mermaid-source"', rendered)
        self.assertIn('class="language-mermaid"', rendered)
        self.assertIn("graph TD", rendered)
        self.assertIn("<figcaption>Mermaid caption</figcaption>", rendered)

    def test_render_blocks_renders_column_list_with_explicit_ratios(self) -> None:
        column_list = _block(
            "columns-1",
            "column_list",
            {},
            children=(
                _block(
                    "column-left",
                    "column",
                    {"width_ratio": 0.25},
                    children=(_block("left-p", "paragraph", {"rich_text": [_text_item("Left")]},),),
                ),
                _block(
                    "column-right",
                    "column",
                    {"width_ratio": 0.75},
                    children=(_block("right-p", "paragraph", {"rich_text": [_text_item("Right")]},),),
                ),
            ),
        )

        rendered = render_blocks([column_list])

        self.assertIn('<div class="notion-columns">', rendered)
        self.assertIn('data-column-ratio="0.250000"', rendered)
        self.assertIn('data-column-ratio="0.750000"', rendered)
        self.assertIn("style=\"--notion-column-width: 25.000000%;\"", rendered)
        self.assertIn("style=\"--notion-column-width: 75.000000%;\"", rendered)
        self.assertIn("<p>Left</p>", rendered)
        self.assertIn("<p>Right</p>", rendered)

    def test_render_blocks_infers_missing_column_ratios(self) -> None:
        column_list = _block(
            "columns-2",
            "column_list",
            {},
            children=(
                _block(
                    "column-1",
                    "column",
                    {"width_ratio": 0.5},
                    children=(_block("c1-p", "paragraph", {"rich_text": [_text_item("One")]},),),
                ),
                _block(
                    "column-2",
                    "column",
                    {},
                    children=(_block("c2-p", "paragraph", {"rich_text": [_text_item("Two")]},),),
                ),
                _block(
                    "column-3",
                    "column",
                    {},
                    children=(_block("c3-p", "paragraph", {"rich_text": [_text_item("Three")]},),),
                ),
            ),
        )

        rendered = render_blocks([column_list])

        self.assertIn('data-column-ratio="0.500000"', rendered)
        self.assertEqual(rendered.count('data-column-ratio="0.250000"'), 2)
        self.assertIn("style=\"--notion-column-width: 50.000000%;\"", rendered)
        self.assertEqual(rendered.count("style=\"--notion-column-width: 25.000000%;\""), 2)

    def test_render_blocks_renders_direct_column_children(self) -> None:
        column_block = _block(
            "column-direct",
            "column",
            {"width_ratio": 0.5},
            children=(_block("column-direct-p", "paragraph", {"rich_text": [_text_item("Direct")]},),),
        )

        rendered = render_blocks([column_block])

        self.assertEqual(rendered, '<div class="notion-column"><p>Direct</p></div>')


if __name__ == "__main__":
    unittest.main()
