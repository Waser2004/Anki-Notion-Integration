"""Tests for Notion → Anki sync orchestration."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.db import Database
from anki_notion_integration.parser import ToggleCardPayload
from anki_notion_integration.sync import (
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
        self._model = {"name": "Notion Toggle"}

    def by_name(self, name: str) -> dict[str, str] | None:
        if name == "Notion Toggle":
            return self._model
        return None


class _FakeDecks:
    """Minimal deck API surface used by sync logic."""

    def __init__(self) -> None:
        self._ids: dict[str, int] = {}
        self._next_id = 1

    def id_for_name(self, name: str) -> int:
        if name not in self._ids:
            self._ids[name] = self._next_id
            self._next_id += 1
        return self._ids[name]


class _FakeNote(dict):
    """Small note object supporting field assignment and id tracking."""

    def __init__(self) -> None:
        super().__init__()
        self.id: int | None = None


class _FakeCollection:
    """Collection double with the methods used by sync.py."""

    def __init__(self) -> None:
        self.models = _FakeModels()
        self.decks = _FakeDecks()
        self._next_note_id = 1000
        self.notes: dict[int, _FakeNote] = {}

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

    def update_note(self, note: _FakeNote) -> None:
        if note.id is None:
            raise RuntimeError("note id missing")
        self.notes[note.id] = note


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

    def get_page_content(self, page_id: str) -> list[object]:
        _ = page_id
        return []


class SyncTests(unittest.TestCase):
    """Validate create/update/no-op sync behavior with mocked dependencies."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._db_path = Path(self._temp_dir.name) / "sync.db"
        self._db = Database(self._db_path)
        self._db.initialize()
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
        connection = self._db.connect()
        try:
            row = connection.execute(
                "SELECT anki_note_id FROM cards WHERE notion_block_id = 'block-1'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        self.assertNotEqual(int(row["anki_note_id"]), 123456)

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
        self.assertIn("Canceled by user.", result.errors)

    def test_run_notion_sync_with_progress_uses_progress_dialog_and_callback(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMwWithTaskman(collection)
        captured_result: list[SyncResult] = []
        mock_sync = Mock(return_value=SyncResult(ok=True, message="done", stats=SyncStats()))

        with patch.object(_SYNC_MODULE, "sync_notion_to_anki", mock_sync):
            started = run_notion_sync_with_progress(
                mw=mw,
                db_path=self._db_path,
                on_done=lambda result: captured_result.append(result),
                parent=object(),
            )

        self.assertTrue(started)
        self.assertEqual(len(mw.progress.start_calls), 1)
        self.assertEqual(mw.progress.finish_calls, 1)
        self.assertEqual(len(captured_result), 1)
        self.assertEqual(captured_result[0].message, "done")
        self.assertIn("progress_callback", mock_sync.call_args.kwargs)
        self.assertIn("should_cancel", mock_sync.call_args.kwargs)

    def test_run_notion_sync_with_progress_falls_back_when_progress_api_missing(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        mw.taskman = _FakeTaskManager()

        with patch.object(_SYNC_MODULE, "run_notion_sync_in_background", return_value=True) as fallback:
            started = run_notion_sync_with_progress(
                mw=mw,
                db_path=self._db_path,
            )

        self.assertTrue(started)
        fallback.assert_called_once()

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
