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


if __name__ == "__main__":
    unittest.main()
