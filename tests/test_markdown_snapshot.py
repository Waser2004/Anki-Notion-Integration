"""Tests for deterministic Notion enhanced-Markdown snapshots."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.markdown_snapshot import (
    canonicalize_notion_markdown,
    extract_root_toggle_markdown,
    hash_notion_markdown,
)


class MarkdownSnapshotTests(unittest.TestCase):
    """Validate cleanup and root-toggle source segmentation."""

    def test_canonicalization_removes_only_transient_media_signatures(self) -> None:
        first = (
            '![Diagram](https://files.example.test/image.png?X-Amz-Signature=first&X-Amz-Expires=60)\r\n'
            '[Reference](https://example.test/article?chapter=2)  '
        )
        second = (
            '![Diagram](https://files.example.test/image.png?X-Amz-Signature=second&X-Amz-Expires=120)\n'
            '[Reference](https://example.test/article?chapter=2)'
        )

        self.assertEqual(hash_notion_markdown(first), hash_notion_markdown(second))
        cleaned = canonicalize_notion_markdown(first)
        self.assertIn("https://files.example.test/image.png", cleaned)
        self.assertNotIn("X-Amz-", cleaned)
        self.assertIn("https://example.test/article?chapter=2", cleaned)

    def test_extracts_only_root_toggles_and_keeps_nested_toggle_source(self) -> None:
        markdown = (
            "Intro\n"
            "<details>\n"
            "<summary>First</summary>\n"
            "\tAnswer\n"
            "\t<details>\n"
            "\t<summary>Nested</summary>\n"
            "\t\tNested answer\n"
            "\t</details>\n"
            "</details>\n"
            "<callout>\n"
            "\t<details>\n"
            "\t<summary>Not root</summary>\n"
            "\t\tAnswer\n"
            "\t</details>\n"
            "</callout>\n"
            "<details color=\"blue_bg\">\n"
            "<summary>Second</summary>\n"
            "\tAnswer\n"
            "</details>"
        )

        sources = extract_root_toggle_markdown(markdown)

        self.assertIsNotNone(sources)
        self.assertEqual(len(sources or ()), 2)
        self.assertIn("<summary>Nested</summary>", (sources or ("",))[0])
        self.assertIn("<summary>Second</summary>", (sources or ("", ""))[1])

    def test_code_fence_contents_do_not_create_false_toggles(self) -> None:
        markdown = (
            "```html\n"
            "<details>\n"
            "<summary>Example only</summary>\n"
            "</details>\n"
            "```\n"
            "<details>\n"
            "<summary>Real card</summary>\n"
            "\tAnswer\n"
            "</details>"
        )

        sources = extract_root_toggle_markdown(markdown)

        self.assertEqual(len(sources or ()), 1)
        self.assertIn("Real card", (sources or ("",))[0])

    def test_unclosed_root_toggle_is_ambiguous(self) -> None:
        self.assertIsNone(
            extract_root_toggle_markdown(
                "<details>\n<summary>Question</summary>\n\tAnswer"
            )
        )
