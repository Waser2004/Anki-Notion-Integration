"""Tests for Notion → Anki sync orchestration."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from anki_notion_integration.db import Database
from anki_notion_integration.notion_client import NotionBlock
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

    def test_sync_skips_unchanged_page_without_fetching_blocks_or_parsing(self) -> None:
        collection = _FakeCollection()
        mw = _FakeMw(collection)
        existing_note = collection.new_note({"name": "Notion Toggle"})
        collection.add_note(existing_note, deck_id=1)

        # Record that we've already seen this page edit time so the fast path treats it as unchanged.
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
        ), patch.object(_SYNC_MODULE, "parse_page_to_cards") as parse_mock:
            result = sync_notion_to_anki(mw=mw, db_path=self._db_path)

        self.assertTrue(result.ok)
        parse_mock.assert_not_called()

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
