"""Sync orchestration for Notion → Anki flows."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import html
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable
from urllib import request
from urllib.parse import urlsplit

from .card_types import BASIC, CLOZE, DEFAULT_SELECTABLE_CARD_TYPES, normalize_card_type, normalize_default_selectable_card_type
from .parser.cloze_card_parser import CLOZE_MARKER_COLORS, ClozeCardParser
from .card_type_overrides import CardTypeOverrideStore
from .cards import MODEL_NAME_BASIC, ensure_notion_toggle_model
from .db import Database
from .logging_utils import configure_file_logging, log_file_path
from .markdown_snapshot import extract_root_toggle_markdown, hash_notion_markdown
from .notion_client import (
    NotionBlock,
    NotionClient,
    NotionMarkdownSnapshot,
    NotionPageFetchResult,
    NotionPageSyncData,
    merge_markdown_table_colors,
)
from .parser import CardParseResult, CardParseWarning, ToggleCardPayload, parse_page_to_cards
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
    cards_detached: int = 0
    cards_warned: int = 0


@dataclass(frozen=True)
class SyncWarning:
    """Describe one recoverable condition encountered during sync."""

    code: str
    message: str
    page_id: str | None = None
    block_id: str | None = None


@dataclass(frozen=True)
class SyncResult:
    """Result object returned by sync entrypoints."""

    ok: bool
    message: str
    stats: SyncStats = field(default_factory=SyncStats)
    errors: tuple[str, ...] = ()
    warnings: tuple[SyncWarning, ...] = ()
    cancelled: bool = False


class SyncError(RuntimeError):
    """Raised when sync cannot proceed due to configuration or runtime errors."""


@dataclass(frozen=True)
class EnabledPage:
    """Represents one sync-enabled Notion page and its stored deck reference."""

    notion_page_id: str
    anki_deck_name: str
    anki_deck_id: int | None
    default_card_type: str | None


SyncDoneCallback = Callable[[SyncResult], None]
SyncProgressCallback = Callable[[str], None]
SyncCancelCheck = Callable[[], bool]
_sync_is_running = False
_sync_done_callbacks: list[SyncDoneCallback] = []
_LOG = logging.getLogger("noteck.sync")
_IMAGE_TAG_RE = re.compile(r'<img(?P<before>[^>]*?)\ssrc="(?P<src>[^"]+)"(?P<after>[^>]*)>', re.IGNORECASE)
# Match Mermaid placeholders even if attributes or whitespace shift slightly during HTML processing.
_MERMAID_FIGURE_RE = re.compile(
    r'<figure(?=[^>]*\bnotion-mermaid\b)[^>]*>\s*'
    r'<div(?=[^>]*\bnotion-mermaid-source\b)[^>]*\bdata-mermaid="(?P<encoded>[^"]+)"[^>]*>\s*'
    r'<pre[^>]*>\s*<code[^>]*>(?P<source>.*?)</code>\s*</pre>\s*'
    r'</div>\s*(?P<caption><figcaption>.*?</figcaption>)?\s*</figure>',
    re.DOTALL,
)
_HTTP_TIMEOUT_SECONDS = 20.0
_CLOZE_REFRESH_REVISION_SETTING_KEY = "_internal_cloze_refresh_revision"
_CLOZE_REFRESH_REVISION = "2026-07-cloze-block-colors-v10" # reset to v1 before release as this has been updated to v9 for testing only
_TOGGLE_REFRESH_REVISION_SETTING_KEY = "_internal_toggle_refresh_revision"
_TOGGLE_REFRESH_REVISION = "2026-07-block-colors-v1"
_GRAY_TOGGLE_CLOZE_ENABLED_SETTING_KEY = "_internal_gray_toggle_cloze_enabled"
_CLOZE_MARKER_COLORS_SETTING_KEY = "_internal_cloze_marker_colors"


def sync_notion_to_anki(
    mw: Any,
    db_path: str | Path,
    progress_callback: SyncProgressCallback | None = None,
    should_cancel: SyncCancelCheck | None = None,
) -> SyncResult:
    """Run a blocking Notion → Anki sync."""
    global _LOG
    try:
        _LOG = configure_file_logging(db_path).getChild("sync")
    except OSError:
        # A read-only or full profile directory must not make syncing impossible.
        _LOG = logging.getLogger("noteck.sync")
    _LOG.info("Sync started. database=%s log=%s", db_path, log_file_path(db_path))

    try:
        db = Database(db_path)
        _ensure_db_ready(db)
        ensure_notion_toggle_model(mw)
        enabled_pages = _load_enabled_pages(db)
    except Exception as exc:
        _LOG.exception("Sync aborted during local initialization.")
        return SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))
    if not enabled_pages:
        _LOG.info("Sync finished without work: no enabled pages.")
        return SyncResult(ok=True, message="No enabled pages to sync.", stats=SyncStats())

    profile_name = _resolve_profile_name(mw)
    try:
        client = NotionClient.from_settings(db, profile_name=profile_name)
    except Exception as exc:
        _LOG.exception("Sync aborted while creating the Notion client.")
        return SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))

    try:
        store = SettingsStore(db, profile_name=profile_name)
        card_type_override_store = CardTypeOverrideStore(db)
        global_default_card_type = normalize_default_selectable_card_type(store.get_value("default_card_type"))
        enable_cloze = bool(store.get_value("enable_cloze_parsing"))
        enable_gray_toggle_cloze = bool(store.get_value("enable_gray_toggle_cloze_parsing"))
        cloze_marker_colors = list(store.get_value("cloze_marker_colors"))
        cloze_refresh_revision_pending = (
            enable_cloze
            and _load_cloze_refresh_revision(db) != _CLOZE_REFRESH_REVISION
        )
        cloze_settings_refresh_pending = enable_cloze and (
            _load_gray_toggle_cloze_enabled(db) != enable_gray_toggle_cloze
            or cloze_marker_colors_need_sync(db, cloze_marker_colors)
        )
        # Keep existing parser revision markers for upgrade bookkeeping. The full
        # content scan now makes timestamp-specific refresh branches unnecessary.
        toggle_refresh_revision_pending = _load_toggle_refresh_revision(db) != _TOGGLE_REFRESH_REVISION
    except Exception as exc:
        _LOG.exception("Sync aborted while loading local configuration.")
        return SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))

    stats = SyncStats()
    errors: list[str] = []
    warnings: list[SyncWarning] = []
    collection = _collection_from_mw(mw)
    if collection is None:
        message = "Anki collection is not available."
        _LOG.error("Sync aborted: %s", message)
        return SyncResult(ok=False, message=f"Sync failed: {message}", errors=(message,))

    _LOG.info(
        "Sync configured. pages=%d default_card_type=%s cloze_enabled=%s "
        "cloze_refresh_revision_pending=%s toggle_refresh_revision_pending=%s",
        len(enabled_pages), global_default_card_type, enable_cloze,
        cloze_refresh_revision_pending, toggle_refresh_revision_pending,
    )

    # Fetch latency-bound page inputs concurrently. Anki and SQLite mutations stay
    # on this sync thread because those APIs are not safe for worker-thread writes.
    if _is_sync_cancelled(should_cancel):
        _LOG.warning("Sync cancelled before Notion page preparation.")
        return _build_cancelled_result(stats)

    prefetched_pages: dict[str, NotionPageFetchResult] | None = None
    get_pages_sync_data = getattr(client, "get_pages_sync_data", None)
    supports_page_queue = callable(
        getattr(type(client), "get_pages_sync_data", None)
    )
    if supports_page_queue and callable(get_pages_sync_data):
        try:
            total_pages = len(enabled_pages)
            _publish_progress(
                callback=progress_callback,
                label=f"Fetching page data: 0/{total_pages} pages fetched.",
            )

            def publish_fetch_progress(completed: int, total: int) -> None:
                """Translate page-worker completion into an Anki progress label."""
                _publish_progress(
                    callback=progress_callback,
                    label=f"Fetching page data: {completed}/{total} pages fetched.",
                )

            prefetched_pages = get_pages_sync_data(
                (page.notion_page_id for page in enabled_pages),
                progress_callback=publish_fetch_progress,
            )
            _LOG.info("Prepared Notion page inputs. pages=%d", len(prefetched_pages))
            if _is_sync_cancelled(should_cancel):
                _LOG.warning("Sync cancelled after Notion page preparation.")
                return _build_cancelled_result(stats)
        except Exception as exc:
            _LOG.exception("Concurrent Notion page preparation failed.")
            return SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))

    # Reconcile prepared pages sequentially so collection and database writes remain safe.
    _publish_progress(
        callback=progress_callback,
        label=f"Parsing page data: 0/{len(enabled_pages)} pages parsed.",
    )
    for page in enabled_pages:
        if _is_sync_cancelled(should_cancel):
            _LOG.warning("Sync cancelled before page %s.", page.notion_page_id)
            return _build_cancelled_result(stats)

        stats = _replace_stats(stats, pages_scanned=stats.pages_scanned + 1)

        page_warnings: list[SyncWarning] = []
        page_id = page.notion_page_id
        try:
            page_sync_data: NotionPageSyncData | None = None
            if prefetched_pages is not None:
                fetch_result = prefetched_pages.get(page_id)
                if fetch_result is None:
                    raise RuntimeError("Notion page preparation returned no result.")
                if fetch_result.error is not None:
                    raise fetch_result.error
                if fetch_result.data is None:
                    raise RuntimeError("Notion page preparation returned no data.")
                page_sync_data = fetch_result.data

            page_default_card_type = _effective_default_card_type(
                page.default_card_type,
                global_default=global_default_card_type,
            )
            page_card_type_overrides = card_type_override_store.get_card_type_overrides_for_page(page_id)
            _LOG.info(
                "Processing page. page_id=%s stored_deck=%s stored_deck_id=%s default_card_type=%s overrides=%d",
                page_id, page.anki_deck_name, page.anki_deck_id, page_default_card_type,
                len(page_card_type_overrides),
            )
            deck_id, resolved_deck_name = _resolve_page_deck(
                collection=collection,
                stored_deck_name=page.anki_deck_name,
                stored_deck_id=page.anki_deck_id,
            )
            if page.anki_deck_id != deck_id or page.anki_deck_name != resolved_deck_name:
                _LOG.info("Updating page deck reference. page_id=%s deck_id=%s deck_name=%s", page_id, deck_id, resolved_deck_name)
                _set_page_deck_reference(
                    db=db,
                    page_id=page_id,
                    deck_id=deck_id,
                    deck_name=resolved_deck_name,
                )
            page_last_edited_time = (
                page_sync_data.last_edited_time
                if page_sync_data is not None
                else client.get_page_last_edited_time(page_id)
            )
            stored_page_edit_time = _load_page_last_seen_notion_edit_time(db, page_id)
            page_metadata_matches = (
                page_last_edited_time is not None
                and stored_page_edit_time is not None
                and page_last_edited_time == stored_page_edit_time
            )
            _LOG.info(
                "Page metadata edit check. page_id=%s notion_edit=%s stored_edit=%s matches=%s",
                page_id, page_last_edited_time, stored_page_edit_time, page_metadata_matches,
            )

            before_writes = stats.cards_created + stats.cards_updated
            stats, page_errors, cancelled = _sync_page_content(
                db=db,
                collection=collection,
                page_id=page_id,
                deck_id=deck_id,
                client=client,
                stats=stats,
                default_card_type=page_default_card_type,
                card_type_overrides=page_card_type_overrides,
                enable_cloze=enable_cloze,
                enable_gray_toggle_cloze=enable_gray_toggle_cloze,
                cloze_marker_colors=cloze_marker_colors,
                force_toggle_refresh=(
                    toggle_refresh_revision_pending
                    or cloze_refresh_revision_pending
                    or cloze_settings_refresh_pending
                ),
                force_cloze_refresh=(
                    cloze_refresh_revision_pending or cloze_settings_refresh_pending
                ),
                should_cancel=should_cancel,
                warnings=page_warnings,
                prefetched_data=page_sync_data,
            )

            if cancelled:
                _LOG.warning("Sync cancelled while processing page %s.", page_id)
                return _build_cancelled_result(stats)

            errors.extend(page_errors)
            warnings.extend(page_warnings)

            # Persist the last seen Notion edit time only when the page processed cleanly and
            # the value actually changed (avoid unnecessary DB writes on skipped pages).
            if (
                not page_errors
                and page_last_edited_time
                and page_last_edited_time != stored_page_edit_time
            ):
                _set_page_last_seen_notion_edit_time(db, page_id, page_last_edited_time)
                _LOG.debug("Stored page edit timestamp. page_id=%s value=%s", page_id, page_last_edited_time)

            # Only mark the page as synced when we actually wrote changes to Anki.
            after_writes = stats.cards_created + stats.cards_updated
            if after_writes > before_writes:
                _mark_page_synced(db, page_id)
            _LOG.info(
                "Page complete. page_id=%s writes=%d errors=%d warnings=%d stats=%s",
                page_id, after_writes - before_writes, len(page_errors), len(page_warnings), stats,
            )
            
        except Exception as exc:
            _LOG.exception("Page sync failed. page_id=%s", page_id)
            errors.append(f"Page {page_id}: {exc}")
            warnings.extend(page_warnings)
        finally:
            _publish_progress(
                callback=progress_callback,
                label=(
                    f"Parsing page data: {stats.pages_scanned}/"
                    f"{len(enabled_pages)} pages parsed."
                ),
            )

    try:
        _reset_mw_if_available(mw)
    except Exception as exc:
        _LOG.warning("Sync could not refresh the Anki UI: %s", exc)
        stats = _add_warning(
            stats,
            warnings,
            code="anki_ui_refresh_failed",
            message=f"Anki UI refresh failed after sync: {exc}",
            page_id=None,
            block_id=None,
        )
    
    if errors:
        _LOG.error("Sync completed with errors. count=%d warnings=%d stats=%s", len(errors), len(warnings), stats)
        return SyncResult(
            ok=False,
            message=f"Sync completed with {len(errors)} error(s).",
            stats=stats,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    try:
        if cloze_refresh_revision_pending:
            _set_cloze_refresh_revision(db, _CLOZE_REFRESH_REVISION)
            _LOG.info("Recorded completed cloze refresh revision %s.", _CLOZE_REFRESH_REVISION)
        if enable_cloze:
            _set_gray_toggle_cloze_enabled(db, enable_gray_toggle_cloze)
            _set_cloze_marker_colors(db, cloze_marker_colors)

        if toggle_refresh_revision_pending:
            _set_toggle_refresh_revision(db, _TOGGLE_REFRESH_REVISION)
            _LOG.info("Recorded completed toggle refresh revision %s.", _TOGGLE_REFRESH_REVISION)
    except Exception as exc:
        _LOG.exception("Sync could not record parser refresh completion.")
        return SyncResult(
            ok=False,
            message=f"Sync failed: {exc}",
            stats=stats,
            errors=(str(exc),),
            warnings=tuple(warnings),
        )

    if warnings:
        _LOG.warning("Sync completed with warnings. count=%d stats=%s", len(warnings), stats)
        return SyncResult(
            ok=True,
            message=f"Sync completed with {len(warnings)} warning(s).",
            stats=stats,
            warnings=tuple(warnings),
        )

    _LOG.info("Sync completed successfully. stats=%s", stats)
    return SyncResult(
        ok=True,
        message="Sync completed.",
        stats=stats,
    )


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
        return False

    start(parent=parent, label="Syncing Notion changes...", immediate=True, title="Sync Notion pages")
    set_title = getattr(progress, "set_title", None)
    if callable(set_title):
        set_title("Sync Notion pages")

    def emit_progress(label: str) -> None:
        def apply_progress_update() -> None:
            update(label=label)

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
        _notify_sync_done(result)
        if on_done is not None:
            on_done(result)

    run_in_background(work, done)
    return True


def register_sync_done_callback(callback: SyncDoneCallback) -> None:
    """Register a UI callback that runs after any progress-based Notion sync."""
    if callback not in _sync_done_callbacks:
        _sync_done_callbacks.append(callback)


def unregister_sync_done_callback(callback: SyncDoneCallback) -> None:
    """Stop notifying a previously registered Notion-sync callback."""
    if callback in _sync_done_callbacks:
        _sync_done_callbacks.remove(callback)


def _notify_sync_done(result: SyncResult) -> None:
    """Notify live UI observers without allowing one observer to break sync."""
    for callback in tuple(_sync_done_callbacks):
        try:
            callback(result)
        except Exception:
            _LOG.exception("A Notion sync completion callback failed.")


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
    if not started:
        _sync_is_running = False
        return


def _load_enabled_pages(db: Database) -> list[EnabledPage]:
    """Load enabled pages and their deck references from the pages table."""
    connection = db.connect()
    try:
        rows = connection.execute(
            """
            SELECT notion_page_id, anki_deck_name, anki_deck_id, default_card_type
            FROM pages
            WHERE sync_enabled = 1
            ORDER BY notion_page_id
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        EnabledPage(
            notion_page_id=str(row["notion_page_id"]),
            anki_deck_name=str(row["anki_deck_name"]),
            anki_deck_id=_coerce_deck_id(row["anki_deck_id"]),
            default_card_type=_as_optional_string(row["default_card_type"]),
        )
        for row in rows
    ]


def _effective_default_card_type(page_card_type: str | None, *, global_default: str) -> str:
    """Resolve effective default card type for one page."""
    if page_card_type is None:
        return normalize_default_selectable_card_type(global_default)
    return normalize_default_selectable_card_type(page_card_type)


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


def _sync_page_content(
    db:                   Database,
    collection:           Any,
    page_id:              str,
    deck_id:              int,
    client:               NotionClient,
    stats:                SyncStats,
    default_card_type:    str,
    card_type_overrides:  dict[str, str],
    enable_cloze:         bool,
    enable_gray_toggle_cloze: bool = True,
    cloze_marker_colors:  list[str] | None = None,
    force_toggle_refresh: bool                     = False,
    force_cloze_refresh:  bool                     = False,
    should_cancel:        SyncCancelCheck | None   = None,
    warnings:             list[SyncWarning] | None = None,
    prefetched_data:      NotionPageSyncData | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Use Markdown snapshots to recursively fetch only changed root toggles."""
    get_page_markdown = getattr(client, "get_page_markdown", None)
    if not callable(get_page_markdown):
        return _sync_page_content_full(
            db                  = db,
            collection          = collection,
            page_id             = page_id,
            deck_id             = deck_id,
            client              = client,
            stats               = stats,
            default_card_type   = default_card_type,
            card_type_overrides = card_type_overrides,
            enable_cloze        = enable_cloze,
            enable_gray_toggle_cloze = enable_gray_toggle_cloze,
            cloze_marker_colors = cloze_marker_colors,
            should_cancel       = should_cancel,
            warnings            = warnings,
        )

    snapshot = (
        prefetched_data.markdown_snapshot
        if prefetched_data is not None
        else get_page_markdown(page_id)
    )
    if not isinstance(snapshot, NotionMarkdownSnapshot) or snapshot.truncated:
        _LOG.info(
            "Page being parsed. page_id=%s reason=markdown_snapshot_unavailable "
            "truncated=%s mode=full_tree",
            page_id,
            getattr(snapshot, "truncated", None),
        )
        return _sync_page_content_full(
            db                  = db,
            collection          = collection,
            page_id             = page_id,
            deck_id             = deck_id,
            client              = client,
            stats               = stats,
            default_card_type   = default_card_type,
            card_type_overrides = card_type_overrides,
            enable_cloze        = enable_cloze,
            enable_gray_toggle_cloze = enable_gray_toggle_cloze,
            cloze_marker_colors = cloze_marker_colors,
            should_cancel       = should_cancel,
            warnings            = warnings,
        )

    shallow_blocks = (
        list(prefetched_data.shallow_blocks)
        if prefetched_data is not None
        else client.get_page_blocks_shallow(page_id)
    )
    shallow_toggles = [block for block in shallow_blocks if block.block_type == "toggle"]
    toggle_sources  = extract_root_toggle_markdown(snapshot.markdown)
    if toggle_sources is None or len(toggle_sources) != len(shallow_toggles):
        _LOG.warning(
            "Page being parsed. page_id=%s reason=markdown_toggle_alignment_ambiguous "
            "mode=full_tree markdown_toggles=%s notion_toggles=%d",
            page_id,
            None if toggle_sources is None else len(toggle_sources),
            len(shallow_toggles),
        )
        return _sync_page_content_full(
            db                  = db,
            collection          = collection,
            page_id             = page_id,
            deck_id             = deck_id,
            client              = client,
            stats               = stats,
            default_card_type   = default_card_type,
            card_type_overrides = card_type_overrides,
            enable_cloze        = enable_cloze,
            enable_gray_toggle_cloze = enable_gray_toggle_cloze,
            cloze_marker_colors = cloze_marker_colors,
            markdown            = snapshot.markdown,
            should_cancel       = should_cancel,
            warnings            = warnings,
        )

    return _sync_page_content_selective(
        db                   = db,
        collection           = collection,
        page_id              = page_id,
        deck_id              = deck_id,
        client               = client,
        stats                = stats,
        default_card_type    = default_card_type,
        card_type_overrides  = card_type_overrides,
        enable_cloze         = enable_cloze,
        enable_gray_toggle_cloze = enable_gray_toggle_cloze,
        cloze_marker_colors  = cloze_marker_colors,
        force_toggle_refresh = force_toggle_refresh,
        force_cloze_refresh  = force_cloze_refresh,
        should_cancel        = should_cancel,
        warnings             = warnings,
        snapshot             = snapshot,
        shallow_blocks       = shallow_blocks,
        shallow_toggles      = shallow_toggles,
        toggle_sources       = toggle_sources,
    )


def _sync_page_content_selective(
    *,
    db:                   Database,
    collection:           Any,
    page_id:              str,
    deck_id:              int,
    client:               NotionClient,
    stats:                SyncStats,
    default_card_type:    str,
    card_type_overrides:  dict[str, str],
    enable_cloze:         bool,
    enable_gray_toggle_cloze: bool,
    cloze_marker_colors:  list[str] | None,
    force_toggle_refresh: bool,
    force_cloze_refresh:  bool,
    should_cancel:        SyncCancelCheck | None,
    warnings:             list[SyncWarning] | None,
    snapshot:             NotionMarkdownSnapshot,
    shallow_blocks:       list[NotionBlock],
    shallow_toggles:      list[NotionBlock],
    toggle_sources:       tuple[str, ...],
) -> tuple[SyncStats, list[str], bool]:
    """Synchronize one trusted Markdown/shallow-block snapshot."""
    existing_cards       = _load_existing_cards_for_page(db, page_id)
    stored_source_hashes = _load_toggle_source_hashes(db, page_id)
    stored_page_hash     = _load_page_content_hash(db, page_id)
    current_page_hash    = hash_notion_markdown(snapshot.markdown)
    current_toggle_ids   = {toggle.block_id for toggle in shallow_toggles}
    cloze_parser         = ClozeCardParser(cloze_marker_colors)
    errors: list[str] = []
    page_parse_reasons: set[str] = set()
    _LOG.info(
        "Markdown page hash check. page_id=%s changed=%s stored=%s current=%s",
        page_id,
        stored_page_hash != current_page_hash,
        stored_page_hash,
        current_page_hash,
    )

    # Shallow block IDs are authoritative for root-toggle eligibility.
    for block_id, mapping in existing_cards.items():
        if mapping["excluded"]:
            continue

        normalized_type = normalize_card_type(mapping["card_type"], default=BASIC)
        if normalized_type in DEFAULT_SELECTABLE_CARD_TYPES and block_id not in current_toggle_ids:
            stats = _detach_mapping_with_warning(
                db       = db,
                stats    = stats,
                warnings = warnings,
                page_id  = page_id,
                block_id = block_id,
                code     = "source_no_longer_syncable",
                message  = (
                    "Mapped toggle is missing or no longer a top-level toggle; "
                    "its Anki note was preserved."
                ),
            )
    _delete_stale_toggle_source_hashes(db, page_id, current_toggle_ids)

    # Iterate over the shallow toggles and their corresponding Markdown sources, skipping any that are unchanged.
    for toggle, source_markdown in zip(shallow_toggles, toggle_sources):
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True

        block_id             = toggle.block_id
        source_hash          = hash_notion_markdown(source_markdown)
        previous_source_hash = stored_source_hashes.get(block_id)
        mapping              = existing_cards.get(block_id)
        stats                = _replace_stats(stats, cards_seen=stats.cards_seen + 1)

        # Skip toggles that are explicitly excluded, but still record their source hash so we don't repeatedly re-parse them.
        if mapping is not None and mapping["excluded"]:
            _upsert_toggle_source_hash(db, page_id, block_id, source_hash)
            stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
            continue

        expected_card_type = _expected_card_type_for_toggle(
            toggle,
            default_card_type=default_card_type,
            card_type_overrides=card_type_overrides,
            enable_cloze=enable_cloze,
            enable_gray_toggle_cloze=enable_gray_toggle_cloze,
            cloze_parser=cloze_parser,
        )

        parse_reasons: list[str] = []
        if force_toggle_refresh:
            parse_reasons.append("parser_refresh")
        if previous_source_hash is None:
            parse_reasons.append("no_stored_toggle_markdown")
        elif previous_source_hash != source_hash:
            parse_reasons.append("toggle_markdown_changed")
        needs_recursive_fetch = bool(parse_reasons)
        note: Any | None = None

        # If the toggle is unchanged and has a valid mapping, we can skip the recursive fetch.
        if mapping is None:
            if not needs_recursive_fetch:
                stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
                continue

        # Check if the existing mapping's card type or Anki note is no longer valid, which would require a recursive fetch.
        else:
            mapped_type = normalize_card_type(mapping["card_type"], default=BASIC)
            if mapped_type != expected_card_type:
                parse_reasons.append("card_type_changed")
                needs_recursive_fetch = True

            note_id = mapping["anki_note_id"]
            note = _get_note(collection, note_id) if note_id is not None else None
            if note_id is None or note is None:
                parse_reasons.append("anki_note_missing")
                needs_recursive_fetch = True
            elif _note_back_needs_mermaid_theme_upgrade(note):
                parse_reasons.append("mermaid_theme_upgrade")
                needs_recursive_fetch = True

        if not needs_recursive_fetch and mapping is not None:
            note_id = mapping["anki_note_id"]
            media_updated = False
            if note is not None and _note_contains_pending_media(note):
                stats, media_updated, media_errors = _backfill_pending_note_media(
                    db=db,
                    collection=collection,
                    note=note,
                    page_id=page_id,
                    block_id=block_id,
                    stats=stats,
                    warnings=warnings,
                )
                errors.extend(media_errors)
                if media_errors:
                    continue
            if note_id is not None:
                _ensure_note_cards_in_deck(collection, note_id, deck_id)
            if not media_updated:
                stats = _replace_stats(stats, cards_unchanged=stats.cards_unchanged + 1)
            continue

        # recursively fetch the toggle's children and parse them into card payloads
        page_parse_reasons.update(parse_reasons)
        children = client.get_block_children_recursive(block_id)
        expanded_toggle = NotionBlock(
            block_id=toggle.block_id,
            block_type=toggle.block_type,
            has_children=toggle.has_children,
            parent_id=toggle.parent_id,
            parent_type=toggle.parent_type,
            raw=toggle.raw,
            children=tuple(children),
        )
        enriched_toggle = merge_markdown_table_colors(
            [expanded_toggle], snapshot.markdown
        )[0]
        parse_result = _parse_cards_with_warnings(
            page_id=page_id,
            blocks=[enriched_toggle],
            default_card_type=default_card_type,
            card_type_overrides=card_type_overrides,
            enable_cloze=enable_cloze,
            enable_gray_toggle_cloze=enable_gray_toggle_cloze,
            cloze_marker_colors=cloze_marker_colors,
        )
        stats = _record_parser_warnings(stats, warnings, page_id, parse_result.warnings)

        if not parse_result.payloads:
            if not _has_parse_failure(parse_result.warnings):
                stats = _detach_mapping(db, stats, page_id, block_id)
                _upsert_toggle_source_hash(db, page_id, block_id, source_hash)
            continue

        # Sync each payload from the toggle, recording any errors and stopping if cancelled.
        toggle_errors: list[str] = []
        for payload in parse_result.payloads:
            stats, payload_errors, cancelled = _sync_one_payload(
                db=db,
                collection=collection,
                page_id=page_id,
                deck_id=deck_id,
                payload=payload,
                stats=stats,
                should_cancel=should_cancel,
                warnings=warnings,
            )
            toggle_errors.extend(payload_errors)
            if cancelled:
                return stats, errors + toggle_errors, True

        errors.extend(toggle_errors)
        if not toggle_errors:
            _upsert_toggle_source_hash(db, page_id, block_id, source_hash)

    cloze_parse_reasons = (
        _cloze_parse_reasons(
            collection=collection,
            blocks=shallow_blocks,
            existing_cards=existing_cards,
            current_toggle_ids=current_toggle_ids,
            markdown_changed=stored_page_hash != current_page_hash,
            force_cloze_refresh=force_cloze_refresh,
        )
        if enable_cloze
        else set()
    )
    if cloze_parse_reasons:
        page_parse_reasons.update(cloze_parse_reasons)
        stats, cloze_errors, cancelled = _sync_shallow_cloze_content(
            db                  = db,
            collection          = collection,
            page_id             = page_id,
            deck_id             = deck_id,
            blocks              = shallow_blocks,
            existing_cards      = existing_cards,
            stats               = stats,
            default_card_type   = default_card_type,
            card_type_overrides = card_type_overrides,
            enable_cloze        = enable_cloze,
            enable_gray_toggle_cloze = enable_gray_toggle_cloze,
            cloze_marker_colors = cloze_marker_colors,
            should_cancel       = should_cancel,
            warnings            = warnings,
        )
        errors.extend(cloze_errors)
        if cancelled:
            return stats, errors, True
    else:
        stats, local_media_errors = _sync_unchanged_cloze_cards_locally(
            db=db,
            collection=collection,
            page_id=page_id,
            existing_cards=existing_cards,
            current_toggle_ids=current_toggle_ids,
            deck_id=deck_id,
            stats=stats,
            warnings=warnings,
        )
        errors.extend(local_media_errors)

    if not errors:
        _set_page_content_hash(db, page_id, current_page_hash)
    if page_parse_reasons:
        _LOG.info(
            "Page parsed. page_id=%s reasons=%s",
            page_id,
            ",".join(sorted(page_parse_reasons)),
        )
    else:
        skip_reason = (
            "markdown_unchanged"
            if stored_page_hash == current_page_hash
            else "no_changed_card_sources"
        )
        _LOG.info("Page parsing skipped. page_id=%s reason=%s", page_id, skip_reason)
    return stats, errors, False


def _cloze_parse_reasons(
    *,
    collection: Any,
    blocks: list[NotionBlock],
    existing_cards: dict[str, dict[str, Any]],
    current_toggle_ids: set[str],
    markdown_changed: bool,
    force_cloze_refresh: bool,
) -> set[str]:
    """Return why shallow paragraph clozes must be parsed on this sync."""
    has_reconciliation_target = any(
        block.block_type == "paragraph"
        and not (
            existing_cards.get(block.block_id) is not None
            and existing_cards[block.block_id]["excluded"]
        )
        for block in blocks
    ) or any(
        not mapping["excluded"]
        and block_id not in current_toggle_ids
        and normalize_card_type(mapping["card_type"], default=BASIC) == CLOZE
        for block_id, mapping in existing_cards.items()
    )
    if not has_reconciliation_target:
        return set()

    reasons: set[str] = set()
    if markdown_changed:
        reasons.add("page_markdown_changed")
    if force_cloze_refresh:
        reasons.add("cloze_parser_refresh")

    # An unchanged source still needs parsing when its mapped note requires repair.
    for block_id, mapping in existing_cards.items():
        if mapping["excluded"] or block_id in current_toggle_ids:
            continue
        if normalize_card_type(mapping["card_type"], default=BASIC) != CLOZE:
            continue
        note_id = mapping["anki_note_id"]
        note = _get_note(collection, note_id) if note_id is not None else None
        if note_id is None or note is None:
            reasons.add("anki_note_missing")
        elif _note_back_needs_mermaid_theme_upgrade(note):
            reasons.add("mermaid_theme_upgrade")
            
    return reasons


def _sync_unchanged_cloze_cards_locally(
    *,
    db: Database,
    collection: Any,
    page_id: str,
    existing_cards: dict[str, dict[str, Any]],
    current_toggle_ids: set[str],
    deck_id: int,
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
) -> tuple[SyncStats, list[str]]:
    """Repair local media and keep skipped paragraph clozes in their deck."""
    errors: list[str] = []
    for block_id, mapping in existing_cards.items():
        if mapping["excluded"] or block_id in current_toggle_ids:
            continue
        if normalize_card_type(mapping["card_type"], default=BASIC) != CLOZE:
            continue

        stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)
        note_id = mapping["anki_note_id"]
        note = _get_note(collection, note_id) if note_id is not None else None
        if note_id is None or note is None:
            # Missing cloze notes are normally handled by _cloze_parse_reasons().
            continue

        media_updated = False
        if _note_contains_pending_media(note):
            stats, media_updated, media_errors = _backfill_pending_note_media(
                db=db,
                collection=collection,
                note=note,
                page_id=page_id,
                block_id=block_id,
                stats=stats,
                warnings=warnings,
            )
            errors.extend(media_errors)
            if media_errors:
                continue

        _ensure_note_cards_in_deck(collection, note_id, deck_id)
        if not media_updated:
            stats = _replace_stats(stats, cards_unchanged=stats.cards_unchanged + 1)
    return stats, errors


def _sync_shallow_cloze_content(
    *,
    db:                  Database,
    collection:          Any,
    page_id:             str,
    deck_id:             int,
    blocks:              list[NotionBlock],
    existing_cards:      dict[str, dict[str, Any]],
    stats:               SyncStats,
    default_card_type:   str,
    card_type_overrides: dict[str, str],
    enable_cloze:        bool,
    enable_gray_toggle_cloze: bool,
    cloze_marker_colors: list[str] | None,
    should_cancel:       SyncCancelCheck | None,
    warnings:            list[SyncWarning] | None,
) -> tuple[SyncStats, list[str], bool]:
    """Parse top-level cloze paragraphs directly from the shallow page result."""
    if not enable_cloze:
        return stats, [], False

    candidate_ids: set[str] = set()
    for block in blocks:
        if block.block_type != "paragraph":
            continue
        mapping = existing_cards.get(block.block_id)
        if mapping is not None and mapping["excluded"]:
            stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
            continue
        candidate_ids.add(block.block_id)

    if candidate_ids:
        parse_result = _parse_cards_with_warnings(
            page_id=page_id,
            blocks=blocks,
            default_card_type=default_card_type,
            card_type_overrides=card_type_overrides,
            enable_cloze=True,
            enable_gray_toggle_cloze=enable_gray_toggle_cloze,
            cloze_marker_colors=cloze_marker_colors,
            include_block_ids=candidate_ids,
        )
    else:
        parse_result = CardParseResult(payloads=(), warnings=())
    stats = _record_parser_warnings(stats, warnings, page_id, parse_result.warnings)
    cloze_payloads = [payload for payload in parse_result.payloads if payload.card_type == CLOZE]

    if not _has_parse_failure(parse_result.warnings):
        current_cloze_ids = {payload.notion_block_id for payload in cloze_payloads}
        root_toggle_ids = {
            block.block_id for block in blocks if block.block_type == "toggle"
        }
        stats = _detach_stale_paragraph_cloze_mappings(
            db=db,
            stats=stats,
            warnings=warnings,
            page_id=page_id,
            existing_cards=existing_cards,
            current_cloze_ids=current_cloze_ids,
            root_toggle_ids=root_toggle_ids,
        )

    errors: list[str] = []
    for payload in cloze_payloads:
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True
        stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)
        stats, payload_errors, cancelled = _sync_one_payload(
            db=db,
            collection=collection,
            page_id=page_id,
            deck_id=deck_id,
            payload=payload,
            stats=stats,
            should_cancel=should_cancel,
            warnings=warnings,
        )
        errors.extend(payload_errors)
        if cancelled:
            return stats, errors, True
    return stats, errors, False


def _sync_page_content_full(
    db: Database,
    collection: Any,
    page_id: str,
    deck_id: int,
    client: NotionClient,
    stats: SyncStats,
    default_card_type: str,
    card_type_overrides: dict[str, str],
    enable_cloze: bool,
    enable_gray_toggle_cloze: bool = True,
    cloze_marker_colors: list[str] | None = None,
    markdown: str | None = None,
    should_cancel: SyncCancelCheck | None = None,
    warnings: list[SyncWarning] | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Sync a page by recursively inspecting every eligible toggle."""
    existing_cards = _load_existing_cards_for_page(db, page_id)
    toggle_payloads: list[ToggleCardPayload] = []
    errors: list[str] = []

    # Fetch one complete snapshot so all descendants are retrieved concurrently
    # and every parser on this page observes the same Notion block tree.
    blocks = client.get_page_content(page_id)
    if markdown:
        blocks = merge_markdown_table_colors(blocks, markdown)
    toggles = [block for block in blocks if block.block_type == "toggle"]
    toggle_ids = {block.block_id for block in toggles}
    _LOG.debug("Fetched complete page tree. page_id=%s blocks=%d toggles=%d", page_id, len(blocks), len(toggles))

    # Detach old mappings whose source is no longer eligible while preserving their Anki notes.
    for block_id, mapping in existing_cards.items():
        if mapping["excluded"]:
            continue

        normalized_type = normalize_card_type(mapping["card_type"], default=BASIC)
        if normalized_type in DEFAULT_SELECTABLE_CARD_TYPES and block_id not in toggle_ids:
            stats = _detach_mapping_with_warning(
                db       = db,
                stats    = stats,
                warnings = warnings,
                page_id  = page_id,
                block_id = block_id,
                code     = "source_no_longer_syncable",
                message  = "Mapped toggle is missing or no longer a top-level toggle; its Anki note was preserved.",
            )

    for toggle in toggles:
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True

        # Count every root toggle as "seen", even when fast-skip decides no work is needed.
        stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)

        mapping = existing_cards.get(toggle.block_id)
        if mapping is not None and mapping["excluded"]:
            # Excluded cards must be skipped before expansion/parsing.
            _LOG.info("Card skipped because it is excluded. page_id=%s block_id=%s", page_id, toggle.block_id)
            stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
            continue

        effective_card_type = _effective_card_type_for_block(
            toggle.block_id,
            default_card_type=default_card_type,
            card_type_overrides=card_type_overrides,
        )

        # The page-tree fetch has already expanded this toggle and its descendants.
        _LOG.debug("Parsing expanded toggle. page_id=%s block_id=%s children=%d card_type=%s", page_id, toggle.block_id, len(toggle.children), effective_card_type)
        parse_result = _parse_cards_with_warnings(
            page_id=page_id,
            blocks=[toggle],
            default_card_type=default_card_type,
            card_type_overrides=card_type_overrides,
            enable_cloze=enable_cloze,
            enable_gray_toggle_cloze=enable_gray_toggle_cloze,
            cloze_marker_colors=cloze_marker_colors,
        )
        stats = _record_parser_warnings(stats, warnings, page_id, parse_result.warnings)
        toggle_payloads.extend(parse_result.payloads)

        # If the parser produced no payloads but also no parse failures, detach the mapping to preserve the Anki note.
        if not parse_result.payloads and parse_result.warnings and not _has_parse_failure(parse_result.warnings):
            stats = _detach_mapping(db, stats, page_id, toggle.block_id)

    # Sync only the payloads we actually expanded.
    for payload in toggle_payloads:
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True
        stats, payload_errors, cancelled = _sync_one_payload(
            db=db,
            collection=collection,
            page_id=page_id,
            deck_id=deck_id,
            payload=payload,
            stats=stats,
            should_cancel=should_cancel,
            warnings=warnings,
        )
        errors.extend(payload_errors)
        if cancelled:
            return stats, errors, True

    # sync colze cards
    if enable_cloze:
        # Cloze payloads come from top-level paragraphs; exclude toggle parsing here.
        cloze_candidate_block_ids: set[str] = set()
        for block in blocks:
            if block.block_type != "paragraph":
                continue
            mapping = existing_cards.get(block.block_id)
            if mapping is not None and mapping["excluded"]:
                # Keep excluded cloze cards out of parsing entirely.
                _LOG.info("Cloze candidate skipped because it is excluded. page_id=%s block_id=%s", page_id, block.block_id)
                stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
                continue
            cloze_candidate_block_ids.add(block.block_id)
        cloze_payloads: list[ToggleCardPayload] = []
        if cloze_candidate_block_ids:
            parse_result = _parse_cards_with_warnings(
                page_id             = page_id,
                blocks              = blocks,
                default_card_type   = default_card_type,
                card_type_overrides = card_type_overrides,
                enable_cloze        = True,
                enable_gray_toggle_cloze = enable_gray_toggle_cloze,
                cloze_marker_colors = cloze_marker_colors,
                include_block_ids   = cloze_candidate_block_ids,
            )
            stats = _record_parser_warnings(stats, warnings, page_id, parse_result.warnings)

            cloze_payloads = [payload for payload in parse_result.payloads if payload.card_type == CLOZE]
            if not _has_parse_failure(parse_result.warnings):
                current_cloze_ids = {payload.notion_block_id for payload in cloze_payloads}
                stats = _detach_stale_paragraph_cloze_mappings(
                    db=db,
                    stats=stats,
                    warnings=warnings,
                    page_id=page_id,
                    existing_cards=existing_cards,
                    current_cloze_ids=current_cloze_ids,
                    root_toggle_ids=toggle_ids,
                )

        if cloze_payloads:
            _LOG.debug("Parsed cloze payloads. page_id=%s payloads=%d", page_id, len(cloze_payloads))
            for payload in cloze_payloads:
                if _is_sync_cancelled(should_cancel):
                    return stats, errors, True
                stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)
                stats, payload_errors, cancelled = _sync_one_payload(
                    db=db,
                    collection=collection,
                    page_id=page_id,
                    deck_id=deck_id,
                    payload=payload,
                    stats=stats,
                    should_cancel=should_cancel,
                    warnings=warnings,
                )
                errors.extend(payload_errors)
                if cancelled:
                    return stats, errors, True

    return stats, errors, False


def _detach_stale_paragraph_cloze_mappings(
    *,
    db: Database,
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
    page_id: str,
    existing_cards: dict[str, dict[str, Any]],
    current_cloze_ids: set[str],
    root_toggle_ids: set[str],
) -> SyncStats:
    """Detach stale paragraph clozes without touching advanced-toggle mappings."""
    for block_id, mapping in existing_cards.items():
        if mapping["excluded"]:
            continue
        if normalize_card_type(mapping["card_type"], default=BASIC) != CLOZE:
            continue
        # Advanced cloze cards share the cloze note type but are reconciled by
        # the root-toggle pass, not by this top-level paragraph pass.
        if block_id in root_toggle_ids or block_id in current_cloze_ids:
            continue
        stats = _detach_mapping_with_warning(
            db=db,
            stats=stats,
            warnings=warnings,
            page_id=page_id,
            block_id=block_id,
            code="source_no_longer_syncable",
            message=(
                "Mapped cloze paragraph is missing or no longer contains usable "
                "cloze text; its Anki note was preserved."
            ),
        )
    return stats


def _parse_cards_with_warnings(
    *,
    page_id:             str,
    blocks:              list[NotionBlock],
    default_card_type:   str,
    card_type_overrides: dict[str, str],
    enable_cloze:        bool,
    enable_gray_toggle_cloze: bool = True,
    cloze_marker_colors: list[str] | None = None,
    include_block_ids:   set[str] | None = None,
) -> CardParseResult:
    """Parse cards and convert unexpected parser exceptions into warnings."""
    parse_warnings: list[CardParseWarning] = []
    try:
        payloads = parse_page_to_cards(
            page_id,
            blocks,
            default_card_type   = default_card_type,
            card_type_overrides = card_type_overrides,
            enable_cloze        = enable_cloze,
            enable_gray_toggle_cloze = enable_gray_toggle_cloze,
            cloze_marker_colors = cloze_marker_colors,
            include_block_ids   = include_block_ids,
            warnings            = parse_warnings,
        )
    except Exception as exc:
        target_ids = include_block_ids or {block.block_id for block in blocks}
        target_id  = next(iter(target_ids), "")
        parse_warnings.append(
            CardParseWarning(
                code            = "card_parse_failed",
                message         = f"Card parsing failed and the existing mapping was preserved: {exc}",
                notion_block_id = target_id,
            )
        )
        payloads = []

    return CardParseResult(payloads=tuple(payloads), warnings=tuple(parse_warnings))


def _record_parser_warnings(
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
    page_id: str,
    parse_warnings: tuple[CardParseWarning, ...],
) -> SyncStats:
    """Promote parser diagnostics to sync warnings without failing the run."""
    for warning in parse_warnings:
        stats = _add_warning(
            stats,
            warnings,
            code     = warning.code,
            message  = warning.message,
            page_id  = page_id,
            block_id = warning.notion_block_id or None,
        )

    return stats


def _has_parse_failure(parse_warnings: tuple[CardParseWarning, ...]) -> bool:
    """Return whether parsing raised unexpectedly and mappings must be preserved."""
    return any(warning.code == "card_parse_failed" for warning in parse_warnings)


def _add_warning(
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
    *,
    code: str,
    message: str,
    page_id: str | None,
    block_id: str | None,
) -> SyncStats:
    """Record one recoverable sync condition and increment warning statistics."""
    warning = SyncWarning(
        code     = code,
        message  = message,
        page_id  = page_id,
        block_id = block_id,
    )
    if warnings is not None:
        warnings.append(warning)

    _LOG.warning(
        "Sync warning. code=%s page_id=%s block_id=%s message=%s",
        code,
        page_id,
        block_id,
        message,
    )
    return _replace_stats(stats, cards_warned=stats.cards_warned + 1)


def _detach_mapping_with_warning(
    *,
    db:       Database,
    stats:    SyncStats,
    warnings: list[SyncWarning] | None,
    page_id:  str,
    block_id: str,
    code:     str,
    message:  str,
) -> SyncStats:
    """Warn and detach one stale mapping while preserving the Anki note."""
    stats = _add_warning(
        stats,
        warnings,
        code     = code,
        message  = message,
        page_id  = page_id,
        block_id = block_id,
    )

    return _detach_mapping(db, stats, page_id, block_id)


def _detach_mapping(db: Database, stats: SyncStats, page_id: str, block_id: str) -> SyncStats:
    """Remove Noteck metadata for one block without deleting its Anki note."""
    connection = db.connect()
    try:
        connection.execute(
            "DELETE FROM card_type_overrides WHERE notion_block_id = ?",
            (block_id,),
        )
        cursor = connection.execute(
            "DELETE FROM cards WHERE notion_block_id = ? AND notion_page_id = ?",
            (block_id, page_id),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()

    if cursor.rowcount <= 0:
        return stats

    _LOG.info(
        "Card detached. page_id=%s block_id=%s reason=source_no_longer_syncable",
        page_id,
        block_id,
    )
    return _replace_stats(stats, cards_detached=stats.cards_detached + 1)


def _load_cloze_refresh_revision(db: Database) -> str | None:
    """Return the last completed internal cloze-refresh revision, if any."""
    return _as_optional_string(db.get_setting(_CLOZE_REFRESH_REVISION_SETTING_KEY))


def _set_cloze_refresh_revision(db: Database, value: str) -> None:
    """Persist the latest completed internal cloze-refresh revision."""
    db.set_setting(_CLOZE_REFRESH_REVISION_SETTING_KEY, value)


def _load_gray_toggle_cloze_enabled(db: Database) -> bool:
    """Return the gray-toggle cloze option used by the last successful sync."""
    return db.get_setting(_GRAY_TOGGLE_CLOZE_ENABLED_SETTING_KEY) == "1"


def _set_gray_toggle_cloze_enabled(db: Database, enabled: bool) -> None:
    """Persist the gray-toggle cloze option used by this successful sync."""
    db.set_setting(_GRAY_TOGGLE_CLOZE_ENABLED_SETTING_KEY, "1" if enabled else "0")


def _load_cloze_marker_colors(db: Database) -> list[str] | None:
    """Return the marker-color selection used by the last successful sync."""
    raw_value = db.get_setting(_CLOZE_MARKER_COLORS_SETTING_KEY)
    # Existing installations predate this snapshot and treated every color as enabled.
    return raw_value.split(",") if raw_value is not None else list(CLOZE_MARKER_COLORS)


def cloze_marker_colors_need_sync(db: Database, colors: list[str]) -> bool:
    """Return whether current marker colors differ from the last successful sync."""
    return _load_cloze_marker_colors(db) != list(colors)


def _set_cloze_marker_colors(db: Database, colors: list[str]) -> None:
    """Persist the marker-color selection used by this successful sync."""
    db.set_setting(_CLOZE_MARKER_COLORS_SETTING_KEY, ",".join(colors))


def _load_toggle_refresh_revision(db: Database) -> str | None:
    """Return the last completed internal toggle-refresh revision, if any."""
    return _as_optional_string(db.get_setting(_TOGGLE_REFRESH_REVISION_SETTING_KEY))


def _set_toggle_refresh_revision(db: Database, value: str) -> None:
    """Persist the latest completed internal toggle-refresh revision."""
    db.set_setting(_TOGGLE_REFRESH_REVISION_SETTING_KEY, value)


def _effective_card_type_for_block(
    block_id: str,
    *,
    default_card_type: str,
    card_type_overrides: dict[str, str],
) -> str:
    """Resolve the effective selectable card type for one block id."""
    if block_id in card_type_overrides:
        return normalize_default_selectable_card_type(card_type_overrides[block_id])
    return normalize_default_selectable_card_type(default_card_type)


def _expected_card_type_for_toggle(
    toggle: NotionBlock,
    *,
    default_card_type:        str,
    card_type_overrides:      dict[str, str],
    enable_cloze:             bool,
    enable_gray_toggle_cloze: bool,
    cloze_parser:             ClozeCardParser,
) -> str:
    """Resolve the type a shallow root toggle will produce when parsed."""
    if enable_cloze and cloze_parser.is_advanced_container(
        toggle,
        enable_gray_toggle_cloze=enable_gray_toggle_cloze,
    ):
        return CLOZE
    
    return _effective_card_type_for_block(
        toggle.block_id,
        default_card_type=default_card_type,
        card_type_overrides=card_type_overrides,
    )


def _sync_one_payload(
    db: Database,
    collection: Any,
    page_id: str,
    deck_id: int,
    payload: ToggleCardPayload,
    stats: SyncStats,
    should_cancel: SyncCancelCheck | None = None,
    warnings: list[SyncWarning] | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Sync a single card payload (small wrapper around the existing mapping logic)."""
    if _is_sync_cancelled(should_cancel):
        return stats, [], True

    existing_cards = _load_existing_cards_for_page(db, page_id)
    mapping = existing_cards.get(payload.notion_block_id)
    if mapping is not None and mapping["excluded"]:
        _LOG.info("Payload skipped because its mapping is excluded. page_id=%s block_id=%s", page_id, payload.notion_block_id)
        return _replace_stats(stats, cards_skipped=stats.cards_skipped + 1), [], False

    # Parser services may intentionally construct deterministic payloads for
    # incomplete cloze containers. Validate at the write boundary so no create,
    # recreation, or update path can send markup Anki cannot generate cards for.
    if payload.card_type == CLOZE:
        validation = ClozeCardParser().validate(payload)
        if not validation.is_valid:
            message = "; ".join(validation.errors)
            stats = _add_warning(
                stats,
                warnings,
                code="invalid_cloze_card",
                message=f"Invalid cloze card was skipped: {message}",
                page_id=page_id,
                block_id=payload.notion_block_id,
            )
            return _replace_stats(stats, cards_skipped=stats.cards_skipped + 1), [], False

    try:
        model_name = payload.model_name or MODEL_NAME_BASIC
        model = _model_by_name(collection, model_name)
        if model is None:
            raise SyncError(f"Anki note type '{model_name}' is not available.")

        if mapping is None:
            recovered = _find_existing_note_for_payload(collection, payload)
            if recovered is not None:
                recovered_note_id, recovered_note, candidate_count = recovered
                stats = _relink_existing_note(
                    db              = db,
                    collection      = collection,
                    page_id         = page_id,
                    deck_id         = deck_id,
                    payload         = payload,
                    note_id         = recovered_note_id,
                    note            = recovered_note,
                    candidate_count = candidate_count,
                    stats           = stats,
                    warnings        = warnings,
                    reason          = "missing_mapping",
                )
                return stats, [], False

            prepared_payload, stats = _prepare_payload_media_with_warnings(collection, payload, stats, warnings)
            note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, note_id, page_id)
            _LOG.info("Card created. page_id=%s block_id=%s note_id=%s card_type=%s reason=new_mapping", page_id, payload.notion_block_id, note_id, payload.card_type)
            return _replace_stats(stats, cards_created=stats.cards_created + 1), [], False

        note_id = mapping["anki_note_id"]
        if note_id is None:
            recovered = _find_existing_note_for_payload(collection, payload)
            if recovered is not None:
                recovered_note_id, recovered_note, candidate_count = recovered
                stats = _relink_existing_note(
                    db              = db,
                    collection      = collection,
                    page_id         = page_id,
                    deck_id         = deck_id,
                    payload         = payload,
                    note_id         = recovered_note_id,
                    note            = recovered_note,
                    candidate_count = candidate_count,
                    stats           = stats,
                    warnings        = warnings,
                    reason          = "mapping_without_note_id",
                )
                return stats, [], False

            prepared_payload, stats = _prepare_payload_media_with_warnings(collection, payload, stats, warnings)
            note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, note_id, page_id)
            _LOG.info("Card recreated. page_id=%s block_id=%s note_id=%s card_type=%s reason=mapping_without_note", page_id, payload.notion_block_id, note_id, payload.card_type)
            stats = _add_warning(
                stats,
                warnings,
                code="mapping_without_note",
                message="Card mapping had no Anki note ID and the note was recreated.",
                page_id=page_id,
                block_id=payload.notion_block_id,
            )
            return _replace_stats(stats, cards_created=stats.cards_created + 1), [], False

        note = _get_note(collection, note_id)
        if note is None:
            stats = _replace_stats(stats, cards_missing_note=stats.cards_missing_note + 1)
            recovered = _find_existing_note_for_payload(collection, payload)
            if recovered is not None:
                recovered_note_id, recovered_note, candidate_count = recovered
                stats = _relink_existing_note(
                    db              = db,
                    collection      = collection,
                    page_id         = page_id,
                    deck_id         = deck_id,
                    payload         = payload,
                    note_id         = recovered_note_id,
                    note            = recovered_note,
                    candidate_count = candidate_count,
                    stats           = stats,
                    warnings        = warnings,
                    reason          = "stale_note_id",
                )
                return stats, [], False

            prepared_payload, stats = _prepare_payload_media_with_warnings(collection, payload, stats, warnings)
            note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, note_id, page_id)
            _LOG.info("Card recreated. page_id=%s block_id=%s previous_note_id=%s note_id=%s card_type=%s reason=missing_anki_note", page_id, payload.notion_block_id, mapping["anki_note_id"], note_id, payload.card_type)
            stats = _add_warning(
                stats,
                warnings,
                code="missing_anki_note",
                message="Mapped Anki note was missing and was recreated.",
                page_id=page_id,
                block_id=payload.notion_block_id,
            )
            return _replace_stats(stats, cards_created=stats.cards_created + 1), [], False

        if mapping["card_type"] != payload.card_type:
            _LOG.info("Card invalidated and recreated due to type change. page_id=%s block_id=%s note_id=%s old_type=%s new_type=%s", page_id, payload.notion_block_id, note_id, mapping["card_type"], payload.card_type)
            _delete_note(collection, note_id)
            prepared_payload, stats = _prepare_payload_media_with_warnings(collection, payload, stats, warnings)
            recreated_note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, recreated_note_id, page_id)
            _LOG.info(
                "Card recreation completed. page_id=%s block_id=%s previous_note_id=%s note_id=%s",
                page_id,
                payload.notion_block_id,
                note_id,
                recreated_note_id,
            )
            return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False

        _ensure_note_cards_in_deck(collection, note_id, deck_id)
        if mapping["content_hash"] == payload.content_hash:
            if _note_back_needs_mermaid_theme_upgrade(note):
                prepared_payload, stats = _prepare_payload_media_with_warnings(collection, payload, stats, warnings)
                _apply_payload_to_note(note, prepared_payload)
                _update_note(collection, note)
                _upsert_card_mapping(db, prepared_payload, note_id, page_id)
                _LOG.info("Card updated. page_id=%s block_id=%s note_id=%s reason=mermaid_theme_upgrade", page_id, payload.notion_block_id, note_id)
                return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False
            # Backfill older notes that still contain sync-time media placeholders.
            if _note_contains_pending_media(note):
                stats, media_updated, media_errors = _backfill_pending_note_media(
                    db=db,
                    collection=collection,
                    note=note,
                    page_id=page_id,
                    block_id=payload.notion_block_id,
                    stats=stats,
                    warnings=warnings,
                )
                if media_errors:
                    return stats, media_errors, False
                if media_updated:
                    return stats, [], False
            _LOG.debug("Card unchanged. page_id=%s block_id=%s note_id=%s card_type=%s", page_id, payload.notion_block_id, note_id, payload.card_type)
            return _replace_stats(stats, cards_unchanged=stats.cards_unchanged + 1), [], False

        prepared_payload, stats = _prepare_payload_media_with_warnings(collection, payload, stats, warnings)
        _apply_payload_to_note(note, prepared_payload)
        _update_note(collection, note)
        if payload.card_type == CLOZE:
            # Updating a cloze note creates missing ordinals but Anki requires a
            # separate empty-card pass to remove ordinals no longer in Text.
            _remove_empty_cards_for_note(collection, note_id)
        _upsert_card_mapping(db, prepared_payload, note_id, page_id)
        _LOG.info("Card updated. page_id=%s block_id=%s note_id=%s reason=content_changed", page_id, payload.notion_block_id, note_id)
        return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False
    except Exception as exc:
        _LOG.exception("Card sync failed. page_id=%s block_id=%s", page_id, payload.notion_block_id)
        return stats, [f"Block {payload.notion_block_id}: {exc}"], False


def _load_page_last_seen_notion_edit_time(db: Database, page_id: str) -> str | None:
    """Return the last seen Notion edit time for a page (fast sync bookkeeping)."""
    connection = db.connect()
    try:
        row = connection.execute(
            """
            SELECT last_seen_notion_edit_time
            FROM pages
            WHERE notion_page_id = ?
            """,
            (page_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None
    value = row["last_seen_notion_edit_time"]
    return str(value) if isinstance(value, str) and value else None


def _set_page_last_seen_notion_edit_time(db: Database, page_id: str, value: str) -> None:
    """Persist the last seen Notion edit time for a page."""
    connection = db.connect()
    try:
        connection.execute(
            """
            UPDATE pages
            SET last_seen_notion_edit_time = ?
            WHERE notion_page_id = ?
            """,
            (value, page_id),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _as_optional_string(value: Any) -> str | None:
    """Normalize optional values to strings or None."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


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


def _resolve_page_deck(
    collection: Any,
    stored_deck_name: str,
    stored_deck_id: int | None,
) -> tuple[int, str]:
    """Resolve one page deck by id first and return canonical `(deck_id, deck_name)`."""
    # Keep persisted data stable even when legacy rows contain whitespace-only deck names.
    normalized_stored_name = stored_deck_name.strip() or "Notion"
    if stored_deck_id is not None:
        deck_name_by_id = _deck_name_by_id(collection, stored_deck_id)
        if deck_name_by_id is not None:
            return stored_deck_id, deck_name_by_id

    # Fallback path: resolve by name and create when missing.
    deck_id = _ensure_deck_id(collection, normalized_stored_name)
    resolved_name = _deck_name_by_id(collection, deck_id) or normalized_stored_name
    return deck_id, resolved_name


def _deck_name_by_id(collection: Any, deck_id: int) -> str | None:
    """Return current deck name for an id when the deck exists, otherwise `None`."""
    decks = getattr(collection, "decks", None)
    if decks is None:
        return None

    # Try direct name lookup APIs first.
    for attr_name in ("name_if_exists", "name", "nameForDid"):
        lookup = getattr(decks, attr_name, None)
        if not callable(lookup):
            continue
        try:
            resolved = lookup(deck_id)
        except Exception:
            continue
        if isinstance(resolved, str):
            cleaned = resolved.strip()
            if cleaned and cleaned.lower() != "[no deck]" and cleaned.lower() != "default":
                return cleaned

    # Fallback: some APIs expose deck metadata dictionaries.
    get_fn = getattr(decks, "get", None)
    if callable(get_fn):
        try:
            resolved = get_fn(deck_id)
        except Exception:
            resolved = None
        if isinstance(resolved, dict):
            name_value = resolved.get("name")
            if isinstance(name_value, str):
                cleaned = name_value.strip()
                if cleaned and cleaned.lower() != "[no deck]" and cleaned.lower() != "default":
                    return cleaned

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
    field_values: dict[str, str]
    if payload.fields:
        field_values = dict(payload.fields)
    else:
        field_values = {
            "Front": payload.front_html,
            "Back": payload.back_html,
            "Notion Block ID": payload.notion_block_id,
        }

    if "Notion Block ID" not in field_values:
        field_values["Notion Block ID"] = payload.notion_block_id

    for field_name, field_value in field_values.items():
        try:
            note[field_name] = field_value
        except Exception:
            continue


_MEDIA_NOTE_FIELDS = ("Back", "Text", "Extra")


def _contains_pending_media_html(value: str) -> bool:
    """Return whether rendered HTML still contains media requiring localization."""
    lowered = value.lower()
    return ('data-mermaid="' in lowered) or ("<img" in lowered and 'src="http' in lowered)


def _note_contains_pending_media(note: Any) -> bool:
    """Return whether any rendered note field still contains pending media."""
    return any(
        _contains_pending_media_html(_safe_note_field(note, field_name))
        for field_name in _MEDIA_NOTE_FIELDS
    )


def _backfill_pending_note_media(
    *,
    db: Database,
    collection: Any,
    note: Any,
    page_id: str,
    block_id: str,
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
) -> tuple[SyncStats, bool, list[str]]:
    """Retry media from saved note HTML without refetching unchanged Notion blocks."""
    try:
        prepared_fields: dict[str, str] = {}
        for field_name in _MEDIA_NOTE_FIELDS:
            original_html = _safe_note_field(note, field_name)
            if not _contains_pending_media_html(original_html):
                continue

            prepared_html = _prepare_back_html_media(collection, original_html)
            stats = _record_media_fallback_warnings(
                stats=stats,
                warnings=warnings,
                original_back=original_html,
                prepared_back=prepared_html,
                page_id=page_id,
                block_id=block_id,
            )
            if prepared_html != original_html:
                prepared_fields[field_name] = prepared_html

        if not prepared_fields:
            _LOG.info(
                "Pending media fallback retained without Notion refetch. "
                "page_id=%s block_id=%s",
                page_id,
                block_id,
            )
            return stats, False, []

        for field_name, prepared_html in prepared_fields.items():
            note[field_name] = prepared_html
        
        _update_note(collection, note)
        _mark_card_synced(db, block_id)
        _LOG.info(
            "Pending media backfilled locally. page_id=%s block_id=%s",
            page_id,
            block_id,
        )
        return _replace_stats(stats, cards_updated=stats.cards_updated + 1), True, []
    
    except Exception as exc:
        _LOG.exception(
            "Pending media backfill failed. page_id=%s block_id=%s",
            page_id,
            block_id,
        )
        return stats, False, [f"Block {block_id}: {exc}"]


def _note_back_needs_mermaid_theme_upgrade(note: Any) -> bool:
    """Return whether a note contains legacy single Mermaid SVG markup."""
    back_html = _safe_note_field(note, "Back")
    lowered = back_html.lower()
    return ('src="notion_mermaid_' in lowered) and ('class="notion-mermaid-dark"' not in lowered)


def _safe_note_field(note: Any, field_name: str) -> str:
    """Read one note field defensively across dict-like note implementations."""
    try:
        value = note[field_name]
    except Exception:
        return ""
    return str(value) if value is not None else ""


def _prepare_payload_media(collection: Any, payload: ToggleCardPayload) -> ToggleCardPayload:
    """Download external media and rewrite HTML to local collection media filenames."""
    rewritten_back_html = _prepare_back_html_media(collection, payload.back_html)
    rewritten_fields = dict(payload.fields) if payload.fields else {}
    for field_name in _MEDIA_NOTE_FIELDS:
        if field_name in rewritten_fields:
            rewritten_fields[field_name] = _prepare_back_html_media(
                collection,
                rewritten_fields[field_name],
            )

    return ToggleCardPayload(
        notion_page_id=payload.notion_page_id,
        notion_block_id=payload.notion_block_id,
        front_html=payload.front_html,
        back_html=rewritten_back_html,
        card_type=payload.card_type,
        model_name=payload.model_name,
        fields=rewritten_fields,
        content_hash=payload.content_hash,
        last_edited_time=payload.last_edited_time,
    )


def _prepare_payload_media_with_warnings(
    collection: Any,
    payload: ToggleCardPayload,
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
) -> tuple[ToggleCardPayload, SyncStats]:
    """Prepare media and report recoverable localization fallbacks."""
    prepared      = _prepare_payload_media(collection, payload)
    media_field_names = [
        field_name for field_name in _MEDIA_NOTE_FIELDS
        if field_name in payload.fields
    ]
    if "Back" not in media_field_names:
        stats = _record_media_fallback_warnings(
            stats=stats,
            warnings=warnings,
            original_back=payload.back_html,
            prepared_back=prepared.back_html,
            page_id=payload.notion_page_id,
            block_id=payload.notion_block_id,
        )
    for field_name in media_field_names:
        stats = _record_media_fallback_warnings(
            stats=stats,
            warnings=warnings,
            original_back=payload.fields[field_name],
            prepared_back=prepared.fields[field_name],
            page_id=payload.notion_page_id,
            block_id=payload.notion_block_id,
        )
    return prepared, stats


def _record_media_fallback_warnings(
    *,
    stats: SyncStats,
    warnings: list[SyncWarning] | None,
    original_back: str,
    prepared_back: str,
    page_id: str,
    block_id: str,
) -> SyncStats:
    """Record unresolved media while treating the retained HTML as a valid fallback."""
    if _contains_remote_image(original_back) and _contains_remote_image(prepared_back):
        stats = _add_warning(
            stats,
            warnings,
            code     = "media_localization_failed",
            message  = "Remote image could not be localized; its original URL was retained.",
            page_id  = page_id,
            block_id = block_id,
        )
    if "data-mermaid=" in original_back.lower() and "data-mermaid=" in prepared_back.lower():
        stats = _add_warning(
            stats,
            warnings,
            code     = "mermaid_render_failed",
            message  = "Mermaid diagram could not be rendered; its source fallback was retained.",
            page_id  = page_id,
            block_id = block_id,
        )
    return stats


def _contains_remote_image(value: str) -> bool:
    """Return whether HTML still references an HTTP(S) image source."""
    for match in _IMAGE_TAG_RE.finditer(value):
        if _is_http_url(html.unescape(match.group("src")).strip()):
            return True
    return False


def _prepare_back_html_media(collection: Any, back_html: str) -> str:
    """Rewrite one Back HTML payload to local media references when possible."""
    media_dir = _resolve_media_directory(collection)
    if media_dir is None:
        return back_html
    html_with_local_images = _localize_image_sources(back_html, media_dir)
    return _render_mermaid_sources(html_with_local_images, media_dir)


def _resolve_media_directory(collection: Any) -> Path | None:
    """Return the Anki collection media directory when available."""
    media = getattr(collection, "media", None)
    if media is None:
        return None

    media_dir = getattr(media, "dir", None)
    if callable(media_dir):
        try:
            raw_path = media_dir()
        except Exception:
            return None
    else:
        raw_path = media_dir

    if not isinstance(raw_path, (str, Path)):
        return None

    path = Path(raw_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _localize_image_sources(source_html: str, media_dir: Path) -> str:
    """Download remote image sources and rewrite tags to local media files."""
    if "<img" not in source_html.lower():
        return source_html

    cache: dict[str, tuple[str, str]] = {}

    def replace(match: re.Match[str]) -> str:
        encoded_src = match.group("src")
        remote_src = html.unescape(encoded_src).strip()
        if remote_src in cache:
            local_name = cache[remote_src]
            return f'<img{match.group("before")} src="{html.escape(local_name, quote=True)}"{match.group("after")}>'

        local_name = _download_and_store_image(remote_src, media_dir)
        if not local_name:
            return match.group(0)

        cache[remote_src] = local_name
        return f'<img{match.group("before")} src="{html.escape(local_name, quote=True)}"{match.group("after")}>'

    return _IMAGE_TAG_RE.sub(replace, source_html)


def _download_and_store_image(remote_src: str, media_dir: Path) -> str:
    """Download one remote image URL into collection media and return its filename."""
    if not _is_http_url(remote_src):
        return ""

    image_data = _download_bytes(remote_src)
    if image_data is None:
        return ""

    filename = _image_media_filename(remote_src)
    if not filename:
        return ""

    _write_media_file(media_dir, filename, image_data)
    return filename


def _download_bytes(url: str) -> bytes | None:
    """Download one HTTP(S) URL and return its raw bytes."""
    request_obj = request.Request(url, method="GET")
    try:
        with request.urlopen(request_obj, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except Exception:
        return None


def _image_media_filename(remote_src: str) -> str:
    """Build a stable media filename for an image URL."""
    digest = hashlib.sha256(remote_src.encode("utf-8")).hexdigest()[:20]
    extension = _safe_media_extension(remote_src, default=".img")
    return f"notion_image_{digest}{extension}"


def _safe_media_extension(url: str, default: str) -> str:
    """Extract a conservative file extension from a URL path."""
    suffix = Path(urlsplit(url).path).suffix.lower()
    if not suffix:
        return default

    if len(suffix) > 10:
        return default

    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789.")
    if set(suffix) <= allowed and suffix.startswith("."):
        return suffix
    return default


def _render_mermaid_sources(source_html: str, media_dir: Path) -> str:
    """Render Mermaid placeholders to local SVG media with code-block fallback."""
    if "data-mermaid=" not in source_html:
        return source_html

    cache: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        encoded_source = match.group("encoded")
        caption_html = match.group("caption") or ""

        if encoded_source in cache:
            light_name, dark_name = cache[encoded_source]
            return _render_mermaid_code_block(light_name, dark_name, caption_html)

        mermaid_source = _decode_mermaid_source(encoded_source)
        if not mermaid_source:
            return match.group(0)

        light_svg_payload = _render_mermaid_svg(mermaid_source, theme="light")
        if light_svg_payload is None:
            return match.group(0)

        dark_svg_payload = _render_mermaid_svg(mermaid_source, theme="dark") or light_svg_payload
        light_filename = _mermaid_media_filename(mermaid_source, variant="light")
        dark_filename = _mermaid_media_filename(mermaid_source, variant="dark")
        _write_media_file(media_dir, light_filename, light_svg_payload)
        _write_media_file(media_dir, dark_filename, dark_svg_payload)
        cache[encoded_source] = (light_filename, dark_filename)
        return _render_mermaid_code_block(light_filename, dark_filename, caption_html)

    return _MERMAID_FIGURE_RE.sub(replace, source_html)


def _decode_mermaid_source(encoded_source: str) -> str:
    """Decode a URL-safe Base64 Mermaid source payload."""
    cleaned = encoded_source.strip()
    if not cleaned:
        return ""

    padding = "=" * (-len(cleaned) % 4)
    try:
        decoded = base64.urlsafe_b64decode((cleaned + padding).encode("ascii"))
    except Exception:
        return ""
    return decoded.decode("utf-8", errors="replace")


def _render_mermaid_svg(mermaid_source: str, *, theme: str = "light") -> bytes | None:
    """Render Mermaid source to SVG bytes using the Kroki HTTP API."""
    request_source = mermaid_source
    if theme == "dark":
        # Ask Mermaid to emit dark-mode-friendly colors.
        request_source = "%%{init: {'theme': 'dark'}}%%\n" + mermaid_source

    body = request_source.encode("utf-8")
    request_obj = request.Request(
        "https://kroki.io/mermaid/svg",
        data=body,
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "anki-notion-integration/1.0 (+https://github.com)",
        },
        method="POST",
    )
    try:
        with request.urlopen(request_obj, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except Exception:
        return None

    if b"<svg" not in payload:
        return None
    return payload


def _mermaid_media_filename(mermaid_source: str, variant: str = "light") -> str:
    """Build a stable SVG media filename for Mermaid source text."""
    digest = hashlib.sha256(mermaid_source.encode("utf-8")).hexdigest()[:20]
    safe_variant = "dark" if variant == "dark" else "light"
    return f"notion_mermaid_{safe_variant}_{digest}.svg"


def _render_mermaid_code_block(light_filename: str, dark_filename: str, caption_html: str) -> str:
    """Render Mermaid output as theme-aware SVG images inside a code-style block."""
    light_src_attr = html.escape(light_filename, quote=True)
    dark_src_attr = html.escape(dark_filename, quote=True)
    return (
        '<figure class="notion-mermaid">'
        '<pre class="code notion-mermaid-diagram"><code class="language-mermaid">'
        f'<img class="notion-mermaid-light" src="{light_src_attr}" alt="Mermaid diagram" loading="lazy"/>'
        f'<img class="notion-mermaid-dark" src="{dark_src_attr}" alt="Mermaid diagram" loading="lazy"/>'
        "</code></pre>"
        f"{caption_html}"
        "</figure>"
    )


def _write_media_file(media_dir: Path, filename: str, payload: bytes) -> None:
    """Write bytes to a media file only when content differs."""
    target_path = media_dir / filename
    if target_path.exists():
        try:
            current = target_path.read_bytes()
            if current == payload:
                return
        except Exception:
            pass
    target_path.write_bytes(payload)


def _is_http_url(value: str) -> bool:
    """Return whether a URL uses HTTP(S)."""
    scheme = urlsplit(value).scheme.lower()
    return scheme in {"http", "https"}


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


def _find_existing_note_for_payload(
    collection: Any,
    payload: ToggleCardPayload,
) -> tuple[int, Any, int] | None:
    """Find a compatible Noteck note by its durable Notion block identity."""
    find_notes = getattr(collection, "find_notes", None)
    if not callable(find_notes):
        return None

    # search anki cards to find card with "Notion Block ID" field equal to notion block id
    query         = f'"Notion Block ID:{payload.notion_block_id}"'
    candidate_ids = sorted({int(note_id) for note_id in find_notes(query)})

    compatible_candidates: list[tuple[int, Any]] = []
    for candidate_id in candidate_ids:
        note = _get_note(collection, candidate_id)
        if note is not None and _note_supports_payload(note, payload):
            compatible_candidates.append((candidate_id, note))

    if not compatible_candidates:
        return None

    # Preserve the oldest note when a previous failure already left duplicates;
    note_id, note = compatible_candidates[0]
    return note_id, note, len(compatible_candidates)


def _note_supports_payload(note: Any, payload: ToggleCardPayload) -> bool:
    """Return whether a note exposes every field required by a payload."""
    required_fields = (
        tuple(payload.fields)
        if payload.fields
        else ("Front", "Back", "Notion Block ID")
    )

    for field_name in required_fields:
        try:
            note[field_name]
        except Exception:
            return False
    return True


def _relink_existing_note(
    *,
    db:              Database,
    collection:      Any,
    page_id:         str,
    deck_id:         int,
    payload:         ToggleCardPayload,
    note_id:         int,
    note:            Any,
    candidate_count: int,
    stats:           SyncStats,
    warnings:        list[SyncWarning] | None,
    reason:          str,
) -> SyncStats:
    """Relink and refresh an existing Noteck note instead of duplicating it."""
    prepared_payload, stats = _prepare_payload_media_with_warnings(
        collection,
        payload,
        stats,
        warnings,
    )
    _apply_payload_to_note(note, prepared_payload)
    _update_note(collection, note)
    if payload.card_type == CLOZE:
        _remove_empty_cards_for_note(collection, note_id)
    _ensure_note_cards_in_deck(collection, note_id, deck_id)
    _upsert_card_mapping(db, prepared_payload, note_id, page_id)

    if candidate_count > 1:
        detail = (
            f"{candidate_count} compatible notes already existed; the oldest was "
            "relinked and the others were preserved."
        )
    else:
        detail = "An existing note with the same Notion Block ID was relinked."
    _LOG.warning(
        "Card mapping recovered. page_id=%s block_id=%s note_id=%s "
        "card_type=%s reason=%s candidates=%s",
        page_id,
        payload.notion_block_id,
        note_id,
        payload.card_type,
        reason,
        candidate_count,
    )
    stats = _add_warning(
        stats,
        warnings,
        code="existing_anki_note_relinked",
        message=f"{detail} No new note was created.",
        page_id=page_id,
        block_id=payload.notion_block_id,
    )
    return _replace_stats(stats, cards_updated=stats.cards_updated + 1)


def _delete_note(collection: Any, note_id: int) -> None:
    """Delete one note id across supported Anki APIs."""
    if hasattr(collection, "remove_notes"):
        collection.remove_notes([note_id])
        return
    if hasattr(collection, "removeNotes"):
        collection.removeNotes([note_id])
        return

    notes = getattr(collection, "notes", None)
    if isinstance(notes, dict):
        notes.pop(note_id, None)


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


def _remove_empty_cards_for_note(collection: Any, note_id: int) -> None:
    """Remove obsolete generated cards belonging to one updated cloze note."""
    get_empty_cards = getattr(collection, "get_empty_cards", None)
    remove_cards = getattr(collection, "remove_cards_and_orphaned_notes", None)
    if not callable(get_empty_cards) or not callable(remove_cards):
        return

    report = get_empty_cards()
    empty_card_ids = [
        int(card_id)
        for empty_note in getattr(report, "notes", ())
        if int(getattr(empty_note, "note_id", 0)) == note_id
        for card_id in getattr(empty_note, "card_ids", ())
    ]
    if empty_card_ids:
        remove_cards(empty_card_ids)


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
            SELECT notion_block_id, anki_note_id, card_type, content_hash, last_seen_notion_edit_time, excluded
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
            "card_type": str(row["card_type"]) if row["card_type"] is not None else BASIC,
            "content_hash": str(row["content_hash"]),
            "last_seen_notion_edit_time": _as_optional_string(row["last_seen_notion_edit_time"]),
            "excluded": bool(row["excluded"]),
        }
        for row in rows
    }


def _load_toggle_source_hashes(db: Database, page_id: str) -> dict[str, str]:
    """Return stored canonical Markdown hashes for one page's root toggles."""
    connection = db.connect()
    try:
        rows = connection.execute(
            """
            SELECT notion_block_id, source_hash
            FROM notion_toggle_snapshots
            WHERE notion_page_id = ?
            """,
            (page_id,),
        ).fetchall()
    finally:
        connection.close()
    return {
        str(row["notion_block_id"]): str(row["source_hash"])
        for row in rows
    }


def _load_page_content_hash(db: Database, page_id: str) -> str | None:
    """Return the last successful canonical full-page Markdown hash."""
    connection = db.connect()
    try:
        row = connection.execute(
            "SELECT content_hash FROM pages WHERE notion_page_id = ?",
            (page_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row["content_hash"] is None:
        return None
    value = str(row["content_hash"])
    return value or None


def _upsert_toggle_source_hash(
    db: Database,
    page_id: str,
    block_id: str,
    source_hash: str,
) -> None:
    """Persist one successfully handled root-toggle Markdown snapshot."""
    connection = db.connect()
    try:
        connection.execute(
            """
            INSERT INTO notion_toggle_snapshots (
                notion_block_id,
                notion_page_id,
                source_hash,
                updated_at
            )
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(notion_block_id) DO UPDATE SET
                notion_page_id = excluded.notion_page_id,
                source_hash = excluded.source_hash,
                updated_at = datetime('now')
            """,
            (block_id, page_id, source_hash),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _delete_stale_toggle_source_hashes(
    db: Database,
    page_id: str,
    current_block_ids: set[str],
) -> None:
    """Remove snapshots for blocks no longer present as root toggles."""
    connection = db.connect()
    try:
        if current_block_ids:
            placeholders = ", ".join("?" for _block_id in current_block_ids)
            connection.execute(
                f"""
                DELETE FROM notion_toggle_snapshots
                WHERE notion_page_id = ?
                  AND notion_block_id NOT IN ({placeholders})
                """,
                (page_id, *sorted(current_block_ids)),
            )
        else:
            connection.execute(
                "DELETE FROM notion_toggle_snapshots WHERE notion_page_id = ?",
                (page_id,),
            )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _set_page_content_hash(db: Database, page_id: str, content_hash: str) -> None:
    """Persist the canonical full-page Markdown hash after successful sync."""
    connection = db.connect()
    try:
        connection.execute(
            """
            UPDATE pages
            SET content_hash = ?
            WHERE notion_page_id = ?
            """,
            (content_hash, page_id),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


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
                payload.card_type or BASIC,
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


def _mark_card_synced(db: Database, block_id: str) -> None:
    """Record a successful local-only media update for one mapped card."""
    connection = db.connect()
    try:
        connection.execute(
            """
            UPDATE cards
            SET last_synced_at = datetime('now')
            WHERE notion_block_id = ?
            """,
            (block_id,),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _set_page_deck_reference(
    db: Database,
    page_id: str,
    deck_id: int,
    deck_name: str,
) -> None:
    """Persist canonical deck id/name for one page after deck resolution."""
    connection = db.connect()
    try:
        connection.execute(
            """
            UPDATE pages
            SET anki_deck_id = ?, anki_deck_name = ?
            WHERE notion_page_id = ?
            """,
            (deck_id, deck_name, page_id),
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
        "cards_detached": stats.cards_detached,
        "cards_warned": stats.cards_warned,
    }
    payload.update(changes)
    return SyncStats(**payload)


def _publish_progress(
    callback: SyncProgressCallback | None,
    label: str,
) -> None:
    """Emit sync progress label updates when a callback is configured."""
    if callback is None:
        return
    callback(label)


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
        errors=(),
        cancelled=True,
    )
