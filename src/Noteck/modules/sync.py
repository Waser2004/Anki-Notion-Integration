"""Sync orchestration for Notion → Anki flows."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import html
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
from .notion_client import NotionBlock, NotionClient
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
_CLOZE_REFRESH_REVISION = "2026-07-paragraph-color-markers-v2"
_GRAY_TOGGLE_CLOZE_ENABLED_SETTING_KEY = "_internal_gray_toggle_cloze_enabled"
_CLOZE_MARKER_COLORS_SETTING_KEY = "_internal_cloze_marker_colors"


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

    profile_name = _resolve_profile_name(mw)
    try:
        client = NotionClient.from_settings(db, profile_name=profile_name)
    except Exception as exc:
        return SyncResult(ok=False, message=f"Sync failed: {exc}", errors=(str(exc),))

    store = SettingsStore(db, profile_name=profile_name)
    card_type_override_store = CardTypeOverrideStore(db)
    global_default_card_type = normalize_default_selectable_card_type(store.get_value("default_card_type"))
    enable_cloze = bool(store.get_value("enable_cloze_parsing"))
    enable_gray_toggle_cloze = bool(store.get_value("enable_gray_toggle_cloze_parsing"))
    cloze_marker_colors = list(store.get_value("cloze_marker_colors"))
    force_cloze_marker_colors_refresh = (
        enable_cloze and _load_cloze_marker_colors(db) != cloze_marker_colors
    )
    force_gray_toggle_cloze_refresh = (
        enable_cloze
        and _load_gray_toggle_cloze_enabled(db) != enable_gray_toggle_cloze
    )
    force_cloze_refresh = (
        enable_cloze
        and _load_cloze_refresh_revision(db) != _CLOZE_REFRESH_REVISION
    )

    stats = SyncStats()
    errors: list[str] = []
    collection = _collection_from_mw(mw)
    if collection is None:
        message = "Anki collection is not available."
        return SyncResult(ok=False, message=f"Sync failed: {message}", errors=(message,))

    _publish_progress(
        callback=progress_callback,
        label="Preparing Notion sync...",
    )

    # Iterate over enabled Notion pages and sync their content
    for page in enabled_pages:
        if _is_sync_cancelled(should_cancel):
            return _build_cancelled_result(stats)

        _publish_progress(
            callback=progress_callback,
            label=f"{stats.pages_scanned}/{len(enabled_pages)} pages synced...",
        )
        stats = _replace_stats(stats, pages_scanned=stats.pages_scanned + 1)

        try:
            page_id = page.notion_page_id
            page_default_card_type = _effective_default_card_type(
                page.default_card_type,
                global_default=global_default_card_type,
            )
            page_card_type_overrides = card_type_override_store.get_card_type_overrides_for_page(page_id)
            # Check if the page has changed since last sync
            deck_id, resolved_deck_name = _resolve_page_deck(
                collection=collection,
                stored_deck_name=page.anki_deck_name,
                stored_deck_id=page.anki_deck_id,
            )
            if page.anki_deck_id != deck_id or page.anki_deck_name != resolved_deck_name:
                _set_page_deck_reference(
                    db=db,
                    page_id=page_id,
                    deck_id=deck_id,
                    deck_name=resolved_deck_name,
                )
            page_last_edited_time = client.get_page_last_edited_time(page_id)
            stored_page_edit_time = _load_page_last_seen_notion_edit_time(db, page_id)
            page_is_unchanged = (
                page_last_edited_time is not None
                and stored_page_edit_time is not None
                and page_last_edited_time == stored_page_edit_time
            )

            before_writes = stats.cards_created + stats.cards_updated
            page_errors: list[str] = []
            cancelled = False

            if page_is_unchanged:
                # Even when the page is unchanged in Notion, local Anki notes may be missing.
                # We only contact Notion for those missing notes so we can recreate them.
                stats, page_errors, cancelled = _repair_missing_notes_for_unchanged_page(
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
                    force_cloze_refresh=force_cloze_refresh,
                    force_gray_toggle_cloze_refresh=force_gray_toggle_cloze_refresh,
                    force_cloze_marker_colors_refresh=force_cloze_marker_colors_refresh,
                    should_cancel=should_cancel,
                )
            
            else:
                # Page changed: fetch only top-level blocks, then expand only the toggles that need work.
                stats, page_errors, cancelled = _sync_changed_page_fast(
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
                    should_cancel=should_cancel,
                )

            if cancelled:
                return _build_cancelled_result(stats)

            errors.extend(page_errors)

            # Persist the last seen Notion edit time only when the page processed cleanly and
            # the value actually changed (avoid unnecessary DB writes on skipped pages).
            if (
                not page_errors
                and page_last_edited_time
                and page_last_edited_time != stored_page_edit_time
            ):
                _set_page_last_seen_notion_edit_time(db, page_id, page_last_edited_time)

            # Only mark the page as synced when we actually wrote changes to Anki.
            after_writes = stats.cards_created + stats.cards_updated
            if after_writes > before_writes:
                _mark_page_synced(db, page_id)
            
            _publish_progress(
                callback=progress_callback,
                label=f"Synced page {stats.pages_scanned}/{len(enabled_pages)}.",
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

    if force_cloze_refresh:
        _set_cloze_refresh_revision(db, _CLOZE_REFRESH_REVISION)
    if enable_cloze:
        _set_gray_toggle_cloze_enabled(db, enable_gray_toggle_cloze)
        _set_cloze_marker_colors(db, cloze_marker_colors)

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


def _sync_changed_page_fast(
    db: Database,
    collection: Any,
    page_id: str,
    deck_id: int,
    client: NotionClient,
    stats: SyncStats,
    default_card_type: str,
    card_type_overrides: dict[str, str],
    enable_cloze: bool,
    enable_gray_toggle_cloze: bool,
    cloze_marker_colors: list[str],
    should_cancel: SyncCancelCheck | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Sync a page by expanding only toggles that are new/changed/missing locally."""
    existing_cards = _load_existing_cards_for_page(db, page_id)
    toggle_payloads: list[ToggleCardPayload] = []
    errors: list[str] = []

    # Shallow fetch: direct children only (no recursion).
    blocks = client.get_page_blocks_shallow(page_id)
    toggles = [block for block in blocks if block.block_type == "toggle"]
    cloze_parser = ClozeCardParser(cloze_marker_colors)

    for toggle in toggles:
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True

        # Count every root toggle as "seen", even when fast-skip decides no work is needed.
        stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)

        mapping = existing_cards.get(toggle.block_id)
        if mapping is not None and mapping["excluded"]:
            # Excluded cards must be skipped before expansion/parsing.
            stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
            continue

        is_advanced_cloze_toggle = (
            enable_cloze
            and cloze_parser.is_advanced_container(
                toggle,
                enable_gray_toggle_cloze=enable_gray_toggle_cloze,
            )
        )
        # Avoid an extra API call when Notion indicates there are no child blocks.
        children = client.get_block_children_recursive(toggle.block_id) if toggle.has_children else []
        expanded_toggle = _with_children(toggle, children)
        toggle_payloads.extend(
            parse_page_to_cards(
                page_id,
                [expanded_toggle],
                default_card_type=default_card_type,
                card_type_overrides=card_type_overrides,
                enable_cloze=is_advanced_cloze_toggle,
                enable_gray_toggle_cloze=enable_gray_toggle_cloze,
                cloze_marker_colors=cloze_marker_colors,
            )
        )

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
                stats = _replace_stats(stats, cards_skipped=stats.cards_skipped + 1)
                continue
            cloze_candidate_block_ids.add(block.block_id)
        cloze_payloads: list[ToggleCardPayload] = []
        if cloze_candidate_block_ids:
            cloze_payloads = [
                payload
                for payload in parse_page_to_cards(
                    page_id,
                    blocks,
                    default_card_type=default_card_type,
                    card_type_overrides=card_type_overrides,
                    enable_cloze=True,
                    enable_gray_toggle_cloze=enable_gray_toggle_cloze,
                    cloze_marker_colors=cloze_marker_colors,
                    include_block_ids=cloze_candidate_block_ids,
                )
                if payload.card_type == CLOZE
            ]
        if cloze_payloads:
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
                )
                errors.extend(payload_errors)
                if cancelled:
                    return stats, errors, True

    return stats, errors, False


def _repair_missing_notes_for_unchanged_page(
    db: Database,
    collection: Any,
    page_id: str,
    deck_id: int,
    client: NotionClient,
    stats: SyncStats,
    default_card_type: str,
    card_type_overrides: dict[str, str],
    enable_cloze: bool,
    enable_gray_toggle_cloze: bool,
    cloze_marker_colors: list[str],
    force_cloze_refresh: bool = False,
    force_gray_toggle_cloze_refresh: bool = False,
    force_cloze_marker_colors_refresh: bool = False,
    should_cancel: SyncCancelCheck | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Recreate local Anki notes that are missing even though the Notion page is unchanged."""
    existing_cards = _load_existing_cards_for_page(db, page_id)
    errors: list[str] = []

    # Collect only blocks that need local repair so we avoid a full Notion page fetch.
    blocks_needing_resync: list[str] = []

    def schedule_resync(block_id: str) -> None:
        """Queue one block for re-parse without duplicating work."""
        if block_id not in blocks_needing_resync:
            blocks_needing_resync.append(block_id)

    for block_id, mapping in existing_cards.items():
        if mapping["excluded"]:
            continue
        if force_gray_toggle_cloze_refresh:
            # The setting changes a block's card type, so re-parse every mapped block.
            schedule_resync(block_id)
            continue
        if force_cloze_marker_colors_refresh and normalize_card_type(mapping["card_type"], default=BASIC) == CLOZE:
            # Re-render existing cloze notes when a color becomes formatting or a marker.
            schedule_resync(block_id)
            continue
        note_id = mapping["anki_note_id"]
        if note_id is None:
            schedule_resync(block_id)
            continue
        if _get_note(collection, note_id) is None:
            schedule_resync(block_id)
            continue

        # Re-sync toggle notes when page default type changed without a Notion page edit.
        effective_card_type = _effective_card_type_for_block(
            block_id,
            default_card_type=default_card_type,
            card_type_overrides=card_type_overrides,
        )
        if _card_type_needs_default_conversion(mapping["card_type"], effective_card_type):
            schedule_resync(block_id)
            continue

        # Force one parser-refresh pass for existing cloze cards after parser upgrades.
        if force_cloze_refresh and normalize_card_type(mapping["card_type"], default=BASIC) == CLOZE:
            schedule_resync(block_id)

    if not blocks_needing_resync:
        return stats, errors, False

    try:
        blocks = client.get_page_content(page_id)
    except Exception as exc:
        return stats, [f"Page {page_id}: {exc}"], False
    payloads = parse_page_to_cards(
        page_id,
        blocks,
        default_card_type=default_card_type,
        card_type_overrides=card_type_overrides,
        enable_cloze=enable_cloze,
        enable_gray_toggle_cloze=enable_gray_toggle_cloze,
        cloze_marker_colors=cloze_marker_colors,
        include_block_ids=blocks_needing_resync,
    )
    payloads_by_block_id = {payload.notion_block_id: payload for payload in payloads}

    for block_id in blocks_needing_resync:
        if _is_sync_cancelled(should_cancel):
            return stats, errors, True
        stats = _replace_stats(stats, cards_seen=stats.cards_seen + 1)
        payload = payloads_by_block_id.get(block_id)
        if payload is None:
            errors.append(f"Block {block_id}: no payload could be generated during repair.")
            continue
        stats, payload_errors, cancelled = _sync_one_payload(
            db=db,
            collection=collection,
            page_id=page_id,
            deck_id=deck_id,
            payload=payload,
            stats=stats,
            should_cancel=should_cancel,
        )
        errors.extend(payload_errors)
        if cancelled:
            return stats, errors, True

    return stats, errors, False


def _load_cloze_refresh_revision(db: Database) -> str | None:
    """Return the last completed internal cloze-refresh revision, if any."""
    return _as_optional_string(db.get_setting(_CLOZE_REFRESH_REVISION_SETTING_KEY))


def _set_cloze_refresh_revision(db: Database, value: str) -> None:
    """Persist the latest completed internal cloze-refresh revision."""
    db.set_setting(_CLOZE_REFRESH_REVISION_SETTING_KEY, value)


def _load_gray_toggle_cloze_enabled(db: Database) -> bool:
    """Return the last gray-toggle cloze option, defaulting to disabled."""
    raw_value = db.get_setting(_GRAY_TOGGLE_CLOZE_ENABLED_SETTING_KEY)
    if raw_value is None:
        return False
    
    return raw_value == "1"


def _set_gray_toggle_cloze_enabled(db: Database, enabled: bool) -> None:
    """Persist the gray-toggle cloze option used by the last successful sync."""
    db.set_setting(_GRAY_TOGGLE_CLOZE_ENABLED_SETTING_KEY, "1" if enabled else "0")


def _load_cloze_marker_colors(db: Database) -> list[str] | None:
    """Return the marker-color selection used by the last successful sync."""
    raw_value = db.get_setting(_CLOZE_MARKER_COLORS_SETTING_KEY)
    # Existing installations predate this snapshot and behaved as if every color was enabled.
    return raw_value.split(",") if raw_value is not None else list(CLOZE_MARKER_COLORS)


def _set_cloze_marker_colors(db: Database, colors: list[str]) -> None:
    """Remember the marker-color selection for local parser-change detection."""
    db.set_setting(_CLOZE_MARKER_COLORS_SETTING_KEY, ",".join(colors))


def _card_type_needs_default_conversion(current_card_type: str, default_card_type: str) -> bool:
    """Return whether a selectable card type should be converted to the active page default."""
    normalized_current = normalize_card_type(current_card_type, default=BASIC)
    normalized_default = normalize_default_selectable_card_type(default_card_type)
    if normalized_current not in DEFAULT_SELECTABLE_CARD_TYPES:
        return False
    return normalized_current != normalized_default


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


def _sync_one_payload(
    db: Database,
    collection: Any,
    page_id: str,
    deck_id: int,
    payload: ToggleCardPayload,
    stats: SyncStats,
    should_cancel: SyncCancelCheck | None = None,
) -> tuple[SyncStats, list[str], bool]:
    """Sync a single card payload (small wrapper around the existing mapping logic)."""
    if _is_sync_cancelled(should_cancel):
        return stats, [], True

    existing_cards = _load_existing_cards_for_page(db, page_id)
    mapping = existing_cards.get(payload.notion_block_id)
    if mapping is not None and mapping["excluded"]:
        return _replace_stats(stats, cards_skipped=stats.cards_skipped + 1), [], False

    # Validate immediately before touching Anki so every create/update path is protected.
    if payload.card_type == CLOZE:
        validation = ClozeCardParser().validate(payload)
        if not validation.is_valid:
            message = "; ".join(validation.errors)
            return stats, [f"Block {payload.notion_block_id}: invalid cloze card: {message}"], False

    try:
        model_name = payload.model_name or MODEL_NAME_BASIC
        model = _model_by_name(collection, model_name)
        if model is None:
            raise SyncError(f"Anki note type '{model_name}' is not available.")

        if mapping is None:
            prepared_payload = _prepare_payload_media(collection, payload)
            note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, note_id, page_id)
            return _replace_stats(stats, cards_created=stats.cards_created + 1), [], False

        note_id = mapping["anki_note_id"]
        if note_id is None:
            prepared_payload = _prepare_payload_media(collection, payload)
            note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, note_id, page_id)
            return _replace_stats(stats, cards_created=stats.cards_created + 1), [], False

        note = _get_note(collection, note_id)
        if note is None:
            stats = _replace_stats(stats, cards_missing_note=stats.cards_missing_note + 1)
            prepared_payload = _prepare_payload_media(collection, payload)
            note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, note_id, page_id)
            return _replace_stats(stats, cards_created=stats.cards_created + 1), [], False

        if mapping["card_type"] != payload.card_type:
            _delete_note(collection, note_id)
            prepared_payload = _prepare_payload_media(collection, payload)
            recreated_note_id = _create_note(collection, model, deck_id, prepared_payload)
            _upsert_card_mapping(db, prepared_payload, recreated_note_id, page_id)
            return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False

        _ensure_note_cards_in_deck(collection, note_id, deck_id)
        if mapping["content_hash"] == payload.content_hash:
            if _note_back_needs_mermaid_theme_upgrade(note):
                prepared_payload = _prepare_payload_media(collection, payload)
                _apply_payload_to_note(note, prepared_payload)
                _update_note(collection, note)
                _upsert_card_mapping(db, prepared_payload, note_id, page_id)
                return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False
            # Backfill older notes that still contain sync-time media placeholders.
            if _note_back_contains_pending_media(note):
                note["Back"] = _prepare_back_html_media(collection, _safe_note_field(note, "Back"))
                _update_note(collection, note)
                _upsert_card_mapping(db, payload, note_id, page_id)
                return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False
            return _replace_stats(stats, cards_unchanged=stats.cards_unchanged + 1), [], False

        prepared_payload = _prepare_payload_media(collection, payload)
        _apply_payload_to_note(note, prepared_payload)
        _update_note(collection, note)
        if payload.card_type == CLOZE:
            _remove_empty_cards_for_note(collection, note_id)
        _upsert_card_mapping(db, prepared_payload, note_id, page_id)
        return _replace_stats(stats, cards_updated=stats.cards_updated + 1), [], False
    except Exception as exc:
        return stats, [f"Block {payload.notion_block_id}: {exc}"], False


def _with_children(block: NotionBlock, children: list[NotionBlock]) -> NotionBlock:
    """Return a copy of a block with a fully populated children tuple."""
    return NotionBlock(
        block_id=block.block_id,
        block_type=block.block_type,
        has_children=block.has_children,
        parent_id=block.parent_id,
        parent_type=block.parent_type,
        raw=block.raw,
        children=tuple(children),
    )


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


def _note_back_contains_pending_media(note: Any) -> bool:
    """Return whether a note back still contains remote/media placeholders to localize."""
    back_html = _safe_note_field(note, "Back")
    lowered = back_html.lower()
    return ('data-mermaid="' in lowered) or ("<img" in lowered and 'src="http' in lowered)


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
    if "Back" in rewritten_fields:
        rewritten_fields["Back"] = _prepare_back_html_media(collection, rewritten_fields["Back"])

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
    """Delete generated empty cards for one updated cloze note only."""
    get_empty_cards = getattr(collection, "get_empty_cards", None)
    remove_cards    = getattr(collection, "remove_cards_and_orphaned_notes", None)
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
        errors=("Canceled by user.",),
    )
