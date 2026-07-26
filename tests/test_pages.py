"""Tests for pages selection helpers and pages-table persistence."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.db import Database
from Noteck.modules.notion_client import NotionPage, PageNode
from Noteck.modules.pages import (
    DEFAULT_PAGE_SELECTION_BEHAVIOR,
    DYNAMIC_SELECTION_PARENT_IDS_SETTING,
    PAGE_SELECTION_BEHAVIOR_DETAILS,
    PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
    PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS,
    PAGE_SELECTION_BEHAVIOR_MANUAL,
    PageRefreshCancelled,
    PagesStore,
    apply_default_card_type_rule,
    apply_selection_rule,
    begin_startup_page_refresh,
    claim_startup_page_refresh_status,
    build_children_map,
    build_children_map_from_pages,
    build_deck_names,
    build_deck_names_from_pages,
    normalize_page_selection_behavior,
    page_selection_behavior_description,
    page_selection_behavior_tooltip,
    refresh_pages,
    trigger_startup_page_refresh,
)
from Noteck.modules.settings import SettingsStore, create_default_settings


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
    """Validate configured parent/child selection behavior."""

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
            behavior=PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS,
        )
        self.assertEqual(selected, {"parent", "child", "grandchild"})

    def test_manual_select_parent_only_selects_parent(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=True,
            selected_ids={"parent"},
            children_map=self._children_map,
            behavior=PAGE_SELECTION_BEHAVIOR_MANUAL,
        )
        self.assertEqual(selected, {"parent"})

    def test_manual_deselect_parent_only_deselects_parent(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=False,
            selected_ids={"parent", "child", "grandchild"},
            children_map=self._children_map,
            behavior=PAGE_SELECTION_BEHAVIOR_MANUAL,
        )
        self.assertEqual(selected, {"child", "grandchild"})

    def test_select_parent_does_not_force_descendants_when_any_already_selected(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=True,
            selected_ids={"parent", "child"},
            children_map=self._children_map,
            behavior=PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS,
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

    def test_dynamic_select_parent_selects_all_known_descendants(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=True,
            selected_ids={"parent", "child"},
            children_map=self._children_map,
            behavior=PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
        )
        self.assertEqual(selected, {"parent", "child", "grandchild"})

    def test_dynamic_deselect_parent_deselects_descendants(self) -> None:
        selected = apply_selection_rule(
            "parent",
            checked=False,
            selected_ids={"parent", "child", "grandchild"},
            children_map=self._children_map,
            behavior=PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
        )
        self.assertEqual(selected, set())

    def test_dynamic_descendant_cannot_be_deselected_while_parent_is_active(self) -> None:
        selected = apply_selection_rule(
            "child",
            checked=False,
            selected_ids={"parent", "child", "grandchild"},
            children_map=self._children_map,
            behavior=PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
            dynamic_parent_ids={"parent"},
        )
        self.assertEqual(selected, {"parent", "child", "grandchild"})

    def test_unknown_selection_behavior_uses_default(self) -> None:
        self.assertEqual(
            normalize_page_selection_behavior("unsupported"),
            DEFAULT_PAGE_SELECTION_BEHAVIOR,
        )

    def test_selection_behavior_labels_are_short_setting_labels(self) -> None:
        self.assertEqual(PAGE_SELECTION_BEHAVIOR_DETAILS[PAGE_SELECTION_BEHAVIOR_MANUAL][0], "Manual")
        self.assertEqual(PAGE_SELECTION_BEHAVIOR_DETAILS[PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS][0], "Smart")
        self.assertEqual(PAGE_SELECTION_BEHAVIOR_DETAILS[PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS][0], "Dynamic")

    def test_selection_behavior_descriptions_are_shared_by_behavior_key(self) -> None:
        self.assertIn("only the page you click", page_selection_behavior_description(PAGE_SELECTION_BEHAVIOR_MANUAL))
        self.assertIn("currently visible", page_selection_behavior_description(PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS))
        self.assertIn("later refreshes", page_selection_behavior_description(PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS))

    def test_selection_behavior_tooltips_are_short_and_behavior_specific(self) -> None:
        self.assertEqual(
            page_selection_behavior_tooltip(PAGE_SELECTION_BEHAVIOR_MANUAL),
            "Only toggle the clicked page.",
        )
        self.assertEqual(
            page_selection_behavior_tooltip(PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS),
            "Include currently visible children when selecting a new parent.",
        )
        self.assertEqual(
            page_selection_behavior_tooltip(PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS),
            "Keep selected parent subtrees synced as new children appear.",
        )


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


class PageRefreshTests(unittest.TestCase):
    """Validate background-safe page discovery and dynamic selection updates."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db = Database(Path(self._temp_dir.name) / "refresh.db")
        self._db.initialize()
        create_default_settings(self._db)
        self._store = PagesStore(self._db)
        self._store.upsert_page_selection({"parent": "Notion::Parent"}, {"parent"})
        self._db.set_setting(DYNAMIC_SELECTION_PARENT_IDS_SETTING, '["parent"]')

    def test_dynamic_refresh_selects_new_descendants_before_sync(self) -> None:
        pages = [
            _page("parent", "Parent"),
            _page("child", "Child", parent_id="parent"),
            _page("grandchild", "Grandchild", parent_id="child"),
        ]
        client = type("_Client", (), {"iter_pages": lambda _self: iter(pages)})()
        progress_counts: list[int] = []
        SettingsStore(self._db).set_value(
            "page_selection_behavior",
            PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
        )

        result = refresh_pages(
            self._db,
            client=client,
            progress_callback=progress_counts.append,
        )

        self.assertEqual(result.loaded_count, 3)
        self.assertEqual(progress_counts, [0, 1, 2, 3])
        self.assertEqual(
            self._store.get_selected_page_ids(),
            {"parent", "child", "grandchild"},
        )

    def test_non_dynamic_refresh_preserves_selection_without_selecting_children(self) -> None:
        pages = [
            _page("parent", "Parent"),
            _page("child", "Child", parent_id="parent"),
        ]
        client = type("_Client", (), {"iter_pages": lambda _self: iter(pages)})()
        SettingsStore(self._db).set_value(
            "page_selection_behavior",
            PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS,
        )

        refresh_pages(self._db, client=client)

        self.assertEqual(self._store.get_selected_page_ids(), {"parent"})

    def test_refresh_preserves_selection_changed_during_order_lookup(self) -> None:
        pages = [
            _page("parent", "Parent"),
            _page("child", "Child", parent_id="parent"),
        ]

        class _Client:
            """Simulate a Pages-tab edit while startup ordering is still running."""

            @staticmethod
            def iter_pages() -> object:
                return iter(pages)

            def build_child_page_order_map(
                self,
                _pages: object,
            ) -> dict[str, tuple[str, ...]]:
                self_store.set_page_sync_enabled("parent", False)
                return {}

        self_store = self._store
        SettingsStore(self._db).set_value(
            "page_selection_behavior",
            PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
        )

        refresh_pages(
            self._db,
            client=_Client(),
            startup_generation=begin_startup_page_refresh(),
        )

        self.assertEqual(self._store.get_selected_page_ids(), set())

    def test_startup_order_lookup_honors_cancellation(self) -> None:
        pages = [
            _page("parent", "Parent"),
            _page("child", "Child", parent_id="parent"),
        ]
        cancellation_checks = 0

        class _Client:
            """Request cancellation from inside the sibling-order phase."""

            @staticmethod
            def iter_pages() -> object:
                return iter(pages)

            @staticmethod
            def build_child_page_order_map(
                _pages: object,
                *,
                should_cancel: object,
            ) -> dict[str, tuple[str, ...]]:
                if callable(should_cancel):
                    should_cancel()
                return {}

        def should_cancel() -> bool:
            nonlocal cancellation_checks
            cancellation_checks += 1
            return cancellation_checks >= 3

        with self.assertRaises(PageRefreshCancelled):
            refresh_pages(
                self._db,
                client=_Client(),
                should_cancel=should_cancel,
                startup_generation=begin_startup_page_refresh(),
            )

        self.assertGreaterEqual(cancellation_checks, 3)

    def test_startup_refresh_runs_through_anki_background_task_manager(self) -> None:
        pages = [_page("parent", "Renamed Parent")]
        expected_order = {"parent": ("child-b", "child-a")}
        client = type(
            "_Client",
            (),
            {
                "iter_pages": lambda _self: iter(pages),
                "build_child_page_order_map": lambda _self, _pages: expected_order,
            },
        )()
        completed: list[bool] = []

        class _TaskManager:
            """Execute the submitted startup task immediately for this unit test."""

            @staticmethod
            def run_in_background(work: object, done: object, uses_collection: bool = True) -> None:
                self.assertFalse(uses_collection)
                result = work()
                future = type("_Future", (), {"result": lambda _self: result})()
                done(future)

        mw = type("_Mw", (), {"taskman": _TaskManager()})()
        with patch(
            "Noteck.modules.pages.NotionClient.from_settings",
            return_value=client,
        ):
            started = trigger_startup_page_refresh(
                mw,
                self._db.path,
                on_done=lambda: completed.append(True),
            )

        self.assertTrue(started)
        self.assertEqual(completed, [True])
        self.assertEqual(
            self._store.get_pages()["parent"].anki_deck_name,
            "Notion::Renamed Parent",
        )
        startup_status = claim_startup_page_refresh_status()
        self.assertIsNotNone(startup_status)
        assert startup_status is not None
        self.assertFalse(startup_status.running)
        self.assertEqual(startup_status.loaded_count, 1)
        self.assertEqual(
            startup_status.ordered_child_ids_by_parent,
            expected_order,
        )
        self.assertIsNone(claim_startup_page_refresh_status())
