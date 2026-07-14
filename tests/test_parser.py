"""Tests for Notion block parsing into toggle card payloads."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.notion_client import NotionBlock
from Noteck.modules.parser import (
    collect_image_occlusion_candidates,
    normalize_typed_answer,
    parse_page_to_cards,
    render_blocks,
    render_rich_text,
)


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


def _equation_item(
    expression: str,
    *,
    annotations: dict | None = None,
) -> dict:
    """Create an inline equation rich-text item for tests."""
    return {
        "type": "equation",
        "equation": {"expression": expression},
        "href": None,
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
        self.assertEqual(payloads[0].front_html, "<p>Root toggle</p>")
        self.assertIn("<summary>Nested toggle</summary>", payloads[0].back_html)
        self.assertNotIn("nested-toggle", [payload.notion_block_id for payload in payloads])
        self.assertTrue(payloads[0].content_hash)

    def test_parse_page_to_cards_include_block_ids_filters_toggle_payloads(self) -> None:
        first_toggle = _block(
            "toggle-1",
            "toggle",
            {"rich_text": [_text_item("First")]},
            children=(_block("p1", "paragraph", {"rich_text": [_text_item("One")]}),),
        )
        second_toggle = _block(
            "toggle-2",
            "toggle",
            {"rich_text": [_text_item("Second")]},
            children=(_block("p2", "paragraph", {"rich_text": [_text_item("Two")]}),),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [first_toggle, second_toggle],
            include_block_ids={"toggle-2"},
        )

        self.assertEqual([payload.notion_block_id for payload in payloads], ["toggle-2"])

    def test_parse_page_to_cards_include_block_ids_filters_cloze_payloads(self) -> None:
        cloze_a = _block(
            "cloze-a",
            "paragraph",
            {
                "rich_text": [
                    _text_item(
                        "Alpha",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    )
                ]
            },
        )
        cloze_b = _block(
            "cloze-b",
            "paragraph",
            {
                "rich_text": [
                    _text_item(
                        "Beta",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    )
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_a, cloze_b],
            enable_cloze=True,
            include_block_ids={"cloze-b"},
        )

        self.assertEqual([payload.notion_block_id for payload in payloads], ["cloze-b"])
        self.assertEqual(payloads[0].card_type, "cloze")

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

    def test_render_blocks_applies_foreground_and_background_colors_to_supported_blocks(self) -> None:
        """Every currently supported color-capable semantic block receives safe classes."""
        block_cases = (
            ("paragraph", "p"),
            ("heading_1", "h1"),
            ("heading_2", "h2"),
            ("heading_3", "h3"),
            ("quote", "blockquote"),
            ("callout", "div"),
            ("toggle", "details"),
        )
        for block_type, tag_name in block_cases:
            for color in ("blue", "red_background"):
                with self.subTest(block_type=block_type, color=color):
                    payload = {"rich_text": [_text_item("Colored")], "color": color}
                    rendered = render_blocks([_block("colored", block_type, payload)])
                    self.assertIn(f"<{tag_name}", rendered)
                    self.assertIn(f"notion-block-color-{color}", rendered)
                    if color.endswith("_background"):
                        self.assertIn("notion-block-color-background", rendered)

    def test_render_blocks_colors_list_items_without_splitting_list_sequences(self) -> None:
        blocks = [
            _block(
                "b1",
                "bulleted_list_item",
                {"rich_text": [_text_item("Blue")], "color": "blue_background"},
            ),
            _block("b2", "bulleted_list_item", {"rich_text": [_text_item("Default")]}),
            _block(
                "n1",
                "numbered_list_item",
                {"rich_text": [_text_item("Purple")], "color": "purple_background"},
            ),
            _block("n2", "numbered_list_item", {"rich_text": [_text_item("Default")]}),
        ]

        rendered = render_blocks(blocks)

        self.assertEqual(rendered.count("<ul>"), 1)
        self.assertEqual(rendered.count("<ol>"), 1)
        self.assertIn('<li class="notion-block-color notion-block-color-blue_background ', rendered)
        self.assertIn('<li class="notion-block-color notion-block-color-purple_background ', rendered)
        self.assertIn("<li>Default</li>", rendered)

    def test_render_blocks_ignores_unknown_block_colors(self) -> None:
        rendered = render_blocks(
            [_block("p1", "paragraph", {"rich_text": [_text_item("Safe")], "color": "url(bad)"})]
        )

        self.assertEqual(rendered, "<p>Safe</p>")

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

    def test_parse_page_to_cards_supports_basic_reversed_default_type(self) -> None:
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Root toggle")]},
            children=(_block("p1", "paragraph", {"rich_text": [_text_item("Parent body")]}),),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [root_toggle],
            default_card_type="basic_reversed",
            enable_cloze=False,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].card_type, "basic_reversed")
        self.assertIn("Front", payloads[0].fields)
        self.assertIn("Back", payloads[0].fields)

    def test_parse_page_to_cards_uses_root_toggle_background_for_all_toggle_models(self) -> None:
        """Root background colors become managed fields and affect content identity."""
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Root toggle")], "color": "brown_background"},
            children=(_block("p1", "paragraph", {"rich_text": [_text_item("Body")]}),),
        )

        for card_type in ("basic", "basic_reversed", "input"):
            with self.subTest(card_type=card_type):
                payload = parse_page_to_cards(
                    "page-1",
                    [root_toggle],
                    default_card_type=card_type,
                )[0]
                self.assertEqual(payload.fields["Notion Card Background"], "brown_background")
                self.assertEqual(payload.front_html, "<p>Root toggle</p>")

        default_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Root toggle")], "color": "default"},
            children=root_toggle.children,
        )
        default_payload = parse_page_to_cards("page-1", [default_toggle])[0]
        colored_payload = parse_page_to_cards("page-1", [root_toggle])[0]
        self.assertNotEqual(default_payload.content_hash, colored_payload.content_hash)

    def test_parse_page_to_cards_keeps_root_foreground_color_on_title_only(self) -> None:
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Blue title")], "color": "blue"},
        )

        payload = parse_page_to_cards("page-1", [root_toggle])[0]

        self.assertEqual(payload.fields["Notion Card Background"], "")
        self.assertIn("notion-block-color-blue", payload.front_html)

    def test_parse_page_to_cards_supports_input_default_type(self) -> None:
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Question")]},
            children=(_block("p1", "paragraph", {"rich_text": [_text_item("Answer: Blue!")]}),),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [root_toggle],
            default_card_type="input",
            enable_cloze=False,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].card_type, "input")
        self.assertEqual(payloads[0].fields["Expected Answer"], "Answer: Blue!")

    def test_parse_page_to_cards_input_expected_answer_preserves_equations(self) -> None:
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Question")]},
            children=(
                _block(
                    "p1",
                    "paragraph",
                    {
                        "rich_text": [
                            _text_item("Integral: "),
                            {
                                "type": "equation",
                                "equation": {"expression": r"\int_0^1 x^2 dx"},
                                "annotations": {
                                    "bold": False,
                                    "italic": False,
                                    "strikethrough": False,
                                    "underline": False,
                                    "code": False,
                                    "color": "default",
                                },
                            },
                        ]
                    },
                ),
                _block("eq1", "equation", {"expression": r"\frac{1}{3}"}),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [root_toggle],
            default_card_type="input",
            enable_cloze=False,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(
            payloads[0].fields["Expected Answer"],
            r"Integral: \int_0^1 x^2 dx" + "\n" + r"\frac{1}{3}",
        )

    def test_parse_page_to_cards_extracts_top_level_cloze_paragraphs(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Capital of France is "),
                    _text_item(
                        "Paris",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].card_type, "cloze")
        self.assertIn("{{c1::Paris}}", payloads[0].fields["Text"])
        self.assertEqual(payloads[0].fields["Extra"], "")

    def test_parse_page_to_cards_cloze_preserves_non_highlighted_inline_math(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Solve "),
                    _equation_item(r"x+1"),
                    _text_item(" when "),
                    _text_item(
                        "x=2",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(payloads[0].fields["Text"], r"Solve \(x+1\) when {{c1::x=2}}")

    def test_parse_page_to_cards_cloze_renders_highlighted_inline_math(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _equation_item(
                        r"x^2",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(payloads[0].fields["Text"], r"{{c1::\(x^2\)}}")

    def test_parse_page_to_cards_cloze_merges_contiguous_highlighted_text_and_math(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("The derivative of "),
                    _equation_item(
                        r"x^2",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                    _text_item(
                        " is ",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                    _equation_item(
                        r"2x",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(
            payloads[0].fields["Text"],
            r"The derivative of {{c1::\(x^2\) is \(2x\)}}",
        )
        self.assertEqual(payloads[0].fields["Text"].count("{{c1::"), 1)

    def test_parse_page_to_cards_cloze_keeps_separated_highlighted_runs_distinct(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item(
                        "Paris",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                    _text_item(" and "),
                    _equation_item(
                        r"\pi",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(payloads[0].fields["Text"], r"{{c1::Paris}} and {{c1::\(\pi\)}}")
        self.assertEqual(payloads[0].fields["Text"].count("{{c1::"), 2)

    def test_parse_page_to_cards_cloze_extra_from_immediate_next_extra_paragraph(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Capital of France is "),
                    _text_item(
                        "Paris",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        extra_paragraph = _block(
            "paragraph-extra",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Extra: Located in Europe."),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, extra_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].fields["Extra"], "Located in Europe.")

    def test_parse_page_to_cards_cloze_extra_is_case_insensitive_prefix(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Capital of France is "),
                    _text_item(
                        "Paris",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        extra_paragraph = _block(
            "paragraph-extra",
            "paragraph",
            {
                "rich_text": [
                    _text_item("eXtRa: City of Light"),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, extra_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].fields["Extra"], "City of Light")

    def test_parse_page_to_cards_cloze_extra_requires_immediate_next_block(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Capital of France is "),
                    _text_item(
                        "Paris",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        blocker = _block("divider-1", "divider", {})
        extra_paragraph = _block(
            "paragraph-extra",
            "paragraph",
            {"rich_text": [_text_item("Extra: This should not attach")]},
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, blocker, extra_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].fields["Extra"], "")

    def test_parse_page_to_cards_consumes_extra_paragraph_even_if_highlighted(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Capital of France is "),
                    _text_item(
                        "Paris",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        highlighted_extra_paragraph = _block(
            "paragraph-extra",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Extra: "),
                    _text_item(
                        "Also highlighted",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, highlighted_extra_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertIn("Also highlighted", payloads[0].fields["Extra"])
        self.assertIn('class="highlight-yellow_background"', payloads[0].fields["Extra"])

    def test_parse_page_to_cards_non_extra_paragraph_still_regular_cloze(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("2 + 2 = "),
                    _text_item(
                        "4",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        plain_follow_up = _block(
            "paragraph-follow-up",
            "paragraph",
            {"rich_text": [_text_item("No extra prefix here")]},
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, plain_follow_up],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertIn("{{c1::4}}", payloads[0].fields["Text"])
        self.assertEqual(payloads[0].fields["Extra"], "")

    def test_parse_page_to_cards_cloze_extra_prefix_removed_but_formatting_preserved(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("H2O is "),
                    _text_item(
                        "water",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        extra_paragraph = _block(
            "paragraph-extra",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Extra: "),
                    _text_item(
                        "Click me",
                        href="https://example.com",
                        annotations={
                            "bold": True,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "default",
                        },
                    ),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, extra_paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertNotIn("Extra:", payloads[0].fields["Extra"])
        self.assertIn('<a href="https://example.com">', payloads[0].fields["Extra"])
        self.assertIn("<strong>Click me</strong>", payloads[0].fields["Extra"])

    def test_parse_page_to_cards_ignores_nested_cloze_paragraphs_inside_toggle(self) -> None:
        nested_cloze = _block(
            "nested-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item(
                        "Hidden",
                        annotations={
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    ),
                ]
            },
        )
        root_toggle = _block(
            "root-toggle",
            "toggle",
            {"rich_text": [_text_item("Toggle")]},
            children=(nested_cloze,),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [root_toggle],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].card_type, "basic")

    def test_parse_page_to_cards_ignores_unmarked_paragraphs_for_cloze(self) -> None:
        paragraph = _block("paragraph-1", "paragraph", {"rich_text": [_text_item("No marker")]})

        payloads = parse_page_to_cards(
            "page-1",
            [paragraph],
            default_card_type="basic",
            enable_cloze=True,
        )

        self.assertEqual(payloads, [])

    def test_collect_image_occlusion_candidates_ignores_images_inside_toggles(self) -> None:
        outside = _block(
            "img-outside",
            "image",
            {
                "type": "external",
                "external": {"url": "https://example.com/outside.png"},
                "caption": [_text_item("Outside")],
            },
        )
        inside = _block(
            "img-inside",
            "image",
            {
                "type": "external",
                "external": {"url": "https://example.com/inside.png"},
                "caption": [_text_item("Inside")],
            },
        )
        toggle = _block(
            "toggle",
            "toggle",
            {"rich_text": [_text_item("Toggle")]},
            children=(inside,),
        )

        candidates = collect_image_occlusion_candidates([outside, toggle])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].notion_block_id, "img-outside")

    def test_normalize_typed_answer_strips_html_punctuation_and_case(self) -> None:
        normalized = normalize_typed_answer("<p>Hello,  WORLD!!</p>")
        self.assertEqual(normalized, "hello world")


if __name__ == "__main__":
    unittest.main()
