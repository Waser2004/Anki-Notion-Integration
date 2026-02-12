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
    apply_default_card_type_rule,
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


class DefaultCardTypeRuleTests(unittest.TestCase):
    """Validate descendant cascade behavior for page default card types."""

    def setUp(self) -> None:
        grandchild = _node("grandchild", "Grandchild")
        child = _node("child", "Child", children=(grandchild,))
        self._roots = [_node("parent", "Parent", children=(child,))]
        self._children_map = build_children_map(self._roots)
        self._card_types = {
            "parent": None,
            "child": "basic_reversed",
            "grandchild": "input",
        }

    def test_setting_parent_card_type_only_updates_parent_when_child_has_override(self) -> None:
        updated = apply_default_card_type_rule(
            "parent",
            "basic",
            page_default_card_types=self._card_types,
            children_map=self._children_map,
        )
        self.assertEqual(updated["parent"], "basic")
        self.assertEqual(updated["child"], "basic_reversed")
        self.assertEqual(updated["grandchild"], "input")

    def test_setting_parent_card_type_updates_descendants_when_all_inherit_default(self) -> None:
        updated = apply_default_card_type_rule(
            "parent",
            "basic",
            page_default_card_types={
                "parent": None,
                "child": None,
                "grandchild": None,
            },
            children_map=self._children_map,
        )
        self.assertEqual(updated["parent"], "basic")
        self.assertEqual(updated["child"], "basic")
        self.assertEqual(updated["grandchild"], "basic")

    def test_clearing_parent_card_type_only_updates_parent_when_child_has_override(self) -> None:
        updated = apply_default_card_type_rule(
            "parent",
            None,
            page_default_card_types=self._card_types,
            children_map=self._children_map,
        )
        self.assertIsNone(updated["parent"])
        self.assertEqual(updated["child"], "basic_reversed")
        self.assertEqual(updated["grandchild"], "input")

    def test_setting_child_card_type_only_updates_child_subtree(self) -> None:
        updated = apply_default_card_type_rule(
            "child",
            "input",
            page_default_card_types=self._card_types,
            children_map=self._children_map,
        )
        self.assertIsNone(updated["parent"])
        self.assertEqual(updated["child"], "input")
        self.assertEqual(updated["grandchild"], "input")


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
        self.assertIsNone(pages["page-a"].anki_deck_id)
        self.assertIsNone(pages["page-a"].parent_id)
        self.assertIsNone(pages["page-a"].parent_type)
        self.assertIsNone(pages["page-a"].default_card_type)

    def test_get_pages_reads_stored_deck_id(self) -> None:
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO pages (
                    notion_page_id,
                    anki_deck_name,
                    anki_deck_id,
                    sync_enabled,
                    parent_id,
                    parent_type
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("page-a", "Notion::Deck", 42, 1, "parent", "page_id"),
            )
            connection.commit()
        finally:
            connection.close()

        pages = self._store.get_pages()
        self.assertEqual(pages["page-a"].anki_deck_id, 42)
        self.assertEqual(pages["page-a"].parent_id, "parent")
        self.assertEqual(pages["page-a"].parent_type, "page_id")
        self.assertIsNone(pages["page-a"].default_card_type)

    def test_upsert_page_selection_persists_parent_metadata_from_pages(self) -> None:
        parent = _page("parent", "Parent")
        child = _page("child", "Child", parent_id="parent")
        pages_by_id = {"parent": parent, "child": child}
        deck_names = {
            "parent": "Notion::Parent",
            "child": "Notion::Parent::Child",
        }

        self._store.upsert_page_selection(deck_names, {"child"}, pages_by_id=pages_by_id)
        pages = self._store.get_pages()
        self.assertIsNone(pages["parent"].parent_id)
        self.assertIsNone(pages["parent"].parent_type)
        self.assertEqual(pages["child"].parent_id, "parent")
        self.assertEqual(pages["child"].parent_type, "page_id")

    def test_delete_pages_not_in_removes_stale_rows(self) -> None:
        deck_names = {
            "page-a": "Notion::A",
            "page-b": "Notion::B",
        }
        self._store.upsert_page_selection(deck_names, {"page-a"})

        self._store.delete_pages_not_in({"page-b"})
        pages = self._store.get_pages()
        self.assertNotIn("page-a", pages)
        self.assertIn("page-b", pages)

    def test_set_page_default_card_type_round_trips_and_survives_upsert(self) -> None:
        self._store.upsert_page_selection({"page-a": "Notion::A"}, {"page-a"})
        self._store.set_page_default_card_type("page-a", "input")
        pages = self._store.get_pages()
        self.assertEqual(pages["page-a"].default_card_type, "input")

        # Regular upserts should not reset explicit page overrides.
        self._store.upsert_page_selection({"page-a": "Notion::A"}, {"page-a"})
        pages = self._store.get_pages()
        self.assertEqual(pages["page-a"].default_card_type, "input")

    def test_set_page_default_card_type_accepts_null(self) -> None:
        self._store.upsert_page_selection({"page-a": "Notion::A"}, {"page-a"})
        self._store.set_page_default_card_type("page-a", None)
        pages = self._store.get_pages()
        self.assertIsNone(pages["page-a"].default_card_type)

    def test_set_page_default_card_types_updates_multiple_rows(self) -> None:
        self._store.upsert_page_selection(
            {
                "page-a": "Notion::A",
                "page-b": "Notion::A::B",
                "page-c": "Notion::A::C",
            },
            {"page-a", "page-b", "page-c"},
        )

        self._store.set_page_default_card_types({"page-a", "page-c"}, "input")
        pages = self._store.get_pages()
        self.assertEqual(pages["page-a"].default_card_type, "input")
        self.assertIsNone(pages["page-b"].default_card_type)
        self.assertEqual(pages["page-c"].default_card_type, "input")

    def test_set_page_sync_enabled_updates_single_row(self) -> None:
        self._store.upsert_page_selection(
            {"page-a": "Notion::A", "page-b": "Notion::B"},
            {"page-a", "page-b"},
        )
        self._store.set_page_sync_enabled("page-a", False)
        pages = self._store.get_pages()
        self.assertFalse(pages["page-a"].sync_enabled)
        self.assertTrue(pages["page-b"].sync_enabled)

    def test_set_pages_sync_enabled_updates_multiple_rows(self) -> None:
        self._store.upsert_page_selection(
            {"page-a": "Notion::A", "page-b": "Notion::B", "page-c": "Notion::C"},
            {"page-a", "page-b", "page-c"},
        )
        self._store.set_pages_sync_enabled({"page-a", "page-c"}, enabled=False)
        pages = self._store.get_pages()
        self.assertFalse(pages["page-a"].sync_enabled)
        self.assertTrue(pages["page-b"].sync_enabled)
        self.assertFalse(pages["page-c"].sync_enabled)

    def test_set_all_pages_sync_enabled_updates_every_row(self) -> None:
        self._store.upsert_page_selection(
            {"page-a": "Notion::A", "page-b": "Notion::B"},
            {"page-a", "page-b"},
        )
        self._store.set_all_pages_sync_enabled(False)
        pages = self._store.get_pages()
        self.assertFalse(pages["page-a"].sync_enabled)
        self.assertFalse(pages["page-b"].sync_enabled)

    def test_reset_all_page_default_card_types_clears_all_rows(self) -> None:
        self._store.upsert_page_selection(
            {"page-a": "Notion::A", "page-b": "Notion::B"},
            {"page-a", "page-b"},
        )
        self._store.set_page_default_card_type("page-a", "input")
        self._store.set_page_default_card_type("page-b", "basic_reversed")
        self._store.reset_all_page_default_card_types()
        pages = self._store.get_pages()
        self.assertIsNone(pages["page-a"].default_card_type)
        self.assertIsNone(pages["page-b"].default_card_type)
