"""Tests for Notion → Anki sync orchestration."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.db import Database
from Noteck.modules.notion_client import (
    NotionBlock,
    NotionMarkdownSnapshot,
    NotionPageFetchResult,
    NotionPageSyncData,
)
from Noteck.modules.parser import ToggleCardPayload
from Noteck.modules.sync import (
    SyncResult,
    SyncStats,
    run_notion_sync_with_progress,
    sync_notion_to_anki,
    trigger_sync_with_anki_button,
)

_SYNC_MODULE = sys.modules[sync_notion_to_anki.__module__]


class _FakeModels:
    """Minimal models API surface used by sync logic."""

    def __init__(self) -> None:
        self._models = {
            "Notion (Basic)": {"name": "Notion (Basic)"},
            "Notion (Basic+Reversed)": {"name": "Notion (Basic+Reversed)"},
            "Notion (Input)": {"name": "Notion (Input)"},
            "Notion (Cloze)": {"name": "Notion (Cloze)"},
            "Notion Toggle": {"name": "Notion Toggle"},
            "Notion Toggle (Basic+Reversed)": {"name": "Notion Toggle (Basic+Reversed)"},
            "Notion Toggle (Input)": {"name": "Notion Toggle (Input)"},
            "Notion Toggle (Cloze)": {"name": "Notion Toggle (Cloze)"},
        }

    def by_name(self, name: str) -> dict[str, str] | None:
        return self._models.get(name)


class _FakeDecks:
    """Minimal deck API surface used by sync logic."""

    def __init__(self) -> None:
        self._ids_by_name: dict[str, int] = {}
        self._names_by_id: dict[int, str] = {}
        self._next_id = 1

    def id_for_name(self, name: str) -> int:
        if name not in self._ids_by_name:
            self._ids_by_name[name] = self._next_id
            self._names_by_id[self._next_id] = name
            self._next_id += 1
        return self._ids_by_name[name]

    # Compatibility aliases used by sync helper fallbacks.
    def id(self, name: str) -> int:
        return self.id_for_name(name)

    def idForName(self, name: str) -> int:
        return self.id_for_name(name)

    def name_if_exists(self, deck_id: int) -> str | None:
        return self._names_by_id.get(deck_id)

    def name(self, deck_id: int) -> str | None:
        return self._names_by_id.get(deck_id)

    def get(self, deck_id: int) -> dict[str, object] | None:
        deck_name = self._names_by_id.get(deck_id)
        if deck_name is None:
            return None
        return {"id": deck_id, "name": deck_name}

    def rename(self, deck_id: int, new_name: str) -> None:
        old_name = self._names_by_id.get(deck_id)
        if old_name is None:
            return
        if old_name in self._ids_by_name:
            del self._ids_by_name[old_name]
        self._ids_by_name[new_name] = deck_id
        self._names_by_id[deck_id] = new_name

    def delete_by_id(self, deck_id: int) -> None:
        old_name = self._names_by_id.pop(deck_id, None)
        if old_name is not None:
            self._ids_by_name.pop(old_name, None)


class _FakeNote(dict):
    """Small note object supporting field assignment and id tracking."""

    def __init__(self) -> None:
        super().__init__()
        self.id: int | None = None


class _FakeMedia:
    """Minimal media manager exposing a directory path."""

    def __init__(self, media_dir: Path) -> None:
        self._media_dir = media_dir

    def dir(self) -> str:
        return str(self._media_dir)


class _FakeCollection:
    """Collection double with the methods used by sync.py."""

    def __init__(self, media_dir: Path | None = None) -> None:
        self.models = _FakeModels()
        self.decks = _FakeDecks()
        self._next_note_id = 1000
        self.notes: dict[int, _FakeNote] = {}
        self.media = _FakeMedia(media_dir) if media_dir is not None else None
        self.empty_cards_report = SimpleNamespace(notes=[])
        self.removed_card_ids: list[int] = []

    def new_note(self, model: dict[str, str]) -> _FakeNote:
        _ = model
        return _FakeNote()

    def add_note(self, note: _FakeNote, deck_id: int) -> None:
        _ = deck_id
        note.id = self._next_note_id
        self._next_note_id += 1
        self.notes[note.id] = note

    def get_note(self, note_id: int) -> _FakeNote | None:
        return self.notes.get(note_id)

    def find_notes(self, query: str) -> list[int]:
        """Support exact Noteck block-id field searches used for mapping repair."""
        prefix = '"Notion Block ID:'
        if not query.startswith(prefix) or not query.endswith('"'):
            return []
        block_id = query[len(prefix):-1].replace('\\"', '"').replace("\\\\", "\\")
        return [
            note_id
            for note_id, note in self.notes.items()
            if note.get("Notion Block ID") == block_id
        ]

    def update_note(self, note: _FakeNote) -> None:
        if note.id is None:
            raise RuntimeError("note id missing")
        self.notes[note.id] = note

    def get_empty_cards(self) -> SimpleNamespace:
        """Return Anki-like empty-card report data for cleanup tests."""
        return self.empty_cards_report

    def remove_cards_and_orphaned_notes(self, card_ids: list[int]) -> None:
        """Record card IDs removed by cloze reconciliation."""
        self.removed_card_ids.extend(card_ids)


class _FakeMw:
    """Anki main-window double exposing collection and reset hook."""

    def __init__(self, collection: _FakeCollection) -> None:
        self.col = collection
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1


class _FakeFuture:
    """Simple Future-like object used by the fake task manager."""

    def __init__(self, value: object = None, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    def result(self) -> object:
        if self._error is not None:
            raise self._error
        return self._value


class _FakeTaskManager:
    """Task manager double that executes background tasks immediately."""

    def run_in_background(self, task: object, on_done: object, uses_collection: bool = True) -> _FakeFuture:
        _ = uses_collection
        try:
            value = task()
            future = _FakeFuture(value=value)
        except Exception as exc:
            future = _FakeFuture(error=exc)
        if on_done is not None:
            on_done(future)
        return future

    def run_on_main(self, closure: object) -> None:
        closure()


class _FakeProgress:
    """Progress dialog double exposing the APIs used by the sync runner."""

    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.finish_calls = 0
        self._cancel = False

    def start(self, **kwargs: object) -> None:
        self.start_calls.append(kwargs)

    def update(self, **kwargs: object) -> None:
        self.update_calls.append(kwargs)

    def finish(self) -> None:
        self.finish_calls += 1

    def want_cancel(self) -> bool:
        return self._cancel


class _FakeMwWithTaskman(_FakeMw):
    """Main-window double that includes task manager and progress manager."""

    def __init__(self, collection: _FakeCollection) -> None:
        super().__init__(collection)
        self.taskman = _FakeTaskManager()
        self.progress = _FakeProgress()
        self.app = object()


class _FakeNotionClient:
    """Notion client double used to bypass network calls."""

    def __init__(
        self,
        page_last_edited_time: str = "2026-02-04T00:00:00.000Z",
        toggle_last_edited_time: str = "2026-02-04T00:00:00.000Z",
    ) -> None:
        self._page_last_edited_time = page_last_edited_time
        self._toggle_last_edited_time = toggle_last_edited_time

    def get_page_last_edited_time(self, page_id: str) -> str:
        _ = page_id
        return self._page_last_edited_time

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        return [self._toggle_block(parent_id=page_id)]

    def get_page_content(self, page_id: str) -> list[NotionBlock]:
        return [self._toggle_block(parent_id=page_id)]

    def get_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        _ = block_id
        return []

    def get_block(self, block_id: str) -> NotionBlock:
        _ = block_id
        return self._toggle_block(parent_id="page-1")

    def _toggle_block(self, parent_id: str) -> NotionBlock:
        """Return a normalized toggle block with last_edited_time set."""
        raw = {
            "object": "block",
            "id": "block-1",
            "type": "toggle",
            "has_children": True,
            "last_edited_time": self._toggle_last_edited_time,
            "parent": {"type": "page_id", "page_id": parent_id},
            "toggle": {"rich_text": [{"type": "text", "plain_text": "title", "text": {"content": "title"}}]},
        }
        return NotionBlock(
            block_id="block-1",
            block_type="toggle",
            has_children=True,
            parent_id=parent_id,
            parent_type="page_id",
            raw=raw,
            children=(),
        )


class _FakeNotionClientWithParagraphs(_FakeNotionClient):
    """Notion client double that returns top-level paragraph blocks."""

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        _ = page_id
        return self.get_page_content(page_id)

    def get_page_content(self, page_id: str) -> list[NotionBlock]:
        _ = page_id
        return [
            self._paragraph_block("cloze-excluded", "Excluded"),
            self._paragraph_block("cloze-included", "Included"),
        ]

    def _paragraph_block(self, block_id: str, text: str) -> NotionBlock:
        """Return a paragraph rich-text block with cloze marker annotation."""
        raw = {
            "object": "block",
            "id": block_id,
            "type": "paragraph",
            "has_children": False,
            "last_edited_time": self._toggle_last_edited_time,
            "parent": {"type": "page_id", "page_id": "page-1"},
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": text,
                        "text": {"content": text},
                        "annotations": {
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "yellow_background",
                        },
                    }
                ]
            },
        }
        return NotionBlock(
            block_id=block_id,
            block_type="paragraph",
            has_children=False,
            parent_id="page-1",
            parent_type="page_id",
            raw=raw,
            children=(),
        )


class _MutableToggleClient(_FakeNotionClient):
    """Return mutable toggle content while keeping all Notion timestamps fixed."""

    def __init__(self) -> None:
        super().__init__()
        self.title = "Original title"
        self.body = "Original body"

    def _toggle_block(self, parent_id: str) -> NotionBlock:
        block = super()._toggle_block(parent_id)
        raw = dict(block.raw)
        raw["toggle"] = {
            "rich_text": [
                {
                    "type": "text",
                    "plain_text": self.title,
                    "text": {"content": self.title},
                }
            ]
        }
        return NotionBlock(
            block_id=block.block_id,
            block_type=block.block_type,
            has_children=block.has_children,
            parent_id=block.parent_id,
            parent_type=block.parent_type,
            raw=raw,
            children=(),
        )

    def get_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        raw = {
            "object": "block",
            "id": "body-1",
            "type": "paragraph",
            "has_children": False,
            "last_edited_time": self._toggle_last_edited_time,
            "parent": {"type": "block_id", "block_id": block_id},
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": self.body,
                        "text": {"content": self.body},
                        "annotations": {
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "default",
                        },
                    }
                ]
            },
        }
        return [
            NotionBlock(
                block_id="body-1",
                block_type="paragraph",
                has_children=False,
                parent_id=block_id,
                parent_type="block_id",
                raw=raw,
                children=(),
            )
        ]

    def get_page_content(self, page_id: str) -> list[NotionBlock]:
        """Return the fully expanded tree produced by the real client."""
        toggle = self._toggle_block(parent_id=page_id)
        return [
            NotionBlock(
                block_id=toggle.block_id,
                block_type=toggle.block_type,
                has_children=toggle.has_children,
                parent_id=toggle.parent_id,
                parent_type=toggle.parent_type,
                raw=toggle.raw,
                children=tuple(self.get_block_children_recursive(toggle.block_id)),
            )
        ]


class _SelectiveMarkdownClient(_FakeNotionClient):
    """Expose Markdown, shallow roots, and tracked recursive toggle retrieval."""

    def __init__(self) -> None:
        super().__init__()
        self.body_by_id = {
            "block-1": "First answer",
            "block-2": "Second answer",
        }
        self.recursive_calls: list[str] = []
        self.full_page_fetches = 0

    def get_page_markdown(self, page_id: str) -> NotionMarkdownSnapshot:
        return NotionMarkdownSnapshot(
            page_id=page_id,
            markdown=(
                "<details>\n"
                "<summary>First question</summary>\n"
                f"\t{self.body_by_id['block-1']}\n"
                "</details>\n"
                "<details>\n"
                "<summary>Second question</summary>\n"
                f"\t{self.body_by_id['block-2']}\n"
                "</details>"
            ),
            truncated=False,
            unknown_block_ids=(),
        )

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        return [
            self._named_toggle("block-1", "First question", page_id),
            self._named_toggle("block-2", "Second question", page_id),
        ]

    def get_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        self.recursive_calls.append(block_id)
        body = self.body_by_id[block_id]
        raw = {
            "object": "block",
            "id": f"{block_id}-body",
            "type": "paragraph",
            "has_children": False,
            "parent": {"type": "block_id", "block_id": block_id},
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": body,
                        "text": {"content": body},
                    }
                ]
            },
        }
        return [
            NotionBlock(
                block_id=f"{block_id}-body",
                block_type="paragraph",
                has_children=False,
                parent_id=block_id,
                parent_type="block_id",
                raw=raw,
                children=(),
            )
        ]

    def get_page_content(self, page_id: str) -> list[NotionBlock]:
        self.full_page_fetches += 1
        blocks: list[NotionBlock] = []
        for toggle in self.get_page_blocks_shallow(page_id):
            blocks.append(
                NotionBlock(
                    block_id=toggle.block_id,
                    block_type=toggle.block_type,
                    has_children=toggle.has_children,
                    parent_id=toggle.parent_id,
                    parent_type=toggle.parent_type,
                    raw=toggle.raw,
                    children=tuple(self.get_block_children_recursive(toggle.block_id)),
                )
            )
        return blocks

    def _named_toggle(self, block_id: str, title: str, page_id: str) -> NotionBlock:
        """Build one shallow root toggle with stable identity and current title."""
        raw = {
            "object": "block",
            "id": block_id,
            "type": "toggle",
            "has_children": True,
            "parent": {"type": "page_id", "page_id": page_id},
            "toggle": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": title,
                        "text": {"content": title},
                    }
                ]
            },
        }
        return NotionBlock(
            block_id=block_id,
            block_type="toggle",
            has_children=True,
            parent_id=page_id,
            parent_type="page_id",
            raw=raw,
            children=(),
        )


class _SelectiveAdvancedClozeClient(_FakeNotionClient):
    """Expose one unchanged advanced cloze toggle through selective sync."""

    def __init__(self) -> None:
        super().__init__()
        self.recursive_calls = 0

    def get_page_markdown(self, page_id: str) -> NotionMarkdownSnapshot:
        return NotionMarkdownSnapshot(
            page_id=page_id,
            markdown=(
                "<details>\n"
                "<summary>[cloze] Recovery</summary>\n"
                "\tMarked answer\n"
                "</details>"
            ),
            truncated=False,
            unknown_block_ids=(),
        )

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        return [self._toggle(page_id, children=())]

    def get_block_children_recursive(self, block_id: str) -> list[NotionBlock]:
        self.recursive_calls += 1
        return [
            NotionBlock(
                block_id=f"{block_id}-body",
                block_type="paragraph",
                has_children=False,
                parent_id=block_id,
                parent_type="block_id",
                raw={
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "plain_text": "Marked answer",
                                "text": {"content": "Marked answer"},
                                "annotations": {"color": "yellow_background"},
                            }
                        ]
                    },
                },
                children=(),
            )
        ]

    def get_page_content(self, page_id: str) -> list[NotionBlock]:
        return [self._toggle(page_id, children=tuple(self.get_block_children_recursive("advanced-toggle")))]

    @staticmethod
    def _toggle(page_id: str, *, children: tuple[NotionBlock, ...]) -> NotionBlock:
        """Build the stable advanced-cloze root used across all sync runs."""
        return NotionBlock(
            block_id="advanced-toggle",
            block_type="toggle",
            has_children=True,
            parent_id=page_id,
            parent_type="page_id",
            raw={
                "type": "toggle",
                "toggle": {
                    "rich_text": [
                        {
                            "type": "text",
                            "plain_text": "[cloze] Recovery",
                            "text": {"content": "[cloze] Recovery"},
                        }
                    ]
                },
            },
            children=children,
        )


class _QueuedPageClient(_FakeNotionClient):
    """Return page inputs in one batch and reject sequential source retrieval."""

    def __init__(self) -> None:
        super().__init__()
        self.requested_page_ids: tuple[str, ...] = ()

    def get_pages_sync_data(
        self,
        page_ids: object,
        *,
        progress_callback: object = None,
    ) -> dict[str, NotionPageFetchResult]:
        self.requested_page_ids = tuple(str(page_id) for page_id in page_ids)
        results: dict[str, NotionPageFetchResult] = {}
        for completed, page_id in enumerate(self.requested_page_ids, start=1):
            results[page_id] = NotionPageFetchResult(
                page_id=page_id,
                data=NotionPageSyncData(
                    page_id=page_id,
                    last_edited_time="2026-07-19T00:00:00.000Z",
                    markdown_snapshot=NotionMarkdownSnapshot(
                        page_id=page_id,
                        markdown="",
                        truncated=False,
                        unknown_block_ids=(),
                    ),
                    shallow_blocks=(),
                ),
            )
            if callable(progress_callback):
                progress_callback(completed, len(self.requested_page_ids))
        return results

    def get_page_last_edited_time(self, page_id: str) -> str:
        raise AssertionError(f"Sequential metadata fetch used for {page_id}")

    def get_page_markdown(self, page_id: str) -> NotionMarkdownSnapshot:
        raise AssertionError(f"Sequential Markdown fetch used for {page_id}")

    def get_page_blocks_shallow(self, page_id: str) -> list[NotionBlock]:
        raise AssertionError(f"Sequential shallow fetch used for {page_id}")


class SyncTests(unittest.TestCase):
    """Validate create/update/no-op sync behavior with mocked dependencies."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db_path = Path(self._temp_dir.name) / "sync.db"
        self._media_dir = Path(self._temp_dir.name) / "media"
        self._media_dir.mkdir(parents=True, exist_ok=True)
        self._db = Database(self._db_path)
        self._db.initialize()
        # Most tests exercise steady-state sync; dedicated tests override this upgrade marker.
        self._db.set_setting(
            _SYNC_MODULE._TOGGLE_REFRESH_REVISION_SETTING_KEY,
            _SYNC_MODULE._TOGGLE_REFRESH_REVISION,
        )
        # Reset module-level run lock so tests are independent.
        trigger_sync_with_anki_button.__globals__["_sync_is_running"] = False
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO pages (notion_page_id, anki_deck_name, sync_enabled)
                VALUES ('page-1', 'Notion::Page 1', 1)
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _payload(self, content_hash: str = "hash-1") -> ToggleCardPayload:
        return ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html="<p>back</p>",
            content_hash=content_hash,
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

    def test_cloze_marker_sync_status_survives_database_reopen(self) -> None:
        """The settings warning must compare against durable last-sync state."""
        _SYNC_MODULE._set_cloze_marker_colors(self._db, ["yellow", "green"])

        reopened_db = Database(self._db_path)
        self.assertFalse(
            _SYNC_MODULE.cloze_marker_colors_need_sync(
                reopened_db, ["yellow", "green"]
            )
        )
        self.assertTrue(
            _SYNC_MODULE.cloze_marker_colors_need_sync(reopened_db, ["yellow"])
        )

    def test_sync_removes_only_obsolete_cards_from_updated_cloze_note(self) -> None:
        """Removed cloze ordinals must not leave empty cards or affect other notes."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion (Cloze)"})
        collection.add_note(existing_note, deck_id=1)
        collection.empty_cards_report = SimpleNamespace(
            notes=[
                SimpleNamespace(note_id=existing_note.id, card_ids=[101, 102]),
                SimpleNamespace(note_id=9999, card_ids=[201]),
            ]
        )

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "cloze", "old-hash"),
            )
            connection.commit()
        finally:
            connection.close()

        cloze_payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            card_type="cloze",
            model_name="Notion (Cloze)",
            fields={
                "Text": "{{c1::Current deletion}}",
                "Extra": "",
                "Notion Block ID": "block-1",
                "Notion Card Background": "",
            },
            content_hash="new-hash",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[cloze_payload],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        self.assertEqual(collection.removed_card_ids, [101, 102])

    def test_sync_creates_note_and_mapping(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload()],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_created, 1)
        self.assertEqual(result.stats.cards_seen, 1)
        self.assertEqual(len(collection.notes), 1)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT notion_block_id, anki_note_id FROM cards WHERE notion_block_id = 'block-1'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["notion_block_id"]), "block-1")
        self.assertGreater(int(row["anki_note_id"]), 0)

    def test_sync_warns_for_markerless_cloze_without_creating_an_anki_note(self) -> None:
        """Invalid cloze content is a card-local warning and must not reach Anki."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        invalid_cloze = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="markerless-cloze",
            card_type="cloze",
            model_name="Notion (Cloze)",
            fields={
                "Text": "<p>Plain text without a cloze deletion</p>",
                "Extra": "",
                "Notion Block ID": "markerless-cloze",
            },
            content_hash="markerless-cloze",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[invalid_cloze],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.errors, ())
        self.assertEqual(collection.notes, {})
        self.assertEqual(result.stats.cards_warned, 1)
        self.assertEqual(result.stats.cards_skipped, 1)
        self.assertEqual([warning.code for warning in result.warnings], ["invalid_cloze_card"])
        self.assertEqual(result.warnings[0].page_id, "page-1")
        self.assertEqual(result.warnings[0].block_id, "markerless-cloze")
        self.assertIn("at least one deletion", result.warnings[0].message)

    def test_sync_reset_failure_returns_structured_warning(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        mw.reset = Mock(side_effect=RuntimeError("reset failed"))

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload()],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["anki_ui_refresh_failed"],
        )
        self.assertIn("reset failed", result.warnings[0].message)
        self.assertIsNone(result.warnings[0].page_id)
        self.assertIsNone(result.warnings[0].block_id)

    def test_sync_persists_payload_card_type_to_mapping(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html="<p>back</p>",
            card_type="input",
            model_name="Notion Toggle (Input)",
            fields={
                "Front": "<p>front</p>",
                "Back": "<p>back</p>",
                "Expected Answer": "back",
                "Notion Block ID": "block-1",
            },
            content_hash="hash-input",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT card_type FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["card_type"]), "input")

    def test_sync_skips_excluded_existing_card_without_parsing_or_syncing(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash, excluded
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-old", 1),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(_SYNC_MODULE, "parse_page_to_cards") as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_skipped, 1)
        self.assertEqual(result.stats.cards_created, 0)
        self.assertEqual(result.stats.cards_updated, 0)
        parse_mock.assert_not_called()

    def test_sync_changed_page_skips_excluded_cloze_before_parsing(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash, excluded
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("cloze-excluded", "page-1", None, "cloze", "hash-old", 1),
            )
            connection.commit()
        finally:
            connection.close()
        self._db.set_setting("enable_cloze_parsing", "1")

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClientWithParagraphs(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_skipped, 1)
        parse_mock.assert_called_once()
        include_block_ids = parse_mock.call_args.kwargs.get("include_block_ids")
        self.assertEqual(set(include_block_ids), {"cloze-included"})

    def test_sync_matching_page_timestamp_parses_non_excluded_toggle(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash, excluded
                )
                VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)
                """,
                (
                    "block-excluded", "page-1", existing_note.id, "basic", "hash-1", 1,
                    "block-1", "page-1", None, "basic", "hash-2", 0,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html="<p>back</p>",
            content_hash="hash-2-new",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        fake_client = _FakeNotionClient(page_last_edited_time="2026-02-04T00:00:00.000Z")
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=fake_client,
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_created, 1)
        self.assertEqual(result.stats.cards_seen, 1)
        parse_mock.assert_called_once()
        self.assertIsNone(parse_mock.call_args.kwargs.get("include_block_ids"))

        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["anki_note_id"])

    def test_sync_stale_mapping_is_warning_and_detaches_without_deleting_note(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)
        self._db.set_setting(_SYNC_MODULE._TOGGLE_REFRESH_REVISION_SETTING_KEY, "legacy")

        connection = self._db.connect()
        try:
            connection.execute(
                "UPDATE pages SET last_seen_notion_edit_time = ? WHERE notion_page_id = ?",
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type,
                    content_hash, last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "missing-block", "page-1", existing_note.id, "basic",
                    "legacy-hash", "2026-02-04T00:00:00.000Z",
                ),
            )
            connection.execute(
                """
                INSERT INTO card_type_overrides (notion_block_id, notion_page_id, card_type)
                VALUES (?, ?, ?)
                """,
                ("missing-block", "page-1", "input"),
            )
            connection.commit()
        finally:
            connection.close()

        client = Mock()
        client.get_page_last_edited_time.return_value = "2026-02-04T00:00:00.000Z"
        client.get_page_content.return_value = []
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.errors, ())
        self.assertEqual([warning.code for warning in result.warnings], ["source_no_longer_syncable"])
        self.assertEqual(result.stats.cards_detached, 1)
        self.assertEqual(result.stats.cards_warned, 1)
        self.assertIn(existing_note.id, collection.notes)
        self.assertEqual(
            self._db.get_setting(_SYNC_MODULE._TOGGLE_REFRESH_REVISION_SETTING_KEY),
            _SYNC_MODULE._TOGGLE_REFRESH_REVISION,
        )

    def test_paragraph_cloze_cleanup_preserves_advanced_toggle_mapping(self) -> None:
        """Paragraph reconciliation must not detach advanced-toggle cloze cards."""
        collection = _FakeCollection()
        advanced_note = collection.new_note({"name": "Notion (Cloze)"})
        stale_paragraph_note = collection.new_note({"name": "Notion (Cloze)"})
        collection.add_note(advanced_note, deck_id=1)
        collection.add_note(stale_paragraph_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.executemany(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, 'page-1', ?, 'cloze', ?)
                """,
                (
                    ("advanced-toggle", advanced_note.id, "advanced-hash"),
                    ("removed-paragraph", stale_paragraph_note.id, "paragraph-hash"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        existing_cards = _SYNC_MODULE._load_existing_cards_for_page(self._db, "page-1")
        warnings = []
        stats = _SYNC_MODULE._detach_stale_paragraph_cloze_mappings(
            db=self._db,
            stats=SyncStats(),
            warnings=warnings,
            page_id="page-1",
            existing_cards=existing_cards,
            current_cloze_ids=set(),
            root_toggle_ids={"advanced-toggle"},
        )

        remaining_cards = _SYNC_MODULE._load_existing_cards_for_page(self._db, "page-1")
        self.assertIn("advanced-toggle", remaining_cards)
        self.assertNotIn("removed-paragraph", remaining_cards)
        self.assertEqual(stats.cards_detached, 1)
        self.assertEqual([warning.block_id for warning in warnings], ["removed-paragraph"])
        # Detachment removes only Noteck metadata; both Anki notes remain intact.
        self.assertIn(advanced_note.id, collection.notes)
        self.assertIn(stale_paragraph_note.id, collection.notes)

    def test_cloze_revision_refreshes_root_toggles_to_repair_detached_mappings(self) -> None:
        """A cloze parser revision must bypass unchanged root-toggle snapshots once."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        self._db.set_setting("enable_cloze_parsing", "1")
        self._db.set_setting(_SYNC_MODULE._CLOZE_REFRESH_REVISION_SETTING_KEY, "legacy")

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "_sync_page_content",
            return_value=(SyncStats(), [], False),
        ) as sync_page_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertTrue(sync_page_mock.call_args.kwargs["force_toggle_refresh"])
        self.assertTrue(sync_page_mock.call_args.kwargs["force_cloze_refresh"])
        self.assertEqual(
            self._db.get_setting(_SYNC_MODULE._CLOZE_REFRESH_REVISION_SETTING_KEY),
            _SYNC_MODULE._CLOZE_REFRESH_REVISION,
        )

    def test_deleted_deck_recreates_unchanged_advanced_cloze_card(self) -> None:
        """Advanced cloze mappings survive reconciliation and repair a missing note."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        client = _SelectiveAdvancedClozeClient()
        self._db.set_setting("enable_cloze_parsing", "1")

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            created = sync_notion_to_anki(mw=mw, db_path=self._db_path)
            unchanged = sync_notion_to_anki(mw=mw, db_path=self._db_path)

            mapping = _SYNC_MODULE._load_existing_cards_for_page(
                self._db, "page-1"
            )["advanced-toggle"]
            deleted_note_id = int(mapping["anki_note_id"])
            del collection.notes[deleted_note_id]

            recreated = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(created.ok)
        self.assertEqual(created.stats.cards_created, 1)
        self.assertTrue(unchanged.ok)
        self.assertEqual(unchanged.stats.cards_unchanged, 1)
        self.assertTrue(recreated.ok)
        self.assertEqual(recreated.stats.cards_created, 1)
        self.assertEqual(recreated.stats.cards_missing_note, 1)
        repaired_mapping = _SYNC_MODULE._load_existing_cards_for_page(
            self._db, "page-1"
        )["advanced-toggle"]
        self.assertNotEqual(int(repaired_mapping["anki_note_id"]), deleted_note_id)
        self.assertIn(int(repaired_mapping["anki_note_id"]), collection.notes)

    def test_markdown_snapshots_fetch_only_new_or_changed_toggles(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        client = _SelectiveMarkdownClient()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            first = sync_notion_to_anki(mw=mw, db_path=self._db_path)
            second = sync_notion_to_anki(mw=mw, db_path=self._db_path)
            client.body_by_id["block-2"] = "Changed second answer"
            third = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(first.ok)
        self.assertEqual(first.stats.cards_created, 2)
        self.assertTrue(second.ok)
        self.assertEqual(second.stats.cards_unchanged, 2)
        self.assertTrue(third.ok)
        self.assertEqual(third.stats.cards_updated, 1)
        self.assertEqual(third.stats.cards_unchanged, 1)
        self.assertEqual(
            client.recursive_calls,
            ["block-1", "block-2", "block-2"],
        )
        self.assertEqual(client.full_page_fetches, 0)

        connection = self._db.connect()
        try:
            page_row = connection.execute(
                "SELECT content_hash FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
            snapshot_rows = connection.execute(
                """
                SELECT notion_block_id, source_hash
                FROM notion_toggle_snapshots
                WHERE notion_page_id = ?
                ORDER BY notion_block_id
                """,
                ("page-1",),
            ).fetchall()
        finally:
            connection.close()
        self.assertIsNotNone(page_row)
        self.assertTrue(str(page_row["content_hash"]))
        self.assertEqual(
            [str(row["notion_block_id"]) for row in snapshot_rows],
            ["block-1", "block-2"],
        )

    def test_ambiguous_markdown_alignment_falls_back_to_full_page_tree(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        client = _SelectiveMarkdownClient()
        client.get_page_markdown = Mock(
            return_value=NotionMarkdownSnapshot(
                page_id="page-1",
                markdown="<details>\n<summary>Only one</summary>\n\tAnswer\n</details>",
                truncated=False,
                unknown_block_ids=(),
            )
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_created, 2)
        self.assertEqual(client.full_page_fetches, 1)
        connection = self._db.connect()
        try:
            card_row = connection.execute(
                "SELECT notion_block_id FROM cards WHERE notion_block_id = ?",
                ("missing-block",),
            ).fetchone()
            override_row = connection.execute(
                "SELECT notion_block_id FROM card_type_overrides WHERE notion_block_id = ?",
                ("missing-block",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(card_row)
        self.assertIsNone(override_row)

    def test_sync_empty_toggle_is_warning_and_detaches_existing_mapping(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type,
                    content_hash, last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "old-hash", "old-time"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual([warning.code for warning in result.warnings], ["empty_toggle_content"])
        self.assertEqual(result.stats.cards_detached, 1)
        self.assertIn(existing_note.id, collection.notes)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT notion_block_id FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
            page_row = connection.execute(
                "SELECT last_seen_notion_edit_time FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(row)
        self.assertEqual(str(page_row["last_seen_notion_edit_time"]), "2026-02-04T00:00:00.000Z")

    def test_sync_parser_exception_is_warning_and_preserves_mapping(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type,
                    content_hash, last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "old-hash", "old-time"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            side_effect=ValueError("bad card content"),
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual([warning.code for warning in result.warnings], ["card_parse_failed"])
        self.assertEqual(result.stats.cards_detached, 0)
        self.assertIn(existing_note.id, collection.notes)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(int(row["anki_note_id"]), existing_note.id)

    def test_sync_auto_converts_existing_note_when_card_type_changes(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-old"),
            )
            connection.execute(
                """
                UPDATE pages
                SET default_card_type = ?
                WHERE notion_page_id = ?
                """,
                ("input", "page-1"),
            )
            connection.commit()
        finally:
            connection.close()

        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html="<p>back</p>",
            card_type="input",
            model_name="Notion Toggle (Input)",
            fields={
                "Front": "<p>front</p>",
                "Back": "<p>back</p>",
                "Expected Answer": "back",
                "Notion Block ID": "block-1",
            },
            content_hash="hash-new",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)

        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id, card_type FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["card_type"]), "input")
        self.assertNotEqual(int(row["anki_note_id"]), int(existing_note.id))

    def test_sync_unchanged_page_still_converts_when_per_card_override_changes(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-old"),
            )
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO card_type_overrides (notion_block_id, notion_page_id, card_type)
                VALUES (?, ?, ?)
                """,
                ("block-1", "page-1", "input"),
            )
            connection.commit()
        finally:
            connection.close()

        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html="<p>back</p>",
            card_type="input",
            model_name="Notion Toggle",
            fields={
                "Front": "<p>front</p>",
                "Back": "<p>back</p>",
                "Notion Block ID": "block-1",
            },
            content_hash="hash-new",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ), patch.object(
            _SYNC_MODULE,
            "_model_by_name",
            return_value={"name": "Notion Toggle"},
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT card_type FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["card_type"]), "input")

    def test_sync_override_applies_only_target_block_when_page_unchanged(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note_a = collection.new_note({"name": "Notion Toggle"})
        existing_note_b = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note_a, deck_id=1)
        collection.add_note(existing_note_b, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
                """,
                (
                    "block-1", "page-1", existing_note_a.id, "basic", "hash-a",
                    "block-2", "page-1", existing_note_b.id, "basic", "hash-b",
                ),
            )
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO card_type_overrides (notion_block_id, notion_page_id, card_type)
                VALUES (?, ?, ?)
                """,
                ("block-1", "page-1", "input"),
            )
            connection.commit()
        finally:
            connection.close()

        payload_a = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front a</p>",
            back_html="<p>back a</p>",
            card_type="input",
            model_name="Notion Toggle",
            fields={
                "Front": "<p>front a</p>",
                "Back": "<p>back a</p>",
                "Notion Block ID": "block-1",
            },
            content_hash="hash-a-new",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )
        payload_b = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-2",
            front_html="<p>front b</p>",
            back_html="<p>back b</p>",
            card_type="basic",
            model_name="Notion Toggle",
            fields={
                "Front": "<p>front b</p>",
                "Back": "<p>back b</p>",
                "Notion Block ID": "block-2",
            },
            content_hash="hash-b-new",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload_a, payload_b],
        ), patch.object(
            _SYNC_MODULE,
            "_model_by_name",
            return_value={"name": "Notion Toggle"},
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        connection = self._db.connect()
        try:
            rows = connection.execute(
                "SELECT notion_block_id, card_type FROM cards WHERE notion_page_id = ? ORDER BY notion_block_id",
                ("page-1",),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(str(rows[0]["notion_block_id"]), "block-1")
        self.assertEqual(str(rows[0]["card_type"]), "input")
        self.assertEqual(str(rows[1]["notion_block_id"]), "block-2")
        self.assertEqual(str(rows[1]["card_type"]), "basic")

    def test_sync_backfills_page_deck_id_for_legacy_rows(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload()],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_deck_id, anki_deck_name FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["anki_deck_id"])
        self.assertEqual(str(row["anki_deck_name"]), "Notion::Page 1")

    def test_sync_updates_page_deck_name_when_deck_is_renamed(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        deck_id = collection.decks.id_for_name("Notion::Page 1")
        collection.decks.rename(deck_id, "Notion::Moved::Page 1")

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET anki_deck_id = ?, anki_deck_name = ?
                WHERE notion_page_id = ?
                """,
                (deck_id, "Notion::Page 1", "page-1"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload()],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_deck_id, anki_deck_name FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(int(row["anki_deck_id"]), deck_id)
        self.assertEqual(str(row["anki_deck_name"]), "Notion::Moved::Page 1")

    def test_sync_recreates_missing_deck_using_latest_stored_name(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        stale_id = collection.decks.id_for_name("Notion::Removed")
        collection.decks.delete_by_id(stale_id)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET anki_deck_id = ?, anki_deck_name = ?
                WHERE notion_page_id = ?
                """,
                (stale_id, "Notion::Latest", "page-1"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload()],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_deck_id, anki_deck_name FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        recreated_id = int(row["anki_deck_id"])
        self.assertNotEqual(recreated_id, stale_id)
        self.assertEqual(str(row["anki_deck_name"]), "Notion::Latest")
        self.assertEqual(collection.decks.name_if_exists(recreated_id), "Notion::Latest")

    def test_sync_ignores_no_deck_placeholder_and_keeps_stored_deck_name(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        stale_id = collection.decks.id_for_name("Notion::Removed")
        collection.decks.delete_by_id(stale_id)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET anki_deck_id = ?, anki_deck_name = ?
                WHERE notion_page_id = ?
                """,
                (stale_id, "Notion::Parent::Page 1", "page-1"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(collection.decks, "name_if_exists", return_value="[no deck]"), patch.object(
            _SYNC_MODULE, "ensure_notion_toggle_model"
        ), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload()],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_deck_id, anki_deck_name FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        recreated_id = int(row["anki_deck_id"])
        self.assertNotEqual(recreated_id, stale_id)
        self.assertEqual(str(row["anki_deck_name"]), "Notion::Parent::Page 1")

    def test_sync_localizes_images_and_renders_mermaid_to_media(self) -> None:
        collection = _FakeCollection(media_dir=self._media_dir)
        mw = _FakeMw(collection)
        mermaid_source = "graph TD\nA --> B"
        encoded_mermaid = base64.urlsafe_b64encode(mermaid_source.encode("utf-8")).decode("ascii")
        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html=(
                '<figure class="notion-image"><img src="https://example.com/path/sample.png" alt="img"/></figure>'
                '<figure class="notion-mermaid"><div class="notion-mermaid-source" '
                f'data-mermaid="{encoded_mermaid}"><pre class="code"><code class="language-mermaid">'
                "graph TD\nA --&gt; B"
                "</code></pre></div><figcaption>Flow</figcaption></figure>"
            ),
            content_hash="hash-media-1",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ), patch.object(
            _SYNC_MODULE,
            "_download_bytes",
            return_value=b"PNG",
        ), patch.object(
            _SYNC_MODULE,
            "_render_mermaid_svg",
            return_value=b"<svg></svg>",
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_created, 1)
        self.assertEqual(len(collection.notes), 1)

        saved_note = next(iter(collection.notes.values()))
        back_html = str(saved_note.get("Back", ""))
        image_name = _SYNC_MODULE._image_media_filename("https://example.com/path/sample.png")
        mermaid_light_name = _SYNC_MODULE._mermaid_media_filename(mermaid_source, variant="light")
        mermaid_dark_name = _SYNC_MODULE._mermaid_media_filename(mermaid_source, variant="dark")

        self.assertIn(f'src="{image_name}"', back_html)
        self.assertIn(f'src="{mermaid_light_name}"', back_html)
        self.assertIn(f'src="{mermaid_dark_name}"', back_html)
        self.assertIn('class="code notion-mermaid-diagram"', back_html)
        self.assertTrue((self._media_dir / image_name).exists())
        self.assertTrue((self._media_dir / mermaid_light_name).exists())
        self.assertTrue((self._media_dir / mermaid_dark_name).exists())

    def test_sync_keeps_mermaid_source_when_svg_render_fails(self) -> None:
        collection = _FakeCollection(media_dir=self._media_dir)
        mw = _FakeMw(collection)
        mermaid_source = "graph TD\nA --> B"
        encoded_mermaid = base64.urlsafe_b64encode(mermaid_source.encode("utf-8")).decode("ascii")
        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html=(
                '<figure class="notion-mermaid"><div class="notion-mermaid-source" '
                f'data-mermaid="{encoded_mermaid}"><pre class="code"><code class="language-mermaid">'
                "graph TD\nA --&gt; B"
                "</code></pre></div></figure>"
            ),
            content_hash="hash-mermaid-fallback",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ), patch.object(
            _SYNC_MODULE,
            "_render_mermaid_svg",
            return_value=None,
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(len(collection.notes), 1)
        saved_note = next(iter(collection.notes.values()))
        back_html = str(saved_note.get("Back", ""))
        self.assertIn('class="language-mermaid"', back_html)
        self.assertIn("graph TD", back_html)
        self.assertIn("A --&gt; B", back_html)

    def test_sync_marks_unchanged_when_hash_matches(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-1"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload(content_hash="hash-1")],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_unchanged, 1)
        self.assertEqual(result.stats.cards_updated, 0)
        self.assertEqual(result.stats.cards_created, 0)

    def test_sync_backfills_mermaid_media_even_when_hash_matches(self) -> None:
        collection = _FakeCollection(media_dir=self._media_dir)
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        existing_note["Front"] = "<p>front</p>"
        mermaid_source = "graph TD\nA --> B"
        encoded_mermaid = base64.urlsafe_b64encode(mermaid_source.encode("utf-8")).decode("ascii")
        existing_note["Back"] = (
            '<figure class="notion-mermaid"><div class="notion-mermaid-source" '
            f'data-mermaid="{encoded_mermaid}"><pre class="code"><code class="language-mermaid">'
            "graph TD\nA --&gt; B"
            "</code></pre></div></figure>"
        )
        existing_note["Notion Block ID"] = "block-1"
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-1"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload(content_hash="hash-1")],
        ), patch.object(
            _SYNC_MODULE,
            "_render_mermaid_svg",
            return_value=b"<svg></svg>",
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        saved_note = collection.get_note(existing_note.id)
        self.assertIsNotNone(saved_note)
        back_html = str(saved_note.get("Back", ""))
        mermaid_light_name = _SYNC_MODULE._mermaid_media_filename(mermaid_source, variant="light")
        mermaid_dark_name = _SYNC_MODULE._mermaid_media_filename(mermaid_source, variant="dark")
        self.assertIn(f'src="{mermaid_light_name}"', back_html)
        self.assertIn(f'src="{mermaid_dark_name}"', back_html)
        self.assertTrue((self._media_dir / mermaid_light_name).exists())
        self.assertTrue((self._media_dir / mermaid_dark_name).exists())

    def test_sync_upgrades_legacy_single_mermaid_svg_to_theme_aware_pair(self) -> None:
        collection = _FakeCollection(media_dir=self._media_dir)
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        existing_note["Front"] = "<p>front</p>"
        existing_note["Back"] = (
            '<figure class="notion-mermaid"><pre class="code notion-mermaid-diagram">'
            '<code class="language-mermaid"><img src="notion_mermaid_legacy.svg" alt="Mermaid diagram"/></code>'
            "</pre></figure>"
        )
        existing_note["Notion Block ID"] = "block-1"
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-1"),
            )
            connection.commit()
        finally:
            connection.close()

        mermaid_source = "graph TD\nA --> B"
        encoded_mermaid = base64.urlsafe_b64encode(mermaid_source.encode("utf-8")).decode("ascii")
        payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html=(
                '<figure class="notion-mermaid"><div class="notion-mermaid-source" '
                f'data-mermaid="{encoded_mermaid}"><pre class="code"><code class="language-mermaid">'
                "graph TD\nA --&gt; B"
                "</code></pre></div></figure>"
            ),
            content_hash="hash-1",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[payload],
        ), patch.object(
            _SYNC_MODULE,
            "_render_mermaid_svg",
            return_value=b"<svg></svg>",
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        saved_note = collection.get_note(existing_note.id)
        self.assertIsNotNone(saved_note)
        back_html = str(saved_note.get("Back", ""))
        mermaid_light_name = _SYNC_MODULE._mermaid_media_filename(mermaid_source, variant="light")
        mermaid_dark_name = _SYNC_MODULE._mermaid_media_filename(mermaid_source, variant="dark")
        self.assertIn('class="notion-mermaid-light"', back_html)
        self.assertIn('class="notion-mermaid-dark"', back_html)
        self.assertIn(f'src="{mermaid_light_name}"', back_html)
        self.assertIn(f'src="{mermaid_dark_name}"', back_html)

    def test_sync_recreates_mapping_when_note_is_missing(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", 123456, "basic", "old-hash"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload(content_hash="new-hash")],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_missing_note, 1)
        self.assertEqual(result.stats.cards_created, 1)
        self.assertEqual(result.stats.cards_warned, 1)
        self.assertEqual([warning.code for warning in result.warnings], ["missing_anki_note"])
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id FROM cards WHERE notion_block_id = 'block-1'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertNotEqual(int(row["anki_note_id"]), 123456)

    def test_sync_relinks_existing_note_when_mapping_is_missing(self) -> None:
        """A lost local mapping must not duplicate a note with the same block id."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion (Basic)"})
        existing_note["Front"] = "<p>old front</p>"
        existing_note["Back"] = "<p>old back</p>"
        existing_note["Notion Block ID"] = "block-1"
        collection.add_note(existing_note, deck_id=1)

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload(content_hash="new-hash")],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_created, 0)
        self.assertEqual(result.stats.cards_updated, 1)
        self.assertEqual(len(collection.notes), 1)
        self.assertEqual(existing_note["Front"], "<p>front</p>")
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["existing_anki_note_relinked"],
        )
        mapping = _SYNC_MODULE._load_existing_cards_for_page(
            self._db,
            "page-1",
        )["block-1"]
        self.assertEqual(int(mapping["anki_note_id"]), existing_note.id)

    def test_sync_relinks_existing_note_when_mapped_note_id_is_stale(self) -> None:
        """A profile restore may invalidate IDs while leaving the Noteck note."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        restored_note = collection.new_note({"name": "Notion (Basic)"})
        restored_note["Front"] = "<p>restored front</p>"
        restored_note["Back"] = "<p>restored back</p>"
        restored_note["Notion Block ID"] = "block-1"
        collection.add_note(restored_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", 123456, "basic", "old-hash"),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload(content_hash="new-hash")],
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_missing_note, 1)
        self.assertEqual(result.stats.cards_created, 0)
        self.assertEqual(result.stats.cards_updated, 1)
        self.assertEqual(len(collection.notes), 1)
        mapping = _SYNC_MODULE._load_existing_cards_for_page(
            self._db,
            "page-1",
        )["block-1"]
        self.assertEqual(int(mapping["anki_note_id"]), restored_note.id)

    def test_sync_preserves_cloze_mapping_when_cloze_parsing_is_disabled(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        self._db.set_setting("enable_cloze_parsing", "0")
        connection = self._db.connect()
        try:
            connection.execute(
                "UPDATE pages SET last_seen_notion_edit_time = ? WHERE notion_page_id = ?",
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type, content_hash
                )
                VALUES (?, ?, NULL, ?, ?)
                """,
                ("cloze-paused", "page-1", "cloze", "hash"),
            )
            connection.commit()
        finally:
            connection.close()

        client = Mock()
        client.get_page_last_edited_time.return_value = "2026-02-04T00:00:00.000Z"
        client.get_page_content.return_value = []
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.warnings, ())
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT notion_block_id FROM cards WHERE notion_block_id = ?",
                ("cloze-paused",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)

    def test_sync_fetch_failure_is_error_and_later_pages_continue(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO pages (notion_page_id, anki_deck_name, sync_enabled)
                VALUES (?, ?, 1)
                """,
                ("page-2", "Notion::Page 2"),
            )
            connection.commit()
        finally:
            connection.close()

        client = Mock()

        def page_edit_time(page_id: str) -> str:
            if page_id == "page-1":
                raise RuntimeError("Notion unavailable")
            return "2026-02-05T00:00:00.000Z"

        client.get_page_last_edited_time.side_effect = page_edit_time
        client.get_page_content.return_value = []
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertFalse(result.ok)
        self.assertFalse(result.cancelled)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Notion unavailable", result.errors[0])
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.stats.pages_scanned, 2)
        connection = self._db.connect()
        try:
            failed_page = connection.execute(
                "SELECT last_seen_notion_edit_time FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
            successful_page = connection.execute(
                "SELECT last_seen_notion_edit_time FROM pages WHERE notion_page_id = ?",
                ("page-2",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(failed_page["last_seen_notion_edit_time"])
        self.assertEqual(str(successful_page["last_seen_notion_edit_time"]), "2026-02-05T00:00:00.000Z")

    def test_sync_uses_queued_page_inputs_for_all_enabled_pages(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        connection = self._db.connect()
        try:
            connection.execute(
                """
                INSERT INTO pages (notion_page_id, anki_deck_name, sync_enabled)
                VALUES (?, ?, 1)
                """,
                ("page-2", "Notion::Page 2"),
            )
            connection.commit()
        finally:
            connection.close()

        client = _QueuedPageClient()
        progress_labels: list[str] = []
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            result = sync_notion_to_anki(
                mw=mw,
                db_path=self._db_path,
                progress_callback=progress_labels.append,
            )

        self.assertTrue(result.ok)
        self.assertEqual(client.requested_page_ids, ("page-1", "page-2"))
        self.assertEqual(result.stats.pages_scanned, 2)
        self.assertEqual(
            progress_labels,
            [
                "Fetching page data: 0/2 pages fetched.",
                "Fetching page data: 1/2 pages fetched.",
                "Fetching page data: 2/2 pages fetched.",
                "Parsing page data: 0/2 pages parsed.",
                "Parsing page data: 1/2 pages parsed.",
                "Parsing page data: 2/2 pages parsed.",
            ],
        )

    def test_sync_returns_cancelled_when_user_requests_abort(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ):
            result = sync_notion_to_anki(
                mw=mw,
                db_path=self._db_path,
                should_cancel=lambda: True,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Sync canceled.")
        self.assertEqual(result.stats.pages_scanned, 0)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.errors, ())

    def test_sync_matching_page_timestamp_still_parses_and_uses_content_hash(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        # Matching ancestor timestamps must not prevent content inspection.
        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id,
                    notion_page_id,
                    anki_note_id,
                    card_type,
                    content_hash,
                    last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-1", "2026-02-04T00:00:00.000Z"),
            )
            connection.commit()
        finally:
            connection.close()

        fake_client = _FakeNotionClient(page_last_edited_time="2026-02-04T00:00:00.000Z")
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=fake_client,
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[self._payload(content_hash="hash-1")],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        parse_mock.assert_called_once()
        self.assertEqual(result.stats.cards_unchanged, 1)
        self.assertEqual(result.stats.cards_updated, 0)

        # last_synced_at should remain NULL because nothing was written to Anki.
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT last_synced_at FROM pages WHERE notion_page_id = ?",
                ("page-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row["last_synced_at"])

    def test_sync_detects_toggle_and_descendant_edits_with_unchanged_timestamps(self) -> None:
        """Ancestor timestamps must not hide edits to a toggle title or body."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        client = _MutableToggleClient()

        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=client,
        ):
            first_result = sync_notion_to_anki(mw=mw, db_path=self._db_path)
            self.assertTrue(first_result.ok)
            self.assertEqual(first_result.stats.cards_created, 1)

            client.body = "Updated body"
            body_result = sync_notion_to_anki(mw=mw, db_path=self._db_path)
            self.assertTrue(body_result.ok)
            self.assertEqual(body_result.stats.cards_updated, 1)

            client.title = "Updated title"
            title_result = sync_notion_to_anki(mw=mw, db_path=self._db_path)
            self.assertTrue(title_result.ok)
            self.assertEqual(title_result.stats.cards_updated, 1)

        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        note = collection.get_note(int(row["anki_note_id"]))
        self.assertIsNotNone(note)
        self.assertIn("Updated title", note["Front"])
        self.assertIn("Updated body", note["Back"])

    def test_sync_unchanged_page_refreshes_toggle_once_after_parser_upgrade(self) -> None:
        """Unchanged Notion timestamps must not prevent the block-color field migration."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion (Basic)"})
        existing_note["Front"] = "<p>Legacy title</p>"
        existing_note["Back"] = "<p>Legacy back</p>"
        existing_note["Notion Card Background"] = ""
        collection.add_note(existing_note, deck_id=1)
        self._db.set_setting(_SYNC_MODULE._TOGGLE_REFRESH_REVISION_SETTING_KEY, "legacy")

        connection = self._db.connect()
        try:
            connection.execute(
                "UPDATE pages SET last_seen_notion_edit_time = ? WHERE notion_page_id = ?",
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type,
                    content_hash, last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "block-1", "page-1", existing_note.id, "basic",
                    "legacy-hash", "2026-02-04T00:00:00.000Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        refreshed_payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            fields={
                "Front": "<p>Colored title</p>",
                "Back": "<p>Colored back</p>",
                "Notion Block ID": "block-1",
                "Notion Card Background": "brown_background",
            },
            content_hash="block-color-hash",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )
        fake_client = _FakeNotionClient(page_last_edited_time="2026-02-04T00:00:00.000Z")
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=fake_client,
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[refreshed_payload],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        self.assertEqual(existing_note["Notion Card Background"], "brown_background")
        self.assertEqual(
            self._db.get_setting(_SYNC_MODULE._TOGGLE_REFRESH_REVISION_SETTING_KEY),
            _SYNC_MODULE._TOGGLE_REFRESH_REVISION,
        )
        self.assertEqual(
            parse_mock.call_args.kwargs.get("include_block_ids"),
            None,
        )

    def test_sync_changed_page_does_not_fast_skip_toggle_during_parser_refresh(self) -> None:
        """The one-time refresh also bypasses matching per-toggle edit timestamps."""
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion (Basic)"})
        collection.add_note(existing_note, deck_id=1)
        self._db.set_setting(_SYNC_MODULE._TOGGLE_REFRESH_REVISION_SETTING_KEY, "legacy")

        connection = self._db.connect()
        try:
            connection.execute(
                "UPDATE pages SET last_seen_notion_edit_time = ? WHERE notion_page_id = ?",
                ("2026-02-03T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id, notion_page_id, anki_note_id, card_type,
                    content_hash, last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "block-1", "page-1", existing_note.id, "basic",
                    "legacy-hash", "2026-02-04T00:00:00.000Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        refreshed_payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            fields={
                "Front": "<p>Colored title</p>",
                "Back": "<p>Colored back</p>",
                "Notion Block ID": "block-1",
                "Notion Card Background": "brown_background",
            },
            content_hash="block-color-hash",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=_FakeNotionClient(),
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[refreshed_payload],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        parse_mock.assert_called_once()

    def test_sync_unchanged_page_reprocesses_existing_cloze_cards_after_parser_upgrade(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion (Cloze)"})
        collection.add_note(existing_note, deck_id=1)
        self._db.set_setting("enable_cloze_parsing", "1")

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id,
                    notion_page_id,
                    anki_note_id,
                    card_type,
                    content_hash,
                    last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "cloze-included",
                    "page-1",
                    existing_note.id,
                    "cloze",
                    "legacy-hash",
                    "2026-02-04T00:00:00.000Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        cloze_payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="cloze-included",
            card_type="cloze",
            model_name="Notion (Cloze)",
            fields={
                "Text": "{{c1::Included}}",
                "Extra": "",
                "Notion Block ID": "cloze-included",
                "Notion Card Background": "brown_background",
            },
            content_hash="hash-updated",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        fake_client = _FakeNotionClientWithParagraphs(page_last_edited_time="2026-02-04T00:00:00.000Z")
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=fake_client,
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[cloze_payload],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        self.assertEqual(existing_note["Notion Card Background"], "brown_background")
        parse_mock.assert_called_once()
        include_block_ids = parse_mock.call_args.kwargs.get("include_block_ids")
        self.assertEqual(set(include_block_ids), {"cloze-included", "cloze-excluded"})
        self.assertEqual(
            self._db.get_setting(_SYNC_MODULE._CLOZE_REFRESH_REVISION_SETTING_KEY),
            _SYNC_MODULE._CLOZE_REFRESH_REVISION,
        )

        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT content_hash FROM cards WHERE notion_block_id = ?",
                ("cloze-included",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["content_hash"]), "hash-updated")

    def test_sync_unchanged_page_skips_repeat_cloze_refresh_after_revision_marker(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion (Cloze)"})
        collection.add_note(existing_note, deck_id=1)
        self._db.set_setting("enable_cloze_parsing", "1")
        self._db.set_setting(
            _SYNC_MODULE._CLOZE_REFRESH_REVISION_SETTING_KEY,
            _SYNC_MODULE._CLOZE_REFRESH_REVISION,
        )

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id,
                    notion_page_id,
                    anki_note_id,
                    card_type,
                    content_hash,
                    last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "cloze-included",
                    "page-1",
                    existing_note.id,
                    "cloze",
                    "hash-current",
                    "2026-02-04T00:00:00.000Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        current_payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="cloze-included",
            card_type="cloze",
            model_name="Notion (Cloze)",
            fields={
                "Text": "{{c1::Included}}",
                "Extra": "",
                "Notion Block ID": "cloze-included",
            },
            content_hash="hash-current",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )
        fake_client = _FakeNotionClientWithParagraphs(page_last_edited_time="2026-02-04T00:00:00.000Z")
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=fake_client,
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[current_payload],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        parse_mock.assert_called_once()
        self.assertEqual(result.stats.cards_unchanged, 1)
        self.assertEqual(result.stats.cards_updated, 0)
        self.assertEqual(result.stats.cards_created, 0)

    def test_sync_unchanged_page_reprocesses_toggle_when_default_card_type_changes(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET last_seen_notion_edit_time = ?, default_card_type = ?
                WHERE notion_page_id = ?
                """,
                ("2026-02-04T00:00:00.000Z", "input", "page-1"),
            )
            connection.execute(
                """
                INSERT INTO cards (
                    notion_block_id,
                    notion_page_id,
                    anki_note_id,
                    card_type,
                    content_hash,
                    last_seen_notion_edit_time
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("block-1", "page-1", existing_note.id, "basic", "hash-old", "2026-02-04T00:00:00.000Z"),
            )
            connection.commit()
        finally:
            connection.close()

        input_payload = ToggleCardPayload(
            notion_page_id="page-1",
            notion_block_id="block-1",
            front_html="<p>front</p>",
            back_html="<p>back</p>",
            card_type="input",
            model_name="Notion Toggle (Input)",
            fields={
                "Front": "<p>front</p>",
                "Back": "<p>back</p>",
                "Expected Answer": "back",
                "Notion Block ID": "block-1",
            },
            content_hash="hash-new",
            last_edited_time="2026-02-04T00:00:00.000Z",
        )

        fake_client = _FakeNotionClient(page_last_edited_time="2026-02-04T00:00:00.000Z")
        with patch.object(_SYNC_MODULE, "ensure_notion_toggle_model"), patch.object(
            _SYNC_MODULE.NotionClient,
            "from_settings",
            return_value=fake_client,
        ), patch.object(
            _SYNC_MODULE,
            "parse_page_to_cards",
            return_value=[input_payload],
        ) as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.stats.cards_updated, 1)
        parse_mock.assert_called_once()

        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id, card_type FROM cards WHERE notion_block_id = ?",
                ("block-1",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["card_type"]), "input")
        self.assertNotEqual(int(row["anki_note_id"]), int(existing_note.id))

    def test_run_notion_sync_with_progress_uses_progress_dialog_and_callback(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMwWithTaskman(collection)
        captured_result: list[SyncResult] = []
        observed_result: list[SyncResult] = []
        observer = observed_result.append
        mock_sync = Mock(return_value=SyncResult(ok=True, message="done", stats=SyncStats()))

        _SYNC_MODULE.register_sync_done_callback(observer)
        try:
            with patch.object(_SYNC_MODULE, "sync_notion_to_anki", mock_sync):
                started = run_notion_sync_with_progress(
                    mw=mw,
                    db_path=self._db_path,
                    on_done=lambda result: captured_result.append(result),
                    parent=object(),
                )
        finally:
            _SYNC_MODULE.unregister_sync_done_callback(observer)

        self.assertTrue(started)
        self.assertEqual(len(mw.progress.start_calls), 1)
        self.assertEqual(mw.progress.finish_calls, 1)
        self.assertEqual(len(captured_result), 1)
        self.assertEqual(captured_result[0].message, "done")
        self.assertEqual(observed_result, captured_result)
        self.assertIn("progress_callback", mock_sync.call_args.kwargs)
        self.assertIn("should_cancel", mock_sync.call_args.kwargs)

    def test_run_notion_sync_with_progress_requires_progress_api(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        mw.taskman = _FakeTaskManager()

        started = run_notion_sync_with_progress(
            mw=mw,
            db_path=self._db_path,
        )

        self.assertFalse(started)

    def test_trigger_sync_with_anki_button_resets_run_lock_after_completion(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMwWithTaskman(collection)

        def call_done_and_return(*args: object, **kwargs: object) -> bool:
            done_callback = kwargs.get("on_done")
            if callable(done_callback):
                done_callback(SyncResult(ok=True, message="done", stats=SyncStats()))
            return True

        with patch.object(_SYNC_MODULE.SettingsStore, "get_value", return_value=True), patch.object(
            _SYNC_MODULE,
            "run_notion_sync_with_progress",
            side_effect=call_done_and_return,
        ) as runner:
            trigger_sync_with_anki_button(mw=mw, db_path=self._db_path)
            trigger_sync_with_anki_button(mw=mw, db_path=self._db_path)

        self.assertEqual(runner.call_count, 2)


if __name__ == "__main__":
    unittest.main()
