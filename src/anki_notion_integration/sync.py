"""Sync orchestration for Notion → Anki flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable

from .cards import MODEL_NAME, ensure_notion_toggle_model
from .db import Database
from .notion_client import NotionClient
from .parser import ToggleCardPayload, parse_page_to_cards
from .settings import SettingsStore, create_default_settings


@dataclass(frozen=True)
class SyncStats:
    """Execution counters for one sync run."""

    pages_scanned: int = 0
    cards_seen: int = 0
    cards_created: int = 0
    cards_updated: int = 0
    cards_unchanged: int = 0
    cards_skipped: int = 0
    cards_missing_note: int = 0


@dataclass(frozen=True)
class SyncResult:
    """Result object returned by sync entrypoints."""

    ok: bool
    message: str
    stats: SyncStats = field(default_factory=SyncStats)
    errors: tuple[str, ...] = ()


class SyncError(RuntimeError):
    """Raised when sync cannot proceed due to configuration or runtime errors."""


SyncDoneCallback = Callable[[SyncResult], None]
SyncProgressCallback = Callable[[str, int | None, int | None], None]
SyncCancelCheck = Callable[[], bool]
_sync_is_running = False


def sync_notion_to_anki(
    mw: Any,
    db_path: str | Path,
    progress_callback: SyncProgressCallback | None = None,
    should_cancel: SyncCancelCheck | None = None,
) -> SyncResult:
    """Run a blocking Notion → Anki sync."""
    db = Database(db_path)
    _ensure_db_ready(db)
    ensure_notion_toggle_model(mw)

    enabled_pages = _load_enabled_pages(db)
    if not enabled_pages:
        return SyncResult(ok=True, message="No enabled pages to sync.", stats=SyncStats())

    try:
        client = NotionClient.from_settings(db, profile_name=_resolve_profile_name(mw))
    except Exception as exc:
        return SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))

    stats = SyncStats()
    errors: list[str] = []
    collection = _collection_from_mw(mw)
    if collection is None:
        message = "Anki collection is not available."
        return SyncResult(ok=False, message=f"Sync failed: {message}", errors=(message,))

    model = _model_by_name(collection, MODEL_NAME)
    if model is None:
        message = f"Anki note type '{MODEL_NAME}' is not available."
        return SyncResult(ok=False, message=f"Sync failed: {message}", errors=(message,))

    _publish_progress(
        callback=progress_callback,
        label="Preparing Notion sync...",
        value=0,
        maximum=len(enabled_pages),
    )

    # Iterate over enabled Notion pages and sync their content
    for page_id, deck_name in enabled_pages:
        if _is_sync_cancelled(should_cancel):
            return _build_cancelled_result(stats)

        _publish_progress(
            callback=progress_callback,
            label=f"Syncing page {stats.pages_scanned + 1}/{len(enabled_pages)}...",
            value=stats.pages_scanned,
            maximum=len(enabled_pages),
        )
        stats = _replace_stats(stats, pages_scanned=stats.pages_scanned + 1)
        try:
            deck_id = _ensure_deck_id(collection, deck_name)
            blocks = client.get_page_content(page_id)
            payloads = parse_page_to_cards(page_id, blocks)
            stats, page_errors, cancelled = _sync_page_payloads(
                db=db,
                collection=collection,
                model=model,
                page_id=page_id,
                deck_id=deck_id,
                payloads=payloads,
                stats=stats,
                should_cancel=should_cancel,
            )
            if cancelled:
                return _build_cancelled_result(stats)
            errors.extend(page_errors)
            _mark_page_synced(db, page_id)
            _publish_progress(
                callback=progress_callback,
                label=f"Synced page {stats.pages_scanned}/{len(enabled_pages)}.",
                value=stats.pages_scanned,
                maximum=len(enabled_pages),
            )
        except Exception as exc:
            errors.append(f"Page {page_id}: {exc}")

    _reset_mw_if_available(mw)
    if errors:
        return SyncResult(
            ok=False,
            message=f"Sync completed with {len(errors)} error(s).",
            stats=stats,
            errors=tuple(errors),
        )

    return SyncResult(
        ok=True,
        message="Sync completed.",
        stats=stats,
    )


def run_notion_sync_in_background(
    mw: Any,
    db_path: str | Path,
    on_done: SyncDoneCallback | None = None,
) -> bool:
    """Run Notion → Anki sync on Anki's background task manager."""
    taskman = getattr(mw, "taskman", None)
    if taskman is None or not hasattr(taskman, "run_in_background"):
        return False

    def work() -> SyncResult:
        return sync_notion_to_anki(mw=mw, db_path=db_path)

    def done(future: Any) -> None:
        try:
            result = future.result()
        except Exception as exc:
            result = SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))
        if on_done is not None:
            on_done(result)

    taskman.run_in_background(work, done)
    return True


def run_notion_sync_with_progress(
    mw: Any,
    db_path: str | Path,
    on_done: SyncDoneCallback | None = None,
    parent: Any | None = None,
) -> bool:
    """Run Notion sync with Anki's native progress dialog and cancel support."""
    taskman = getattr(mw, "taskman", None)
    progress = getattr(mw, "progress", None)
    run_in_background = getattr(taskman, "run_in_background", None)
    run_on_main = getattr(taskman, "run_on_main", None)
    start = getattr(progress, "start", None)
    update = getattr(progress, "update", None)
    finish = getattr(progress, "finish", None)

    # If progress APIs are unavailable, fall back to the existing background run.
    if not callable(run_in_background):
        return False
    if not callable(start) or not callable(update) or not callable(finish):
        return run_notion_sync_in_background(mw=mw, db_path=db_path, on_done=on_done)

    start(parent=parent, label="Syncing Notion changes...", immediate=True, title="Sync Notion pages")
    set_title = getattr(progress, "set_title", None)
    if callable(set_title):
        set_title("Sync Notion pages")

    def emit_progress(label: str, value: int | None, maximum: int | None) -> None:
        def apply_progress_update() -> None:
            update(label=label, value=value, max=maximum)

        # Ensure UI updates are always scheduled on the Qt main thread.
        if threading.current_thread() is threading.main_thread():
            apply_progress_update()
            return
        if callable(run_on_main):
            run_on_main(apply_progress_update)

    def should_cancel() -> bool:
        want_cancel = getattr(progress, "want_cancel", None)
        if not callable(want_cancel):
            return False
        try:
            return bool(want_cancel())
        except Exception:
            return False

    def work() -> SyncResult:
        return sync_notion_to_anki(
            mw=mw,
            db_path=db_path,
            progress_callback=emit_progress,
            should_cancel=should_cancel,
        )

    def done(future: Any) -> None:
        try:
            result = future.result()
        except Exception as exc:
            result = SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))
        finally:
            finish()
        if on_done is not None:
            on_done(result)

    run_in_background(work, done)
    return True


def trigger_startup_sync(
    mw: Any,
    db_path: str | Path,
) -> None:
    """Start sync on profile open when enabled in settings."""
    global _sync_is_running
    if _sync_is_running:
        return

    db = Database(db_path)
    _ensure_db_ready(db)
    store = SettingsStore(db, profile_name=_resolve_profile_name(mw))
    if not bool(store.get_value("notion_to_anki_auto_sync")):
        return

    def on_done(_result: SyncResult) -> None:
        global _sync_is_running
        _sync_is_running = False

    # Reuse the native progress flow at startup so users can see and cancel sync.
    _sync_is_running = run_notion_sync_with_progress(
        mw=mw,
        db_path=db_path,
        on_done=on_done,
        parent=mw,
    )


def trigger_sync_with_anki_button(
    mw: Any,
    db_path: str | Path,
) -> None:
    """Run Notion sync before Anki sync when enabled."""
    global _sync_is_running
    if _sync_is_running:
        return

    db = Database(db_path)
    _ensure_db_ready(db)
    store = SettingsStore(db, profile_name=_resolve_profile_name(mw))
    if not bool(store.get_value("sync_with_anki_sync_button")):
        return

    _sync_is_running = True

    def on_done(_result: SyncResult) -> None:
        global _sync_is_running
        _sync_is_running = False

    started = run_notion_sync_with_progress(
        mw=mw,
        db_path=db_path,
        on_done=on_done,
        parent=mw,
    )
    if started:
        return

    # Fall back to the original blocking path when no background/progress API is available.
    try:
        sync_notion_to_anki(mw=mw, db_path=db_path)
    finally:
        _sync_is_running = False


def _load_enabled_pages(db: Database) -> list[tuple[str, str]]:
    """Load enabled pages and their deck names from the pages table."""
    connection = db.connect()
    try:
        rows = connection.execute(
            """
            SELECT notion_page_id, anki_deck_name
            FROM pages
            WHERE sync_enabled = 1
            ORDER BY notion_page_id
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        (str(row["notion_page_id"]), str(row["anki_deck_name"]))
        for row in rows
    ]


def _ensure_db_ready(db: Database) -> None:
    """Initialize schema and default DB-backed settings if needed."""
    db.initialize()
    create_default_settings(db)


def _resolve_profile_name(mw: Any) -> str | None:
    """Resolve active profile name for namespaced keyring settings."""
    pm = getattr(mw, "pm", None)
    if pm is None:
        return None

    name_attr = getattr(pm, "name", None)
    if callable(name_attr):
        try:
            return str(name_attr())
        except Exception:
            return None
    if isinstance(name_attr, str):
        return name_attr
    return None


def _sync_page_payloads(
    db: Database,
    collection: Any,
    model: Any,
    page_id: str,
    deck_id: int,
    payloads: list[ToggleCardPayload],
    stats: SyncStats,
    should_cancel: SyncCancelCheck | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Sync one page worth of parsed toggle payloads."""
    existing_cards = _load_existing_cards_for_page(db, page_id)
    errors: list[str] = []

    for payload in payloads:
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True

        stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)
        mapping = existing_cards.get(payload.notion_block_id)
        if mapping is not None and mapping["excluded"]:
            stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
            continue

        try:
            if mapping is None:
                note_id = _create_note(collection, model, deck_id, payload)
                _upsert_card_mapping(db, payload, note_id, page_id)
                stats = _replace_stats(stats, cards_created=stats.cards_created + 1)
                continue

            note_id = mapping["anki_note_id"]
            if note_id is None:
                note_id = _create_note(collection, model, deck_id, payload)
                _upsert_card_mapping(db, payload, note_id, page_id)
                stats = _replace_stats(stats, cards_created=stats.cards_created + 1)
                continue

            note = _get_note(collection, note_id)
            if note is None:
                stats = _replace_stats(stats, cards_missing_note=stats.cards_missing_note + 1)
                note_id = _create_note(collection, model, deck_id, payload)
                _upsert_card_mapping(db, payload, note_id, page_id)
                stats = _replace_stats(stats, cards_created=stats.cards_created + 1)
                continue

            _ensure_note_cards_in_deck(collection, note_id, deck_id)
            if mapping["content_hash"] == payload.content_hash:
                stats = _replace_stats(stats, cards_unchanged=stats.cards_unchanged + 1)
                continue

            _apply_payload_to_note(note, payload)
            _update_note(collection, note)
            _upsert_card_mapping(db, payload, note_id, page_id)
            stats = _replace_stats(stats, cards_updated=stats.cards_updated + 1)
        except Exception as exc:
            errors.append(f"Block {payload.notion_block_id}: {exc}")

    return stats, errors, False


def _collection_from_mw(mw: Any) -> Any | None:
    """Return the collection object from Anki main window."""
    return getattr(mw, "col", None)


def _model_by_name(collection: Any, model_name: str) -> Any | None:
    """Resolve a model by name across supported Anki APIs."""
    models = getattr(collection, "models", None)
    if models is None:
        return None

    if hasattr(models, "by_name"):
        return models.by_name(model_name)
    if hasattr(models, "byName"):
        return models.byName(model_name)
    return None


def _ensure_deck_id(collection: Any, deck_name: str) -> int:
    """Return a deck id for a name, creating the deck if needed."""
    decks = getattr(collection, "decks", None)
    if decks is None:
        raise SyncError("Collection deck manager is unavailable.")

    raw_deck_id: Any = None
    if hasattr(decks, "id_for_name"):
        raw_deck_id = decks.id_for_name(deck_name)
    elif hasattr(decks, "id"):
        raw_deck_id = decks.id(deck_name)
    elif hasattr(decks, "idForName"):
        raw_deck_id = decks.idForName(deck_name)
    else:
        raise SyncError("Cannot resolve deck id for this Anki version.")

    deck_id = _coerce_deck_id(raw_deck_id)
    if deck_id is not None:
        return deck_id

    # Some APIs may require an explicit create call if the deck was deleted.
    create_fn = getattr(decks, "add_normal_deck_with_name", None)
    if callable(create_fn):
        raw_created = create_fn(deck_name)
        created_id = _coerce_deck_id(raw_created)
        if created_id is not None:
            return created_id

    if hasattr(decks, "id_for_name"):
        raw_deck_id = decks.id_for_name(deck_name)
    elif hasattr(decks, "id"):
        raw_deck_id = decks.id(deck_name)
    elif hasattr(decks, "idForName"):
        raw_deck_id = decks.idForName(deck_name)

    deck_id = _coerce_deck_id(raw_deck_id)
    if deck_id is not None:
        return deck_id

    raise SyncError(f"Cannot resolve or create deck id for '{deck_name}'.")


def _create_note(collection: Any, model: Any, deck_id: int, payload: ToggleCardPayload) -> int:
    """Create a note for one parsed payload and return the new note id."""
    note = _new_note(collection, model)
    _apply_payload_to_note(note, payload)
    _add_note(collection, note, deck_id)
    note_id = _note_id(note)
    if note_id is None:
        raise SyncError("Created note has no id.")
    _ensure_note_cards_in_deck(collection, note_id, deck_id)
    return int(note_id)


def _new_note(collection: Any, model: Any) -> Any:
    """Create a new note instance for a model."""
    if hasattr(collection, "new_note"):
        return collection.new_note(model)
    if hasattr(collection, "newNote"):
        return collection.newNote(model)
    raise SyncError("Cannot create notes for this Anki version.")


def _add_note(collection: Any, note: Any, deck_id: int) -> None:
    """Persist a newly created note in a target deck."""
    if hasattr(collection, "add_note"):
        collection.add_note(note, deck_id)
        return
    if hasattr(collection, "addNote"):
        collection.addNote(note, deck_id)
        return
    raise SyncError("Cannot add notes for this Anki version.")


def _apply_payload_to_note(note: Any, payload: ToggleCardPayload) -> None:
    """Apply parsed payload fields to an Anki note."""
    note["Front"] = payload.front_html
    note["Back"] = payload.back_html
    note["Notion Block ID"] = payload.notion_block_id


def _note_id(note: Any) -> int | None:
    """Extract note id from different note implementations."""
    note_id = getattr(note, "id", None)
    if note_id:
        return int(note_id)
    if isinstance(note, dict):
        raw_id = note.get("id")
        if raw_id:
            return int(raw_id)
    return None


def _get_note(collection: Any, note_id: int) -> Any | None:
    """Load a note by id across supported Anki APIs."""
    try:
        if hasattr(collection, "get_note"):
            return collection.get_note(note_id)
        if hasattr(collection, "getNote"):
            return collection.getNote(note_id)
    except Exception as exc:
        if _is_missing_note_error(exc):
            return None
        raise
    return None


def _coerce_deck_id(deck_id: Any) -> int | None:
    """Return a positive integer deck id when conversion is possible."""
    try:
        value = int(deck_id)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_missing_note_error(exc: Exception) -> bool:
    """Return whether an exception indicates a stale/missing note id."""
    message = str(exc).lower()
    return "no such note" in message


def _ensure_note_cards_in_deck(collection: Any, note_id: int, deck_id: int) -> None:
    """Ensure all cards for a note are assigned to the expected deck."""
    card_ids = _card_ids_for_note(collection, note_id)
    if card_ids:
        if hasattr(collection, "set_deck"):
            collection.set_deck(card_ids, deck_id)
            return
        if hasattr(collection, "setDeck"):
            collection.setDeck(card_ids, deck_id)
            return

    db = getattr(collection, "db", None)
    if db is None or not hasattr(db, "execute"):
        return

    try:
        db.execute("UPDATE cards SET did = ? WHERE nid = ?", deck_id, note_id)
    except TypeError:
        db.execute("UPDATE cards SET did = ? WHERE nid = ?", (deck_id, note_id))


def _card_ids_for_note(collection: Any, note_id: int) -> list[int]:
    """Return card ids for a note across supported collection APIs."""
    if hasattr(collection, "card_ids_of_note"):
        return [int(card_id) for card_id in collection.card_ids_of_note(note_id)]
    if hasattr(collection, "cardIdsOfNote"):
        return [int(card_id) for card_id in collection.cardIdsOfNote(note_id)]

    db = getattr(collection, "db", None)
    if db is None:
        return []

    all_rows = getattr(db, "all", None)
    if callable(all_rows):
        try:
            rows = all_rows("SELECT id FROM cards WHERE nid = ?", note_id)
        except TypeError:
            rows = all_rows("SELECT id FROM cards WHERE nid = ?", (note_id,))
        return [int(row[0]) for row in rows]
    return []


def _update_note(collection: Any, note: Any) -> None:
    """Persist note field updates."""
    if hasattr(collection, "update_note"):
        collection.update_note(note)
        return
    if hasattr(collection, "updateNote"):
        collection.updateNote(note)
        return

    flush = getattr(note, "flush", None)
    if callable(flush):
        flush()
        return

    raise SyncError("Cannot update notes for this Anki version.")


def _load_existing_cards_for_page(db: Database, page_id: str) -> dict[str, dict[str, Any]]:
    """Load card mappings for one page keyed by Notion block id."""
    connection = db.connect()
    try:
        rows = connection.execute(
            """
            SELECT notion_block_id, anki_note_id, content_hash, excluded
            FROM cards
            WHERE notion_page_id = ?
            """,
            (page_id,),
        ).fetchall()
    finally:
        connection.close()

    return {
        str(row["notion_block_id"]): {
            "anki_note_id": int(row["anki_note_id"]) if row["anki_note_id"] is not None else None,
            "content_hash": str(row["content_hash"]),
            "excluded": bool(row["excluded"]),
        }
        for row in rows
    }


def _upsert_card_mapping(
    db: Database,
    payload: ToggleCardPayload,
    note_id: int,
    page_id: str,
) -> None:
    """Insert or update a cards-table mapping entry."""
    connection = db.connect()
    try:
        connection.execute(
            """
            INSERT INTO cards (
                notion_block_id,
                notion_page_id,
                anki_note_id,
                card_type,
                content_hash,
                last_seen_notion_edit_time,
                last_synced_at,
                excluded
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 0)
            ON CONFLICT(notion_block_id) DO UPDATE SET
                notion_page_id = excluded.notion_page_id,
                anki_note_id = excluded.anki_note_id,
                card_type = excluded.card_type,
                content_hash = excluded.content_hash,
                last_seen_notion_edit_time = excluded.last_seen_notion_edit_time,
                last_synced_at = datetime('now')
            """,
            (
                payload.notion_block_id,
                page_id,
                note_id,
                "basic",
                payload.content_hash,
                payload.last_edited_time,
            ),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _mark_page_synced(db: Database, page_id: str) -> None:
    """Update last synced timestamp for one page."""
    connection = db.connect()
    try:
        connection.execute(
            """
            UPDATE pages
            SET last_synced_at = datetime('now')
            WHERE notion_page_id = ?
            """,
            (page_id,),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _reset_mw_if_available(mw: Any) -> None:
    """Refresh Anki UI when a sync run changed note data."""
    reset_fn = getattr(mw, "reset", None)
    if not callable(reset_fn):
        return

    # `mw.reset()` must run on Anki's main/UI thread.
    if threading.current_thread() is threading.main_thread():
        reset_fn()
        return

    taskman = getattr(mw, "taskman", None)
    run_on_main = getattr(taskman, "run_on_main", None)
    if callable(run_on_main):
        run_on_main(reset_fn)


def _replace_stats(stats: SyncStats, **changes: int) -> SyncStats:
    """Return updated immutable sync stats."""
    payload = {
        "pages_scanned": stats.pages_scanned,
        "cards_seen": stats.cards_seen,
        "cards_created": stats.cards_created,
        "cards_updated": stats.cards_updated,
        "cards_unchanged": stats.cards_unchanged,
        "cards_skipped": stats.cards_skipped,
        "cards_missing_note": stats.cards_missing_note,
    }
    payload.update(changes)
    return SyncStats(**payload)


def _publish_progress(
    callback: SyncProgressCallback | None,
    label: str,
    value: int | None,
    maximum: int | None,
) -> None:
    """Emit sync progress updates when a callback is configured."""
    if callback is None:
        return
    callback(label, value, maximum)


def _is_sync_cancelled(should_cancel: SyncCancelCheck | None) -> bool:
    """Evaluate whether the active sync run should abort."""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _build_cancelled_result(stats: SyncStats) -> SyncResult:
    """Build a consistent sync result for user-triggered cancellation."""
    return SyncResult(
        ok=False,
        message="Sync canceled.",
        stats=stats,
        errors=("Canceled by user.",),
    )
