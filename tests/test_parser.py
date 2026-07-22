"""Tests for Notion block parsing into toggle card payloads."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.notion_client import NotionBlock, merge_markdown_table_colors
from Noteck.modules.parser.cloze_card_parser import ClozeCardParser
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


def _annotations(
    *,
    color: str = "default",
    background_color: str = "default",
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
    underline: bool = False,
    code: bool = False,
) -> dict:
    """Create a Notion annotations payload for parser tests."""
    return {
        "bold": bold,
        "italic": italic,
        "strikethrough": strikethrough,
        "underline": underline,
        "code": code,
        "color": color,
        "background_color": background_color,
    }


class ParserTests(unittest.TestCase):
    """Validate parser rendering and toggle-card extraction rules."""

    def test_cloze_validator_accepts_valid_anki_cloze_markup(self) -> None:
        """A cloze note with a positive index and hidden text is valid for Anki."""
        payload = parse_page_to_cards(
            "page-1",
            [_block("cloze", "paragraph", {"rich_text": [_text_item("Answer", annotations=_annotations(color="yellow_background"))]})],
            enable_cloze=True,
        )[0]

        result = ClozeCardParser().validate(payload)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.errors, ())

    def test_cloze_validator_rejects_invalid_or_empty_deletions(self) -> None:
        """Malformed markup cannot reach Anki's cloze card generator."""
        from Noteck.modules.parser import ToggleCardPayload

        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="cloze",
            card_type="cloze",
            model_name="Notion (Cloze)",
            fields={"Text": "{{c0::}} {{c1::}}", "Extra": "", "Notion Block ID": "cloze"},
        )

        result = ClozeCardParser().validate(payload)

        self.assertFalse(result.is_valid)
        self.assertTrue(any("positive integer" in error for error in result.errors))
        self.assertTrue(any("non-empty text" in error for error in result.errors))

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

    def test_render_blocks_preserves_external_callout_icons(self) -> None:
        """Uploaded and external callout icons keep their visual slot in rendered cards."""
        rendered = render_blocks(
            [
                _block(
                    "callout-external-icon",
                    "callout",
                    {
                        "icon": {
                            "type": "external",
                            "external": {"url": "https://example.com/icon.svg"},
                        },
                        "rich_text": [_text_item("Callout text")],
                    },
                )
            ]
        )

        self.assertIn(
            '<img class="notion-callout-icon notion-callout-icon-image" '
            'src="https://example.com/icon.svg" alt="" loading="lazy"/>',
            rendered,
        )

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

    def test_paragraph_cloze_renders_unselected_background_colors(self) -> None:
        """Excluded marker colors remain visible as ordinary text backgrounds."""
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Answer", annotations=_annotations(background_color="yellow")),
                    _text_item(" Context", annotations=_annotations(background_color="blue")),
                ]
            },
        )

        payload = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            enable_cloze=True,
            cloze_marker_colors=["yellow"],
        )[0]

        self.assertIn("{{c1::Answer}}", payload.fields["Text"])
        self.assertIn('class="highlight-blue_background"', payload.fields["Text"])

    def test_cloze_sources_use_validated_root_backgrounds_for_card_surfaces(self) -> None:
        """Both cloze source conventions expose their root background to Anki templates."""
        paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "color": "brown_background",
                "rich_text": [
                    _text_item("Answer", annotations=_annotations(background_color="yellow")),
                ],
            },
        )
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"color": "gray_background", "rich_text": [_text_item("Gray cloze")]},
            children=(
                _block(
                    "advanced-answer",
                    "paragraph",
                    {"rich_text": [_text_item("Answer", annotations=_annotations(background_color="yellow"))]},
                ),
            ),
        )

        paragraph_payload = parse_page_to_cards("page-1", [paragraph], enable_cloze=True)[0]
        advanced_payload = parse_page_to_cards("page-1", [advanced_toggle], enable_cloze=True)[0]

        self.assertEqual(paragraph_payload.fields["Notion Card Background"], "brown_background")
        self.assertEqual(advanced_payload.fields["Notion Card Background"], "gray_background")

        uncolored_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Answer", annotations=_annotations(background_color="yellow")),
                ],
            },
        )
        uncolored_payload = parse_page_to_cards("page-1", [uncolored_paragraph], enable_cloze=True)[0]
        self.assertNotEqual(paragraph_payload.content_hash, uncolored_payload.content_hash)

    def test_paragraph_cloze_root_foreground_styles_visible_text_only(self) -> None:
        colored = _block(
            "paragraph-foreground",
            "paragraph",
            {
                "color": "blue",
                "rich_text": [
                    _text_item("Capital is "),
                    _text_item("Paris", annotations=_annotations(background_color="yellow")),
                ],
            },
        )
        unsafe = _block(
            "paragraph-unsafe",
            "paragraph",
            {
                "color": "url(bad)",
                "rich_text": [
                    _text_item("Capital is "),
                    _text_item("Paris", annotations=_annotations(background_color="yellow")),
                ],
            },
        )

        colored_payload = parse_page_to_cards("page-1", [colored], enable_cloze=True)[0]
        unsafe_payload = parse_page_to_cards("page-1", [unsafe], enable_cloze=True)[0]

        self.assertEqual(colored_payload.fields["Notion Card Background"], "")
        self.assertIn('class="notion-block-color notion-block-color-blue"', colored_payload.fields["Text"])
        self.assertIn("{{c1::Paris}}", colored_payload.fields["Text"])
        self.assertEqual(unsafe_payload.fields["Text"], "Capital is {{c1::Paris}}")
        self.assertEqual(unsafe_payload.fields["Notion Card Background"], "")

    def test_colored_paragraph_cloze_extra_uses_shared_block_renderer(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Paris", annotations=_annotations(background_color="yellow")),
                ],
            },
        )
        extra_paragraph = _block(
            "paragraph-extra",
            "paragraph",
            {
                "color": "green_background",
                "rich_text": [_text_item("Extra: Located in Europe.")],
            },
        )

        payload = parse_page_to_cards(
            "page-1",
            [cloze_paragraph, extra_paragraph],
            enable_cloze=True,
        )[0]

        self.assertIn(
            'class="notion-block-color notion-block-color-green_background notion-block-color-background"',
            payload.fields["Extra"],
        )
        self.assertIn("Located in Europe.", payload.fields["Extra"])
        self.assertNotIn("Extra:", payload.fields["Extra"])

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

    def test_cloze_setting_enables_toggle_cloze_parsing(self) -> None:
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Concept")]},
            children=(
                _block(
                    "advanced-p",
                    "paragraph",
                    {
                        "rich_text": [
                            _text_item("Marked", annotations=_annotations(background_color="yellow")),
                        ]
                    },
                ),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].card_type, "cloze")
        self.assertEqual(payloads[0].fields["Text"], "<p>{{c1::Marked}}</p>")

    def test_advanced_cloze_maps_background_colors_to_fixed_numbers(self) -> None:
        color_items = []
        for color_name in ["yellow", "green", "blue", "purple", "pink", "orange", "red", "brown"]:
            color_items.extend(
                [
                    _text_item(color_name, annotations=_annotations(background_color=color_name)),
                    _text_item(" "),
                ]
            )
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Colors")]},
            children=(_block("colors-p", "paragraph", {"rich_text": color_items}),),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        text = payloads[0].fields["Text"]
        for number, color_name in enumerate(
            ["yellow", "green", "blue", "purple", "pink", "orange", "red", "brown"],
            start=1,
        ):
            self.assertIn(f"{{{{c{number}::{color_name}}}}}", text)

    def test_advanced_cloze_excluded_color_remains_background_formatting(self) -> None:
        """A color excluded from marker parsing remains ordinary rendered styling."""
        advanced_toggle = _block(
            "toggle-colors",
            "toggle",
            {"rich_text": [_text_item("[cloze] Colors")]},
            children=(
                _block(
                    "paragraph-colors",
                    "paragraph",
                    {
                        "rich_text": [
                            _text_item("Answer", annotations=_annotations(background_color="yellow")),
                            _text_item(" Styled", annotations=_annotations(background_color="blue")),
                        ]
                    },
                ),
            ),
        )

        payload = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
            cloze_marker_colors=["yellow"],
        )[0]

        self.assertIn("{{c1::Answer}}", payload.fields["Text"])
        self.assertIn('class="highlight-blue_background"', payload.fields["Text"])
        self.assertNotIn("{{c3::", payload.fields["Text"])

    def test_advanced_cloze_excluded_block_color_uses_normal_block_rendering(self) -> None:
        """An excluded whole-block background retains the shared renderer's semantic classes."""
        advanced_toggle = _block(
            "toggle-colors",
            "toggle",
            {"rich_text": [_text_item("[cloze] Colors")]},
            children=(
                _block(
                    "answer",
                    "paragraph",
                    {
                        "color": "yellow_background",
                        "rich_text": [_text_item("Answer")],
                    },
                ),
                _block(
                    "styled-context",
                    "quote",
                    {
                        "color": "blue_background",
                        "rich_text": [_text_item("Visible context")],
                    },
                ),
            ),
        )

        payload = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
            cloze_marker_colors=["yellow"],
        )[0]

        self.assertIn("<p>{{c1::Answer}}</p>", payload.fields["Text"])
        self.assertNotIn("notion-block-color-yellow_background", payload.fields["Text"])
        self.assertIn("notion-block-color-blue_background", payload.fields["Text"])
        self.assertIn("<blockquote", payload.fields["Text"])

    def test_advanced_cloze_allows_repeated_and_skipped_cloze_numbers(self) -> None:
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Repeats")]},
            children=(
                _block(
                    "repeats-p",
                    "paragraph",
                    {
                        "rich_text": [
                            _text_item("Alpha", annotations=_annotations(background_color="yellow")),
                            _text_item(" / "),
                            _text_item("Beta", annotations=_annotations(background_color="yellow")),
                            _text_item(" / "),
                            _text_item("Gamma", annotations=_annotations(background_color="blue")),
                        ]
                    },
                ),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        text = payloads[0].fields["Text"]
        self.assertEqual(text.count("{{c1::"), 2)
        self.assertIn("{{c3::Gamma}}", text)
        self.assertNotIn("{{c2::", text)

    def test_advanced_cloze_parses_cloze_title_toggle_as_cloze_card(self) -> None:
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Biology")]},
            children=(
                _block(
                    "body-p",
                    "paragraph",
                    {"rich_text": [_text_item("Cell", annotations=_annotations(background_color="green"))]},
                ),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].notion_block_id, "advanced-toggle")
        self.assertEqual(payloads[0].card_type, "cloze")
        self.assertEqual(payloads[0].fields["Text"], "<p>{{c2::Cell}}</p>")
        self.assertNotIn("Front", payloads[0].fields)

    def test_advanced_cloze_parses_gray_top_level_toggle_as_cloze_card(self) -> None:
        gray_toggle = _block(
            "gray-toggle",
            "toggle",
            {"rich_text": [_text_item("Gray container")], "color": "gray_background"},
            children=(
                _block(
                    "body-p",
                    "paragraph",
                    {"rich_text": [_text_item("Sky", annotations=_annotations(background_color="blue"))]},
                ),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [gray_toggle],
            enable_cloze=True,
        )

        self.assertEqual(payloads[0].card_type, "cloze")
        self.assertEqual(payloads[0].fields["Text"], "<p>{{c3::Sky}}</p>")

    def test_gray_toggle_cloze_setting_disabled_preserves_toggle_behavior(self) -> None:
        gray_toggle = _block(
            "gray-toggle",
            "toggle",
            {"rich_text": [_text_item("Gray container")], "color": "gray_background"},
            children=(
                _block(
                    "body-p",
                    "paragraph",
                    {"rich_text": [_text_item("Sky", annotations=_annotations(background_color="blue"))]},
                ),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [gray_toggle],
            enable_cloze=True,
            enable_gray_toggle_cloze=False,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].card_type, "basic")
        self.assertIn("Front", payloads[0].fields)
        self.assertIn('class="highlight-blue_background"', payloads[0].back_html)

    def test_advanced_cloze_direct_extra_paragraph_populates_extra_field(self) -> None:
        """A direct child ``Extra:`` paragraph is rendered without its prefix."""
        extra_paragraph = _block(
            "extra-p",
            "paragraph",
            {"rich_text": [_text_item("  eXtRa: Shown on back")]},
        )
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] With extra")]},
            children=(
                _block(
                    "text-p",
                    "paragraph",
                    {"rich_text": [_text_item("Front", annotations=_annotations(background_color="yellow"))]},
                ),
                extra_paragraph,
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        self.assertEqual(payloads[0].fields["Text"], "<p>{{c1::Front}}</p>")
        self.assertEqual(payloads[0].fields["Extra"], "<p>Shown on back</p>")
        self.assertNotIn("Shown on back", payloads[0].fields["Text"])

    def test_advanced_cloze_extra_toggle_is_rendered_normally(self) -> None:
        """The obsolete ``[extra]`` toggle convention no longer populates Extra."""
        extra_toggle = _block(
            "extra-toggle",
            "toggle",
            {"rich_text": [_text_item("[extra] Details")]},
            children=(_block("extra-body", "paragraph", {"rich_text": [_text_item("Toggle body")]}),),
        )
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Toggle is text")]},
            children=(extra_toggle,),
        )

        payload = parse_page_to_cards("page-1", [advanced_toggle], enable_cloze=True)[0]

        self.assertEqual(payload.fields["Extra"], "")
        self.assertIn("<summary>[extra] Details</summary>", payload.fields["Text"])
        self.assertIn("<p>Toggle body</p>", payload.fields["Text"])

    def test_advanced_cloze_nested_extra_toggle_is_rendered_normally(self) -> None:
        nested_extra = _block(
            "nested-extra",
            "toggle",
            {"rich_text": [_text_item("[extra] Nested")]},
            children=(_block("nested-p", "paragraph", {"rich_text": [_text_item("Nested body")]}),),
        )
        wrapper_toggle = _block(
            "wrapper-toggle",
            "toggle",
            {"rich_text": [_text_item("Wrapper")]},
            children=(nested_extra,),
        )
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Nested extra")]},
            children=(wrapper_toggle,),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        self.assertIn("<summary>Wrapper</summary>", payloads[0].fields["Text"])
        self.assertIn("<summary>[extra] Nested</summary>", payloads[0].fields["Text"])
        self.assertIn("<p>Nested body</p>", payloads[0].fields["Text"])
        self.assertEqual(payloads[0].fields["Extra"], "")

    def test_paragraph_cloze_maps_all_marker_colors_to_toggle_cloze_numbers(self) -> None:
        """Normal paragraphs use the same fixed color-to-number mapping as toggles."""
        rich_text = []
        colors = ["yellow", "green", "blue", "purple", "pink", "orange", "red", "brown"]
        for index, color in enumerate(colors):
            # Exercise both annotation formats returned by supported Notion sources.
            annotations = (
                _annotations(color=f"{color}_background")
                if index % 2 == 0
                else _annotations(background_color=color)
            )
            rich_text.extend([_text_item(color, annotations=annotations), _text_item(" / ")])

        paragraph = _block("paragraph-colors", "paragraph", {"rich_text": rich_text})
        payloads = parse_page_to_cards("page-1", [paragraph], enable_cloze=True)

        self.assertEqual(len(payloads), 1)
        for number, color in enumerate(colors, start=1):
            self.assertIn(f"{{{{c{number}::{color}}}}}", payloads[0].fields["Text"])

    def test_advanced_cloze_preserves_formatting_and_removes_marker_backgrounds(self) -> None:
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] Formatting")]},
            children=(
                _block(
                    "format-p",
                    "paragraph",
                    {
                        "rich_text": [
                            _text_item(
                                "Term",
                                href="https://example.com",
                                annotations=_annotations(
                                    color="yellow_background",
                                    bold=True,
                                    italic=True,
                                ),
                            ),
                            _text_item(" "),
                            _text_item("blue", annotations=_annotations(color="blue")),
                        ]
                    },
                ),
            ),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        text = payloads[0].fields["Text"]
        self.assertIn('{{c1::<a href="https://example.com"><em><strong>Term</strong></em></a>}}', text)
        self.assertIn('<span class="highlight-blue">blue</span>', text)
        self.assertNotIn("highlight-yellow_background", text)

    def test_colored_top_level_paragraph_is_not_a_cloze_card(self) -> None:
        """A block background alone does not provide enough cloze context."""
        paragraph = _block(
            "paragraph-block-cloze",
            "paragraph",
            {
                "color": "yellow_background",
                "rich_text": [
                    _text_item("Whole"),
                    _text_item(" paragraph", annotations=_annotations(bold=True)),
                ],
            },
        )

        self.assertEqual(parse_page_to_cards("page-1", [paragraph], enable_cloze=True), [])

        unselected = _block(
            "paragraph-unselected",
            "paragraph",
            {"color": "yellow_background", "rich_text": [_text_item("Unselected")]},
        )
        foreground_only = _block(
            "paragraph-foreground",
            "paragraph",
            {"color": "yellow", "rich_text": [_text_item("Foreground only")]},
        )
        self.assertEqual(
            parse_page_to_cards("page-1", [unselected], enable_cloze=True, cloze_marker_colors=["green"]),
            [],
        )
        self.assertEqual(parse_page_to_cards("page-1", [foreground_only], enable_cloze=True), [])

    def test_advanced_cloze_preserves_colored_block_structures(self) -> None:
        """Block markers replace direct text while keeping each Notion structure visible."""
        advanced_toggle = _block(
            "advanced-structures",
            "toggle",
            {"rich_text": [_text_item("[cloze] Colored structures")]},
            children=(
                _block(
                    "heading-1",
                    "heading_1",
                    {"color": "yellow_background", "rich_text": [_text_item("Heading one")]},
                ),
                _block(
                    "heading-2",
                    "heading_2",
                    {"color": "green_background", "rich_text": [_text_item("Heading two")]},
                ),
                _block(
                    "heading-3",
                    "heading_3",
                    {"color": "blue_background", "rich_text": [_text_item("Heading three")]},
                ),
                _block(
                    "paragraph",
                    "paragraph",
                    {"color": "purple_background", "rich_text": [_text_item("Paragraph text")]},
                ),
                _block(
                    "nested-toggle",
                    "toggle",
                    {"color": "pink_background", "rich_text": [_text_item("Toggle title")]},
                    children=(_block("toggle-child", "paragraph", {"rich_text": [_text_item("Toggle child")]}),),
                ),
                _block(
                    "bullet",
                    "bulleted_list_item",
                    {"color": "orange_background", "rich_text": [_text_item("Bullet text")]},
                ),
                _block(
                    "numbered",
                    "numbered_list_item",
                    {"color": "red_background", "rich_text": [_text_item("Numbered text")]},
                ),
                _block(
                    "quote",
                    "quote",
                    {"color": "brown_background", "rich_text": [_text_item("Quote text")]},
                ),
                _block(
                    "callout",
                    "callout",
                    {
                        "color": "yellow_background",
                        "icon": {"type": "emoji", "emoji": "💡"},
                        "rich_text": [_text_item("Callout text")],
                    },
                    children=(
                        _block("callout-child", "paragraph", {"rich_text": [_text_item("Callout child")]}),
                        _block(
                            "callout-toggle",
                            "toggle",
                            {"rich_text": [_text_item("Callout toggle")]},
                            children=(
                                _block(
                                    "callout-toggle-child",
                                    "paragraph",
                                    {"rich_text": [_text_item("Callout toggle child")]},
                                ),
                            ),
                        ),
                        _block(
                            "callout-table",
                            "table",
                            {"table_width": 1, "has_column_header": False, "has_row_header": False},
                            children=(
                                _block(
                                    "callout-table-row",
                                    "table_row",
                                    {"cells": [[_text_item("Callout cell")]]},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        text = parse_page_to_cards("page-1", [advanced_toggle], enable_cloze=True)[0].fields["Text"]

        self.assertIn("<h1>{{c1::Heading one}}</h1>", text)
        self.assertIn("<h2>{{c2::Heading two}}</h2>", text)
        self.assertIn("<h3>{{c3::Heading three}}</h3>", text)
        self.assertIn("<p>{{c4::Paragraph text}}</p>", text)
        self.assertIn('<details class="notion-toggle"><summary>{{c5::Toggle title}}</summary>', text)
        self.assertIn("<p>Toggle child</p>", text)
        self.assertIn("<ul><li>{{c6::Bullet text}}</li></ul>", text)
        self.assertIn("<ol><li>{{c7::Numbered text}}</li></ol>", text)
        self.assertIn("<blockquote><p>{{c8::Quote text}}</p></blockquote>", text)
        self.assertIn('<div class="callout">', text)
        self.assertIn('<p class="notion-callout-icon">💡</p>', text)
        self.assertIn("<p>{{c1::Callout text}}</p><p>{{c1::Callout child}}</p>", text)
        self.assertIn(
            '<details class="notion-toggle"><summary>{{c1::Callout toggle}}</summary>'
            "<p>{{c1::Callout toggle child}}</p></details>",
            text,
        )
        self.assertIn('<table><tbody><tr><td>{{c1::Callout cell}}</td></tr></tbody></table>', text)

    def test_colored_callout_hides_all_textual_descendants_and_keeps_their_structure(self) -> None:
        """A callout marker covers nested code, equations, captions, tables, and child callouts."""
        advanced_toggle = _block(
            "advanced-callout-descendants",
            "toggle",
            {"rich_text": [_text_item("[cloze] Callout descendants")]},
            children=(
                _block(
                    "outer-callout",
                    "callout",
                    {
                        "color": "yellow_background",
                        "icon": {"type": "emoji", "emoji": "\U0001F4A1"},
                        "rich_text": [
                            _text_item("Outer text", annotations=_annotations(color="purple_background")),
                        ],
                    },
                    children=(
                        _block(
                            "callout-code",
                            "code",
                            {
                                "language": "python",
                                "rich_text": [
                                    _text_item("print('hidden')", annotations=_annotations(color="purple_background")),
                                ],
                            },
                        ),
                        _block(
                            "callout-equation",
                            "equation",
                            {"expression": "x^2 + y^2"},
                        ),
                        _block(
                            "callout-image",
                            "image",
                            {
                                "type": "external",
                                "external": {"url": "https://example.com/hidden.png"},
                                "caption": [
                                    _text_item("Hidden caption", annotations=_annotations(color="purple_background")),
                                ],
                            },
                        ),
                        _block(
                            "nested-callout",
                            "callout",
                            {
                                "color": "green_background",
                                "icon": {"type": "emoji", "emoji": "\U0001F331"},
                                "rich_text": [_text_item("Inner text")],
                            },
                            children=(
                                _block(
                                    "nested-callout-child",
                                    "paragraph",
                                    {
                                        "rich_text": [
                                            _text_item(
                                                "Inner child",
                                                annotations=_annotations(color="brown_background"),
                                            ),
                                        ],
                                    },
                                ),
                            ),
                        ),
                        _block(
                            "callout-table",
                            "table",
                            {"table_width": 1, "has_column_header": False, "has_row_header": False},
                            children=(
                                _block(
                                    "callout-table-row",
                                    "table_row",
                                    {
                                        "cells": [
                                            [
                                                _text_item(
                                                    "Table answer",
                                                    annotations=_annotations(color="purple_background"),
                                                )
                                            ]
                                        ]
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        text = parse_page_to_cards("page-1", [advanced_toggle], enable_cloze=True)[0].fields["Text"]

        self.assertIn('<div class="callout">', text)
        self.assertIn("<p>{{c1::Outer text}}</p>", text)
        self.assertIn(
            '<pre class="code"><code class="language-python">{{c1::print(&#x27;hidden&#x27;)}}</code></pre>',
            text,
        )
        self.assertIn('<div class="notion-block-equation">{{c1::\\(x^2 + y^2\\)}}</div>', text)
        self.assertIn(
            '<figure class="notion-image"><img src="https://example.com/hidden.png" '
            'alt="Notion image" loading="lazy"/><figcaption>{{c1::Hidden caption}}</figcaption></figure>',
            text,
        )
        self.assertIn('<p>{{c2::Inner text}}</p><p>{{c2::Inner child}}</p>', text)
        self.assertIn('<td>{{c1::Table answer}}</td>', text)
        self.assertNotIn("{{c4::", text)
        self.assertNotIn("{{c8::", text)

    def test_advanced_cloze_replaces_fully_colored_table_cells(self) -> None:
        """Every uniformly marked cell becomes an independently styled whole-cell cloze."""
        table = _block(
            "table",
            "table",
            {"table_width": 3, "has_column_header": True, "has_row_header": True},
            children=(
                _block(
                    "header-row",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Header A", annotations=_annotations(color="yellow_background"))],
                            [_text_item("Header B")],
                            [_text_item("Header C", annotations=_annotations(background_color="green"))],
                        ]
                    },
                ),
                _block(
                    "row-one",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Row one", annotations=_annotations(background_color="blue"))],
                            [
                                _text_item("Split ", annotations=_annotations(color="yellow_background")),
                                _text_item("cell", annotations=_annotations(background_color="yellow")),
                            ],
                            [
                                _text_item("Partial", annotations=_annotations(background_color="yellow")),
                                _text_item(" visible"),
                            ],
                        ]
                    },
                ),
                _block(
                    "row-two",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Row two")],
                            [_text_item("Same column", annotations=_annotations(color="green_background"))],
                            [_text_item("Another cell", annotations=_annotations(background_color="yellow"))],
                        ]
                    },
                ),
            ),
        )
        advanced_toggle = _block(
            "advanced-table",
            "toggle",
            {"rich_text": [_text_item("[cloze] Table")]},
            children=(table,),
        )

        text = parse_page_to_cards("page-1", [advanced_toggle], enable_cloze=True)[0].fields["Text"]

        self.assertIn("<table><thead>", text)
        self.assertIn('<th scope="col">{{c1::Header A}}</th>', text)
        self.assertIn('<th scope="col">{{c2::Header C}}</th>', text)
        self.assertIn('<th scope="row">{{c3::Row one}}</th>', text)
        self.assertIn('<td>{{c1::Split cell}}</td>', text)
        self.assertIn('<td>{{c2::Same column}}</td>', text)
        self.assertIn('<td>{{c1::Another cell}}</td>', text)
        self.assertIn("<td>{{c1::Partial}} visible</td>", text)
        self.assertNotIn('<td class="cloze">{{c1::Partial', text)

    def test_table_only_advanced_cloze_accepts_enhanced_markdown_cell_colors(self) -> None:
        """A table-only card remains discoverable when colors use Notion's short aliases."""
        table = _block(
            "table-short-colors",
            "table",
            {"table_width": 2, "has_column_header": False, "has_row_header": False},
            children=(
                _block(
                    "table-short-colors-row",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Hidden", annotations=_annotations(color="yellow_bg"))],
                            [_text_item("Visible")],
                        ]
                    },
                ),
            ),
        )
        advanced_toggle = _block(
            "advanced-table-only",
            "toggle",
            {"rich_text": [_text_item("[cloze] Table only")]},
            children=(table,),
        )

        payload = parse_page_to_cards("page-1", [advanced_toggle], enable_cloze=True)[0]

        self.assertIn('<td>{{c1::Hidden}}</td>', payload.fields["Text"])
        self.assertIn("<td>Visible</td>", payload.fields["Text"])
        self.assertTrue(ClozeCardParser().validate(payload).is_valid)

    def test_enhanced_markdown_table_colors_are_merged_into_block_api_cells(self) -> None:
        """Cell, row, and column colors from Markdown become table cloze markers."""
        table = _block(
            "table-markdown-colors",
            "table",
            {"table_width": 3, "has_column_header": True, "has_row_header": True},
            children=(
                _block(
                    "table-markdown-row-1",
                    "table_row",
                    {"cells": [[_text_item("A")], [_text_item("B")], [_text_item("C")]]},
                ),
                _block(
                    "table-markdown-row-2",
                    "table_row",
                    {"cells": [[_text_item("D")], [_text_item("E")], [_text_item("F")]]},
                ),
            ),
        )
        advanced_toggle = _block(
            "advanced-markdown-table",
            "toggle",
            {"rich_text": [_text_item("[cloze] Markdown table colors")]},
            children=(table,),
        )
        markdown = """
<table>
<colgroup><col color="yellow_bg"><col><col color="purple_bg"></colgroup>
<tr><td>A</td><td color="green_bg">B</td><td>C</td></tr>
<tr color="blue_bg"><td>D</td><td>E</td><td>F</td></tr>
</table>
"""

        enriched = merge_markdown_table_colors([advanced_toggle], markdown)
        self.assertEqual(
            enriched[0].children[0].children[0].raw["table_row"].get("_noteck_cell_colors"),
            ["yellow_bg", "green_bg", "purple_bg"],
        )
        payload = parse_page_to_cards("page-1", enriched, enable_cloze=True)[0]
        text = payload.fields["Text"]

        self.assertIn('<th scope="col">{{c1::A}}</th>', text)
        self.assertIn('<th scope="col">{{c2::B}}</th>', text)
        self.assertIn('<th scope="col">{{c4::C}}</th>', text)
        self.assertIn('<th scope="row">{{c3::D}}</th>', text)
        self.assertIn('<td>{{c3::E}}</td>', text)
        self.assertIn('<td>{{c3::F}}</td>', text)

    def test_unselected_markdown_table_cell_colors_remain_visible(self) -> None:
        """Whole-cell colors excluded from cloze markers render on td/th elements."""
        table = _block(
            "table-visual-colors",
            "table",
            {"table_width": 3, "has_column_header": True, "has_row_header": False},
            children=(
                _block(
                    "table-visual-row",
                    "table_row",
                    {
                        "cells": [
                            [_text_item("Selected")],
                            [_text_item("Red context")],
                            [_text_item("Pink context")],
                        ]
                    },
                ),
            ),
        )
        advanced_toggle = _block(
            "advanced-visual-table",
            "toggle",
            {"rich_text": [_text_item("[cloze] Visual table colors")]},
            children=(table,),
        )
        markdown = """
<table>
<tr><th color="yellow_bg">Selected</th><th color="red_bg">Red context</th><th color="pink_bg">Pink context</th></tr>
</table>
"""

        enriched = merge_markdown_table_colors([advanced_toggle], markdown)
        payload = parse_page_to_cards(
            "page-1",
            enriched,
            enable_cloze=True,
            cloze_marker_colors=["yellow"],
        )[0]
        text = payload.fields["Text"]

        self.assertIn('<th scope="col">{{c1::Selected}}</th>', text)
        self.assertIn('<th scope="col" class="highlight-red_background">Red context</th>', text)
        self.assertIn('<th scope="col" class="highlight-pink_background">Pink context</th>', text)
        self.assertNotIn("{{c7::", text)
        self.assertNotIn("{{c5::", text)

    def test_unselected_markdown_color_renders_on_empty_table_cell(self) -> None:
        """Color metadata survives even when the block API cell has no rich text."""
        table = _block(
            "table-empty-color",
            "table",
            {"table_width": 2, "has_column_header": False, "has_row_header": False},
            children=(
                _block(
                    "table-empty-row",
                    "table_row",
                    {"cells": [[_text_item("Answer")], []]},
                ),
            ),
        )
        advanced_toggle = _block(
            "advanced-empty-table",
            "toggle",
            {"rich_text": [_text_item("[cloze] Empty colored cell")]},
            children=(table,),
        )
        markdown = """
<table>
<tr><td color="yellow_bg">Answer</td><td color="red_bg"></td></tr>
</table>
"""

        enriched = merge_markdown_table_colors([advanced_toggle], markdown)
        text = parse_page_to_cards(
            "page-1",
            enriched,
            enable_cloze=True,
            cloze_marker_colors=["yellow"],
        )[0].fields["Text"]

        self.assertIn('<td>{{c1::Answer}}</td>', text)
        self.assertIn('<td class="highlight-red_background"></td>', text)

    def test_advanced_cloze_container_without_markers_remains_cloze_payload(self) -> None:
        advanced_toggle = _block(
            "advanced-toggle",
            "toggle",
            {"rich_text": [_text_item("[cloze] No markers")]},
            children=(_block("plain-p", "paragraph", {"rich_text": [_text_item("Plain text")]}),),
        )

        payloads = parse_page_to_cards(
            "page-1",
            [advanced_toggle],
            enable_cloze=True,
        )

        self.assertEqual(payloads[0].card_type, "cloze")
        self.assertEqual(payloads[0].fields["Text"], "<p>Plain text</p>")

    def test_existing_top_level_paragraph_cloze_still_works_with_advanced_enabled(self) -> None:
        cloze_paragraph = _block(
            "paragraph-cloze",
            "paragraph",
            {
                "rich_text": [
                    _text_item("Capital is "),
                    _text_item("Paris", annotations=_annotations(color="yellow_background")),
                ]
            },
        )

        payloads = parse_page_to_cards(
            "page-1",
            [cloze_paragraph],
            enable_cloze=True,
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].fields["Text"], "Capital is {{c1::Paris}}")

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
