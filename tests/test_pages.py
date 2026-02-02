"""Tests for pages selection helpers and pages-table persistence."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.db import Database
from anki_notion_integration.notion_client import NotionPage, PageNode
from anki_notion_integration.pages import (
    PagesStore,
    apply_selection_rule,
    build_children_map,
    build_children_map_from_pages,
    build_deck_names,
    build_deck_names_from_pages,
)


def _node(page_id: str, title: str, children: tuple[PageNode, ...] = ()) -> PageNode:
    """Create a `PageNode` test helper with minimal required fields."""
    return PageNode(
        page=NotionPage(
            page_id=page_id,
            title=title,
            icon=None,
            parent_id=None,
            parent_type=None,
            raw={},
        ),
        children=children,
    )


def _page(
    page_id: str,
    title: str,
    parent_id: str | None = None,
    parent_type: str | None = "page_id",
) -> NotionPage:
    """Create a `NotionPage` test helper with minimal required fields."""
    return NotionPage(
        page_id=page_id,
        title=title,
        icon=None,
        parent_id=parent_id,
        parent_type=parent_type if parent_id else None,
        raw={},
    )


class SelectionRuleTests(unittest.TestCase):
    """Validate asymmetric parent/child selection behavior."""

    def setUp(self) -> None:
        grandchild = _node("grandchild", "Grandchild")
        child = _node("child", "Child", children=(grandchild,))
        self._roots = [_node("parent", "Parent", children=(child,))]
        self._children_map = build_children_map(self._roots)

    def test_select_parent_selects_descendants_when_none_selected(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=True,
            selected_ids={"parent"},
            children_map=self._children_map,
        )
        self.assertEqual(selected, {"parent", "child", "grandchild"})

    def test_select_parent_does_not_force_descendants_when_any_already_selected(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=True,
            selected_ids={"parent", "child"},
            children_map=self._children_map,
        )
        self.assertEqual(selected, {"parent", "child"})

    def test_deselect_parent_only_affects_parent(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=False,
            selected_ids={"parent", "child", "grandchild"},
            children_map=self._children_map,
        )
        self.assertEqual(selected, {"child", "grandchild"})


class DeckNameTests(unittest.TestCase):
    """Validate deterministic deck naming from the page tree."""

    def test_build_deck_names_uses_notion_prefix_and_hierarchy(self) -> None:
        roots = [_node("root", "Root", children=(_node("child", "Child"),))]
        deck_names = build_deck_names(roots)
        self.assertEqual(deck_names["root"], "Notion::Root")
        self.assertEqual(deck_names["child"], "Notion::Root::Child")

    def test_build_deck_names_normalizes_invalid_separator(self) -> None:
        roots = [_node("root", "A::B")]
        deck_names = build_deck_names(roots)
        self.assertEqual(deck_names["root"], "Notion::A∷B")

    def test_build_children_map_from_pages_ignores_unknown_parent(self) -> None:
        pages = {
            "child": _page("child", "Child", parent_id="missing"),
            "parent": _page("parent", "Parent"),
        }
        children_map = build_children_map_from_pages(pages)
        self.assertEqual(children_map["child"], ())
        self.assertEqual(children_map["parent"], ())

    def test_build_deck_names_from_pages_handles_out_of_order_arrival(self) -> None:
        pages = {
            "child": _page("child", "Child", parent_id="parent"),
            "parent": _page("parent", "Parent"),
        }
        deck_names = build_deck_names_from_pages(pages)
        self.assertEqual(deck_names["parent"], "Notion::Parent")
        self.assertEqual(deck_names["child"], "Notion::Parent::Child")

    def test_build_deck_names_from_pages_handles_cycles(self) -> None:
        pages = {
            "a": _page("a", "A", parent_id="b"),
            "b": _page("b", "B", parent_id="a"),
        }
        deck_names = build_deck_names_from_pages(pages)
        self.assertIn("a", deck_names)
        self.assertIn("b", deck_names)


class PagesStoreTests(unittest.TestCase):
    """Validate `pages` table reads/writes for selection persistence."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db = Database(Path(self._temp_dir.name) / "pages.db")
        self._db.initialize()
        self._store = PagesStore(self._db)

    def test_upsert_page_selection_inserts_and_updates_sync_enabled(self) -> None:
        deck_names = {
            "page-a": "Notion::Parent::A",
            "page-b": "Notion::Parent::B",
        }
        self._store.upsert_page_selection(deck_names, {"page-a"})
        first = self._store.get_pages()
        self.assertTrue(first["page-a"].sync_enabled)
        self.assertFalse(first["page-b"].sync_enabled)

        self._store.upsert_page_selection(deck_names, {"page-b"})
        second = self._store.get_pages()
        self.assertFalse(second["page-a"].sync_enabled)
        self.assertTrue(second["page-b"].sync_enabled)

    def test_upsert_page_selection_updates_deck_name(self) -> None:
        self._store.upsert_page_selection({"page-a": "Notion::Old"}, {"page-a"})
        self._store.upsert_page_selection({"page-a": "Notion::New"}, {"page-a"})
        pages = self._store.get_pages()
        self.assertEqual(pages["page-a"].anki_deck_name, "Notion::New")
