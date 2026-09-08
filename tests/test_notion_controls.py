"""Regression coverage for source controls, persistence, and sync eligibility."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from test_parser import _block, _text_item
from Noteck.modules.card_type_overrides import CardTypeOverrideStore
from Noteck.modules.cards_store import CardsStore
from Noteck.modules.db import Database
from Noteck.modules.notion_controls import load_controls, page_controls, parse_controls, save_controls
from Noteck.modules.parser import parse_page_to_cards
from Noteck.modules.notion_client import NotionMarkdownSnapshot
from test_sync import _SYNC_MODULE, _FakeCollection


def toggle(block_id, title, *, color="default"):
    """Build a valid toggle whose answer also supports advanced Cloze."""
    answer = _block("answer-" + block_id, "paragraph", {
        "rich_text": [_text_item("Answer", annotations={"color": "yellow_background"})],
    })
    return _block(block_id, "toggle", {"rich_text": [_text_item(title)], "color": color}, children=(answer,))


class MarkerParsingTests(unittest.TestCase):
    """Exercise marker boundaries, precedence, aliases, and exported formatting."""

    def test_selection_sequence_and_paragraph_independence(self):
        """Cherry-pick filters toggles while paragraph Cloze remains eligible."""
        blocks = [toggle("selected", "🍒 ⌨️ Question"), toggle("plain", "Question"),
                  toggle("excluded", "⌨️ 🍒 🚫 Question"), toggle("cloze", "🧩 Question")]
        blocks.append(_block("paragraph", "paragraph", {
            "rich_text": [_text_item("Cloze", annotations={"color": "yellow_background"})],
        }))

        # page-wide selection must precede per-card parsing
        payloads = parse_page_to_cards("page", blocks, enable_cloze=True)
        self.assertEqual([p.notion_block_id for p in payloads], ["selected", "paragraph"])
        self.assertEqual(payloads[0].card_type, "input")
        self.assertEqual(payloads[0].fields["Front"], "<p>Question</p>")

    def test_type_aliases_and_restored_local_override(self):
        """Every supported type alias wins only while present in Notion."""
        for marker, expected in [("➡️", "basic"), ("[Basic]", "basic"),
                                 ("↔️", "basic_reversed"), ("[Basic + Reversed]", "basic_reversed"),
                                 ("⌨️", "input"), ("[Input]", "input")]:
            with self.subTest(marker=marker):
                payload = parse_page_to_cards("page", [toggle("a", marker + " Question")],
                                              card_type_overrides={"a": "basic_reversed"})[0]
                self.assertEqual(payload.card_type, expected)
                self.assertNotIn(marker, payload.front_html)

        # removing source markers reveals the untouched local preference
        payload = parse_page_to_cards("page", [toggle("a", "Question")], card_type_overrides={"a": "input"})[0]
        self.assertEqual(payload.card_type, "input")

    def test_conflicts_and_cloze_precedence(self):
        """Conflicts warn and fall back while intrinsic Cloze always wins."""
        for title, color, expected in [("➡️ ⌨️ Question", "default", "basic_reversed"),
                                       ("⌨️ 🧩 Question", "default", "cloze"),
                                       ("⌨️ Question", "gray_background", "cloze")]:
            with self.subTest(title=title):
                warnings = []
                payload = parse_page_to_cards("page", [toggle("a", title, color=color)],
                                              default_card_type="basic_reversed", enable_cloze=True, warnings=warnings)[0]
                self.assertEqual(payload.card_type, expected)
                self.assertEqual(len(warnings), 1)

    def test_formatted_prefix_and_literal_later_markers(self):
        """Markers split across formatting runs are stripped without losing links."""
        block = toggle("a", "unused")
        block.raw["toggle"]["rich_text"] = [_text_item("[cherry-"), _text_item("pick] "),
            _text_item("⌨️ Question 🍒", annotations={"bold": True}, href="https://example.com")]

        # content after the first ordinary word is exported literally
        payload = parse_page_to_cards("page", [block])[0]
        self.assertIn("Question 🍒", payload.front_html)
        self.assertIn("<strong>", payload.front_html)
        self.assertIn("https://example.com", payload.front_html)
        self.assertIn("[cherry-", block.raw["toggle"]["rich_text"][0]["plain_text"])
        self.assertFalse(parse_controls(toggle("b", "Question 🍒")).cherry_picked)

    def test_extra_emoji_matches_text_for_paragraph_and_advanced_cloze(self):
        """The Extra emoji follows the existing paragraph and nested-toggle rules."""
        for marker in ("💡", "[extra]", "Extra:"):
            extra = _block("extra", "paragraph", {"rich_text": [_text_item(marker + " Details")]})
            cloze = toggle("a", "🧩 Question")
            paragraph = cloze.children[0]
            payload = parse_page_to_cards("page", [paragraph, extra], enable_cloze=True)[0]
            self.assertEqual(payload.fields["Extra"], "Details")

            # direct nested extra toggles contribute their body only
            extra_toggle = toggle("extra-toggle", marker + " Hidden title")
            from dataclasses import replace
            cloze = replace(cloze, children=(paragraph, extra_toggle))
            payload = parse_page_to_cards("page", [cloze], enable_cloze=True)[0]
            self.assertIn("Answer", payload.fields["Extra"])
            self.assertNotIn("Hidden title", payload.fields["Text"])


    def test_marker_icons_normalize_aliases_and_keep_conflicts_visible(self):
        """The marker column shows distinct source markers rather than derived defaults."""
        control = parse_controls(toggle("a", "[cherry-pick] 🍒 [Input] [Basic] Cloze: [extra] Question"))
        self.assertEqual(control.marker_icons, "🍒 ⌨️ ➡️ 🧩 💡")
        self.assertIn("Conflicting", control.warning)
        self.assertIn("Cherry-pick", control.marker_description)
        self.assertEqual(parse_controls(toggle("a", "Question [Input]")).marker_icons, "")


class MarkerPersistenceTests(unittest.TestCase):
    """Verify source-state removal and both sync routes preserve local preferences."""

    def setUp(self):
        """Create an isolated migrated database with one page."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "test.db")
        self.db.initialize()
        connection = self.db.connect()
        with connection:
            connection.execute("INSERT INTO pages (notion_page_id, anki_deck_name) VALUES ('page', 'Deck')")
        connection.close()

    def test_marker_icons_survive_persistence_and_clear_after_marker_removal(self):
        """Cached rows keep source icons without inventing markers for filtered siblings."""
        save_controls(self.db, "page", page_controls([toggle("a", "[cherry-pick] [Input] Question"), toggle("b", "Question")]))
        self.assertEqual(load_controls(self.db, "page")["a"].marker_icons, "🍒 ⌨️")
        self.assertEqual(load_controls(self.db, "page")["b"].marker_icons, "")

        # the next source snapshot removes icons along with their markers
        save_controls(self.db, "page", page_controls([toggle("a", "Question")]))
        self.assertEqual(load_controls(self.db, "page")["a"].marker_icons, "")

    def test_removal_preserves_manual_state_and_invalidates_siblings(self):
        """Removing markers restores eligibility without overwriting user choices."""
        CardsStore(self.db).set_card_excluded("page", "a", True)
        CardTypeOverrideStore(self.db).set_card_type_override("page", "a", "input")
        save_controls(self.db, "page", page_controls([toggle("a", "🍒 🚫 ➡️ Question"), toggle("b", "Question")]))
        connection = self.db.connect()
        with connection:
            connection.execute("INSERT INTO notion_toggle_snapshots (notion_block_id, notion_page_id, source_hash) VALUES ('b', 'page', 'old')")
        connection.close()

        # page-wide changes must invalidate an unchanged sibling's cached source
        save_controls(self.db, "page", page_controls([toggle("a", "Question"), toggle("b", "Question")]))
        self.assertFalse(load_controls(self.db, "page")["a"].locked)
        self.assertEqual(CardsStore(self.db).get_excluded_block_ids_for_page("page"), {"a"})
        self.assertEqual(CardTypeOverrideStore(self.db).get_card_type_overrides_for_page("page"), {"a": "input"})
        connection = self.db.connect()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM notion_toggle_snapshots").fetchone()[0], 0)
        connection.close()

    def test_full_and_selective_sync_respect_page_wide_controls(self):
        """Both sync routes skip unmarked siblings and restore them on marker removal."""
        for selective in (False, True):
            with self.subTest(selective=selective):
                blocks = [toggle("a", "🍒 ⌨️ Question"), toggle("b", "Question"), toggle("c", "🚫 Question")]

                class Client:
                    """Serve consistent shallow, expanded, and Markdown sources."""

                    def get_page_content(self, page_id):
                        """Return the complete page snapshot."""
                        return blocks

                    def get_page_blocks_shallow(self, page_id):
                        """Return roots in the same order as the Markdown snapshot."""
                        return blocks

                    def get_block_children_recursive(self, block_id):
                        """Expand only the requested root toggle."""
                        return next(block.children for block in blocks if block.block_id == block_id)

                    def get_page_markdown(self, page_id):
                        """Select the full-tree fallback or a trusted Markdown snapshot."""
                        if not selective:
                            return None
                        # preserve exact root order for selective source alignment
                        markdown = "\n".join(
                            "<details>\n<summary>" + block.raw["toggle"]["rich_text"][0]["plain_text"]
                            + "</summary>\nAnswer\n</details>" for block in blocks
                        )
                        return NotionMarkdownSnapshot(page_id, markdown, False, ())

                synced = []

                def capture(**kwargs):
                    """Record eligible payloads without writing to a real Anki collection."""
                    synced.append(kwargs["payload"])
                    return kwargs["stats"], [], False

                # source state must be applied before either route parses a single toggle
                with patch.object(_SYNC_MODULE, "_sync_one_payload", side_effect=capture):
                    result = _SYNC_MODULE._sync_page_content(
                        db=self.db, collection=_FakeCollection(), page_id="page", deck_id=1,
                        client=Client(), stats=_SYNC_MODULE.SyncStats(), default_card_type="basic",
                        card_type_overrides={}, enable_cloze=True,
                    )
                    self.assertEqual(result[1], [])
                    self.assertEqual([(p.notion_block_id, p.card_type) for p in synced], [("a", "input")])
                    synced.clear()
                    blocks[0] = toggle("a", "Question")
                    _SYNC_MODULE._sync_page_content(
                        db=self.db, collection=_FakeCollection(), page_id="page", deck_id=1,
                        client=Client(), stats=_SYNC_MODULE.SyncStats(), default_card_type="basic",
                        card_type_overrides={}, enable_cloze=True,
                    )
                    self.assertEqual({p.notion_block_id for p in synced}, {"a", "b"})

    def test_sync_marker_removal_restores_manual_type_and_preserves_note(self):
        """Sync applies source types temporarily and pauses existing excluded notes."""
        from types import SimpleNamespace

        # use the real payload writer against the existing Anki collection double
        collection = _FakeCollection()
        blocks     = [toggle("a", "[Input] Question")]
        client     = SimpleNamespace(get_page_content=lambda page_id: blocks)
        overrides  = CardTypeOverrideStore(self.db)
        overrides.set_card_type_override("page", "a", "basic_reversed")

        def sync():
            """Run the real full-tree sync with the persisted manual override."""
            stats, errors, cancelled = _SYNC_MODULE._sync_page_content(
                db=self.db, collection=collection, page_id="page", deck_id=1,
                client=client, stats=_SYNC_MODULE.SyncStats(), default_card_type="basic",
                card_type_overrides=overrides.get_card_type_overrides_for_page("page"), enable_cloze=True,
            )
            self.assertEqual(errors, [])
            self.assertFalse(cancelled)
            return stats

        sync()
        self.assertEqual(_SYNC_MODULE._load_existing_cards_for_page(self.db, "page")["a"]["card_type"], "input")

        # source exclusion pauses the mapping without erasing the note or preference
        blocks[0] = toggle("a", "[exclude] Question")
        stats = sync()
        self.assertEqual(stats.cards_skipped, 1)
        self.assertEqual(len(collection.notes), 1)
        self.assertEqual(overrides.get_card_type_overrides_for_page("page"), {"a": "basic_reversed"})

        # removing the exclusion and source type reveals the saved local override
        blocks[0] = toggle("a", "Question")
        sync()
        self.assertEqual(_SYNC_MODULE._load_existing_cards_for_page(self.db, "page")["a"]["card_type"], "basic_reversed")

    def test_upgrade_from_version_two_preserves_manual_preferences(self):
        """The additive schema migration retains pre-existing cards and overrides."""
        from Noteck.modules import db as db_module

        # recreate the previous schema independently of current initialization
        legacy = Database(Path(self.temp.name) / "legacy.db")
        with patch.object(db_module, "MIGRATIONS", db_module.MIGRATIONS[:2]):
            legacy.initialize()
        connection = legacy.connect()
        with connection:
            connection.execute("INSERT INTO pages (notion_page_id, anki_deck_name) VALUES ('page', 'Deck')")
        connection.close()
        CardsStore(legacy).set_card_excluded("page", "a", True)
        CardTypeOverrideStore(legacy).set_card_type_override("page", "a", "input")

        # upgrading creates independent source storage while retaining all local state
        legacy.initialize()
        self.assertEqual(load_controls(legacy, "page"), {})
        self.assertEqual(CardsStore(legacy).get_excluded_block_ids_for_page("page"), {"a"})
        self.assertEqual(CardTypeOverrideStore(legacy).get_card_type_overrides_for_page("page"), {"a": "input"})

class MarkerUiTests(unittest.TestCase):
    """Check source locks at the UI action boundary without requiring native Qt."""

    def setUp(self):
        """Load CardsPage with lightweight Qt and application-shell doubles."""
        import importlib.util
        from types import ModuleType
        from unittest.mock import Mock

        # isolate native imports while exercising the actual CardsPage methods
        qt = ModuleType("aqt.qt")
        for name in ("QComboBox", "QHBoxLayout", "QHeaderView", "QLabel", "QMenu", "QSizePolicy",
                     "QTableWidget", "QTableWidgetItem", "QTimer", "QVBoxLayout"):
            setattr(qt, name, Mock)
        qt.QWidget = object
        shell = ModuleType("Noteck.ui.ui")
        shell.UiContext = object
        path = Path(__file__).resolve().parents[1] / "src/Noteck/ui/cards_ui.py"
        spec = importlib.util.spec_from_file_location("Noteck.ui._marker_test_cards_ui", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"aqt.qt": qt, "Noteck.ui.ui": shell}):
            spec.loader.exec_module(self.module)

        # bypass widget construction so tests focus on lock enforcement
        self.page = object.__new__(self.module.CardsPage)
        self.page._cards_table = Mock()
        self.page._cards_table.cellWidget.return_value = None
        self.page._card_kind_by_block_id = {}
        self.page._excluded_block_ids = set()
        self.page._selected_page_id = Mock(return_value="page")
        self.page._cards_store = Mock()
        self.page._override_store = Mock()
        self.page._build_card_type_combo = Mock()

    def test_source_type_selector_is_disabled_without_mutating_local_override(self):
        """Source type overrides disable the selector and reject queued changes."""
        from unittest.mock import Mock

        # enforce the source-selected type even if a previous manual value exists
        self.page._notion_controls = page_controls([toggle("a", "[Input] Question")])
        self.page._set_row_card_type_widget(row_index=0, page_id="page", block_id="a",
                                           selected_override="basic_reversed", is_excluded=False)
        combo = self.page._build_card_type_combo.return_value
        combo.setEnabled.assert_called_once_with(False)
        self.assertIn("Input", combo.setToolTip.call_args.args[0])
        self.page._on_card_type_selected("page", "a", Mock())
        self.page._override_store.set_card_type_override.assert_not_called()

    def test_source_exclusion_and_cherry_pick_block_local_inclusion(self):
        """Both source eligibility locks prevent manual inclusion writes."""
        for blocks in ([toggle("a", "[exclude] Question")],
                       [toggle("a", "Question"), toggle("b", "[cherry-pick] Question")]):
            self.page._notion_controls = page_controls(blocks)
            self.page._toggle_card_excluded("a")
            self.page._cards_store.set_card_excluded.assert_not_called()

    def test_front_tooltip_distinguishes_source_and_manual_exclusion(self):
        """Front tooltips retain exclusion details without adding a status column."""
        from unittest.mock import Mock

        # keep the source title unchanged and attach explanations to its tooltip
        self.page._notion_controls = page_controls([toggle("a", "[exclude] Question")])
        self.page._item_data_user_role = Mock(return_value=256)
        with patch.object(self.module, "QTableWidgetItem", side_effect=lambda text: Mock(label=text)):
            self.page._set_row_front_item(row_index=0, block_id="a", front_text="Question", is_excluded=True)
        self.assertEqual(self.page._cards_table.setItem.call_count, 2)
        row, column, front = self.page._cards_table.setItem.call_args_list[0].args
        self.assertEqual((row, column, front.label), (0, 1, "Question"))
        self.assertIn("Remove", front.setToolTip.call_args.args[0])
        self.assertIn("Excluded in Noteck", front.setToolTip.call_args.args[0])


    def test_disabled_inclusion_action_explains_source_lock_in_tooltip(self):
        """Disabled menu actions retain their action label and expose lock tooltips."""
        from types import SimpleNamespace
        from unittest.mock import Mock

        # both source exclusion mechanisms explain why unexcluding is unavailable
        entry = SimpleNamespace(type="action", key="toggle_card_excluded", label="Exclude Card")
        self.page._context_menu_schema = SimpleNamespace(on_item=[entry])
        for blocks in ([toggle("a", "[exclude] Question")],
                       [toggle("a", "Question"), toggle("b", "[cherry-pick] Question")]):
            self.page._notion_controls = page_controls(blocks)
            menu = Mock()
            self.page._populate_cards_item_context_menu(menu, "a")
            menu.setToolTipsVisible.assert_called_once_with(True)
            menu.addAction.assert_called_once_with("Unexclude Card")
            action = menu.addAction.return_value
            action.setEnabled.assert_called_once_with(False)
            action.setToolTip.assert_called_once_with(self.page._notion_controls["a"].explanation)
            action.setText.assert_not_called()


    def test_marker_column_precedes_front_and_type_controls(self):
        """Icons occupy the first column while type controls retain their source lock."""
        from unittest.mock import Mock

        # rendering a marker cell must keep its tooltip and row identity
        self.page._notion_controls = page_controls([toggle("a", "[cherry-pick] [Input] Question")])
        self.page._item_data_user_role = Mock(return_value=256)
        with patch.object(self.module, "QTableWidgetItem", side_effect=lambda text: Mock(label=text)):
            self.page._set_row_front_item(row_index=0, block_id="a", front_text="Question", is_excluded=False)
        row, column, marker = self.page._cards_table.setItem.call_args.args
        self.assertEqual((row, column, marker.label), (0, 0, "🍒 ⌨️"))
        self.assertIn("Cherry-pick", marker.setToolTip.call_args.args[0])
        marker.setData.assert_called_once_with(256, "a")

        # shifting the front column must also shift all card-type widgets
        self.page._set_row_card_type_widget(row_index=0, page_id="page", block_id="a",
                                           selected_override=None, is_excluded=False)
        self.assertEqual(self.page._cards_table.setCellWidget.call_args.args[:2], (0, 2))
