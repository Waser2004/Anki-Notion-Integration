"""Helpers for reading and writing page-selection state in the local database."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from .card_types import normalize_default_selectable_card_type
from .db import Database
from .notion_client import NotionPage, PageNode
from .notion_client import NotionClient

PAGE_SELECTION_BEHAVIOR_MANUAL               = "manual"
PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS = "existing_descendants"
PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS  = "dynamic_descendants"
PAGE_SELECTION_BEHAVIORS = (
    PAGE_SELECTION_BEHAVIOR_MANUAL,
    PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS,
    PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
)
DEFAULT_PAGE_SELECTION_BEHAVIOR = PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS
DYNAMIC_SELECTION_PARENT_IDS_SETTING = "page_selection_dynamic_parent_ids"
PAGE_SELECTION_BEHAVIOR_DETAILS = {
    PAGE_SELECTION_BEHAVIOR_MANUAL: (
        "Manual",
        "Checkboxes affect only the page you click. Parent pages and child pages stay independent. "
        "Use this when you want precise control over exactly which Notion pages sync.",
    ),
    PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS: (
        "Smart",
        "Selecting a parent with no already selected child pages also selects the child pages currently visible "
        "in the Pages tab. New child pages created in Notion later are not selected automatically.",
    ),
    PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS: (
        "Dynamic",
        "Selecting a parent keeps its whole subtree selected. Child pages discovered on later refreshes are "
        "selected automatically and cannot be unselected while the parent remains selected.",
    ),
}
PAGE_SELECTION_BEHAVIOR_TOOLTIPS = {
    PAGE_SELECTION_BEHAVIOR_MANUAL: "Only toggle the clicked page.",
    PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS: "Include currently visible children when selecting a new parent.",
    PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS: "Keep selected parent subtrees synced as new children appear.",
}


def page_selection_behavior_description(behavior: str) -> str:
    """Return reusable descriptive text for one page-selection behavior."""
    normalized_behavior = normalize_page_selection_behavior(behavior)
    _, description = PAGE_SELECTION_BEHAVIOR_DETAILS[normalized_behavior]
    return description


def page_selection_behavior_tooltip(behavior: str) -> str:
    """Return short hover text for one page-selection behavior."""
    normalized_behavior = normalize_page_selection_behavior(behavior)
    return PAGE_SELECTION_BEHAVIOR_TOOLTIPS[normalized_behavior]


@dataclass(frozen=True)
class StoredPage:
    """Represents one row from the `pages` table."""

    notion_page_id: str
    anki_deck_name: str
    anki_deck_id: int | None
    sync_enabled: bool
    last_synced_at: str | None
    parent_id: str | None
    parent_type: str | None
    default_card_type: str | None


@dataclass(frozen=True)
class PageRefreshResult:
    """Summarize one completed refresh of the locally cached Notion pages."""
    loaded_count: int
    ordered_child_ids_by_parent: Mapping[str, tuple[str, ...]]


PageRefreshProgressCallback = Callable[[int], None]
PageRefreshCancelCheck = Callable[[], bool]


class PageRefreshCancelled(RuntimeError):
    """Raised when a page refresh is canceled before it can be persisted."""


@dataclass(frozen=True)
class StartupPageRefreshStatus:
    """Expose startup page-discovery progress to the first Pages-tab instance."""

    generation: int
    running: bool
    loaded_count: int
    error_message: str | None = None
    ordered_child_ids_by_parent: Mapping[str, tuple[str, ...]] | None = None


_PAGE_REFRESH_LOCK          = threading.Lock() # lock to serialize page refreshes within the add-on process
_STARTUP_REFRESH_STATE_LOCK = threading.Lock() # lock to serialize access to the startup refresh state

_startup_refresh_status: StartupPageRefreshStatus | None = None
_startup_refresh_available = False
_LOG = logging.getLogger("noteck.pages")


def begin_startup_page_refresh() -> int:
    """Start a session-scoped refresh that the first Pages tab can observe."""
    global _startup_refresh_status, _startup_refresh_available
    with _STARTUP_REFRESH_STATE_LOCK:
        generation = (
            1
            if _startup_refresh_status is None
            else _startup_refresh_status.generation + 1
        )
        _startup_refresh_status = StartupPageRefreshStatus(
            generation   = generation,
            running      = True,
            loaded_count = 0,
        )
        _startup_refresh_available = True
        return generation


def claim_startup_page_refresh_status() -> StartupPageRefreshStatus | None:
    """Return startup refresh state once so only the first Pages tab consumes it."""
    global _startup_refresh_available
    with _STARTUP_REFRESH_STATE_LOCK:
        if not _startup_refresh_available:
            return None
        _startup_refresh_available = False
        return _startup_refresh_status


def get_startup_page_refresh_status(
    generation: int,
) -> StartupPageRefreshStatus | None:
    """Return current state for one claimed startup refresh generation."""
    with _STARTUP_REFRESH_STATE_LOCK:
        if (
            _startup_refresh_status is None
            or _startup_refresh_status.generation != generation
        ):
            return None
        return _startup_refresh_status


def _update_startup_page_refresh(generation: int, loaded_count: int) -> None:
    """Publish a new loaded-page count for an active startup refresh."""
    global _startup_refresh_status
    with _STARTUP_REFRESH_STATE_LOCK:
        if (
            _startup_refresh_status is None
            or _startup_refresh_status.generation != generation
        ):
            return
        _startup_refresh_status = StartupPageRefreshStatus(
            generation   = generation,
            running      = True,
            loaded_count = loaded_count,
        )


def finish_startup_page_refresh(
    generation: int,
    *,
    loaded_count: int,
    error_message: str | None = None,
    ordered_child_ids_by_parent: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Mark startup discovery complete while retaining state for the Pages tab."""
    global _startup_refresh_status
    with _STARTUP_REFRESH_STATE_LOCK:
        if (
            _startup_refresh_status is None
            or _startup_refresh_status.generation != generation
        ):
            return
        _startup_refresh_status = StartupPageRefreshStatus(
            generation    = generation,
            running       = False,
            loaded_count  = loaded_count,
            error_message = error_message,
            ordered_child_ids_by_parent = ordered_child_ids_by_parent,
        )


def parse_dynamic_selection_parent_ids(raw_value: str | None) -> set[str]:
    """Parse persisted roots whose descendants must remain selected."""
    if not raw_value:
        return set()

    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, list):
        return set()

    return {
        page_id
        for page_id in payload
        if isinstance(page_id, str) and page_id.strip()
    }


def load_dynamic_selection_parent_ids(db: Database) -> set[str]:
    """Return persisted roots whose future descendants should stay selected."""
    return parse_dynamic_selection_parent_ids(
        db.get_setting(DYNAMIC_SELECTION_PARENT_IDS_SETTING)
    )


def refresh_pages(
    db: Database,
    *,
    profile_name:       str | None                         = None,
    progress_callback:  PageRefreshProgressCallback | None = None,
    should_cancel:      PageRefreshCancelCheck | None      = None,
    client:             NotionClient | None                = None,
    startup_generation: int | None                         = None,
) -> PageRefreshResult:
    """Refresh cached pages and apply dynamic descendant selection after fetching.

    Fetching is serialized within the add-on process so a startup refresh and a
    user-triggered sync cannot write competing snapshots to the pages table.
    """
    if progress_callback is not None:
        progress_callback(0)
    if startup_generation is not None:
        _update_startup_page_refresh(startup_generation, 0)

    with _PAGE_REFRESH_LOCK:
        pages_by_id: dict[str, NotionPage] = {}
        ordered_child_ids_by_parent: dict[str, tuple[str, ...]] = {}
        try:
            notion_client = client or NotionClient.from_settings(db, profile_name=profile_name)

            # Fetch pages from Notion and build a page-id indexed view.
            for page in notion_client.iter_pages():
                if should_cancel is not None and should_cancel():
                    raise PageRefreshCancelled("Page refresh was canceled.")

                pages_by_id[page.page_id] = page
                if progress_callback is not None:
                    progress_callback(len(pages_by_id))
                if startup_generation is not None:
                    _update_startup_page_refresh(startup_generation, len(pages_by_id))

            # Preserve Notion's sibling block order for the Pages tab.
            build_order_map = getattr(notion_client, "build_child_page_order_map", None)
            if startup_generation is not None and callable(build_order_map):
                if should_cancel is not None and should_cancel():
                    raise PageRefreshCancelled("Page refresh was canceled.")
                try:
                    if should_cancel is None:
                        ordered_child_ids_by_parent = dict(build_order_map(pages_by_id))
                    else:
                        ordered_child_ids_by_parent = dict(
                            build_order_map(
                                pages_by_id,
                                should_cancel=should_cancel,
                            )
                        )
                except Exception:
                    _LOG.exception("Notion child-page ordering could not be refreshed.")
                if should_cancel is not None and should_cancel():
                    raise PageRefreshCancelled("Page refresh was canceled.")

            if should_cancel is not None and should_cancel():
                raise PageRefreshCancelled("Page refresh was canceled.")

            # Read selection state and write the snapshot in one short database
            # transaction after network work, preserving concurrent UI changes.
            deck_names = build_deck_names_from_pages(pages_by_id)
            PagesStore(db).replace_page_snapshot(
                deck_names,
                pages_by_id=pages_by_id,
            )

        except Exception as exc:
            if startup_generation is not None:
                finish_startup_page_refresh(
                    startup_generation,
                    loaded_count=len(pages_by_id),
                    error_message=str(exc),
                )
            raise

        if startup_generation is not None:
            finish_startup_page_refresh(
                startup_generation,
                loaded_count=len(pages_by_id),
                ordered_child_ids_by_parent=ordered_child_ids_by_parent,
            )

        return PageRefreshResult(
            loaded_count=len(pages_by_id),
            ordered_child_ids_by_parent=ordered_child_ids_by_parent,
        )


def trigger_startup_page_refresh(
    mw: Any,
    db_path: str | Path,
    *,
    on_done: Callable[[], None] | None = None,
) -> bool:
    """Start a quiet background page refresh when an Anki profile opens."""
    taskman           = getattr(mw, "taskman", None)
    run_in_background = getattr(taskman, "run_in_background", None)
    if not callable(run_in_background):
        return False

    startup_generation = begin_startup_page_refresh()

    def work() -> PageRefreshResult:
        db = Database(db_path)
        return refresh_pages(
            db,
            profile_name       = _resolve_profile_name(mw),
            startup_generation = startup_generation,
        )

    def done(future: Any) -> None:
        # Consume background errors here; opening Anki must remain usable when
        # Notion is offline or its credentials have not been configured yet.
        try:
            future.result()
        except Exception:
            _LOG.exception("The startup Notion page refresh failed.")
        if on_done is not None:
            on_done()

    run_in_background(work, done, uses_collection=False)
    return True


def _resolve_profile_name(mw: Any) -> str | None:
    """Resolve the active Anki profile name without importing UI modules."""
    pm = getattr(mw, "pm", None)
    if pm is None:
        return None
    name_attr = getattr(pm, "name", None)
    if callable(name_attr):
        try:
            return str(name_attr())
        except Exception:
            return None
    return name_attr if isinstance(name_attr, str) else None


def flatten_page_tree(roots: list[PageNode]) -> dict[str, PageNode]:
    """Return a page-id indexed view of a page tree."""
    pages_by_id: dict[str, PageNode] = {}

    def visit(node: PageNode) -> None:
        if node.page.page_id in pages_by_id:
            return
        
        pages_by_id[node.page.page_id] = node
        for child in node.children:
            visit(child)

    for root in roots:
        visit(root)

    return pages_by_id


def build_children_map(roots: list[PageNode]) -> dict[str, tuple[str, ...]]:
    """Return a map from page id to its direct child page ids."""
    children_map: dict[str, tuple[str, ...]] = {}

    def visit(node: PageNode) -> None:
        children_map[node.page.page_id] = tuple(child.page.page_id for child in node.children)
        for child in node.children:
            visit(child)

    for root in roots:
        visit(root)

    return children_map


def build_children_map_from_pages(pages_by_id: Mapping[str, NotionPage]) -> dict[str, tuple[str, ...]]:
    """Return a children map for known pages from a page-id indexed collection."""
    children_by_parent: dict[str, list[str]] = {
        page_id: []
        for page_id in pages_by_id
    }

    for page_id, page in pages_by_id.items():
        parent_id = page.parent_id if page.parent_type == "page_id" else None
        if parent_id and parent_id in children_by_parent:
            children_by_parent[parent_id].append(page_id)
    
    return {
        page_id: tuple(children)
        for page_id, children in children_by_parent.items()
    }


def get_descendant_ids(page_id: str, children_map: Mapping[str, tuple[str, ...]]) -> set[str]:
    """Return all descendants for a page id, excluding the page itself."""
    descendants: set[str] = set()
    queue = list(children_map.get(page_id, ()))

    while queue:
        current = queue.pop()
        if current in descendants:
            continue
        descendants.add(current)
        queue.extend(children_map.get(current, ()))

    return descendants


def get_dynamic_locked_descendant_ids(
    selected_ids: set[str],
    dynamic_parent_ids: set[str],
    children_map: Mapping[str, tuple[str, ...]],
) -> set[str]:
    """Return descendants forced selected by active Dynamic-mode roots."""
    locked_ids: set[str] = set()
    for parent_id in dynamic_parent_ids:
        if parent_id not in selected_ids:
            continue
        locked_ids.update(get_descendant_ids(parent_id, children_map))
    return locked_ids


def normalize_page_selection_behavior(value: object) -> str:
    """Return a supported page-selection behavior, falling back to the default."""
    normalized = str(value or "").strip()
    if normalized in PAGE_SELECTION_BEHAVIORS:
        return normalized
    return DEFAULT_PAGE_SELECTION_BEHAVIOR


def apply_selection_rule(
    page_id: str,
    checked: bool,
    selected_ids: set[str],
    children_map: Mapping[str, tuple[str, ...]],
    behavior: str = DEFAULT_PAGE_SELECTION_BEHAVIOR,
    dynamic_parent_ids: set[str] | None = None,
) -> set[str]:
    """Apply the configured page-selection behavior and return selected page ids."""
    updated = set(selected_ids)
    normalized_behavior = normalize_page_selection_behavior(behavior)

    # Manual mode intentionally changes only the row the user toggled.
    if normalized_behavior == PAGE_SELECTION_BEHAVIOR_MANUAL:
        if checked:
            updated.add(page_id)
        else:
            updated.discard(page_id)
        return updated

    # Smart mode preserves the historical one-time subtree selection behavior.
    if normalized_behavior == PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS:
        # Deselecting a page only deselects that page.
        if not checked:
            updated.discard(page_id)
            return updated

        # Selecting a page selects it and all its descendants, if all descendants are not already selected.
        updated.add(page_id)
        descendants = get_descendant_ids(page_id, children_map)
        has_selected_descendant = any(descendant in selected_ids for descendant in descendants)
        if not has_selected_descendant:
            updated.update(descendants)

        return updated
    
    # Dynamic mode treats a checked parent as an ongoing subtree subscription.
    if normalized_behavior == PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS:
        locked_descendant_ids = get_dynamic_locked_descendant_ids(
            selected_ids,
            dynamic_parent_ids or set(),
            children_map,
        )
        if not checked and page_id in locked_descendant_ids:
            return updated

        # Deselecting a page deselects it and all its descendants.
        if not checked:
            updated.discard(page_id)
            descendants = get_descendant_ids(page_id, children_map)
            updated.difference_update(descendants)
            return updated

        # Selecting a page selects it and all its descendants.
        updated.add(page_id)
        descendants = get_descendant_ids(page_id, children_map)
        updated.update(descendants)

        return updated

    return updated


def apply_default_card_type_rule(
    page_id: str,
    card_type: str | None,
    page_default_card_types: Mapping[str, str | None],
    children_map: Mapping[str, tuple[str, ...]],
) -> dict[str, str | None]:
    """Apply one page card-type change to the page and conditionally to descendants.

    Descendants are updated only when all descendants currently use the inherited
    default (`None`). This prevents overriding explicit child page overrides.
    """
    normalized = None if card_type is None else normalize_default_selectable_card_type(card_type)
    updated = dict(page_default_card_types)
    descendant_ids = get_descendant_ids(page_id, children_map)

    # Always update the selected page.
    updated[page_id] = normalized

    # Cascade to descendants only while no explicit descendant override exists.
    can_cascade_to_descendants = all(
        page_default_card_types.get(descendant_id) is None
        for descendant_id in descendant_ids
    )
    if can_cascade_to_descendants:
        for descendant_id in descendant_ids:
            updated[descendant_id] = normalized

    return updated


def _normalize_deck_segment(title: str) -> str:
    """Normalize one deck-name segment for predictable deck paths."""
    cleaned = " ".join(str(title).split()).strip()
    if not cleaned:
        return "Untitled"

    return cleaned.replace("::", "∷")


def build_deck_names(roots: list[PageNode], prefix: str = "Notion") -> dict[str, str]:
    """Build deck names using the `Notion::<Parent>::<Child>` convention."""
    deck_names: dict[str, str] = {}

    def visit(node: PageNode, path: tuple[str, ...]) -> None:
        current_path = path + (_normalize_deck_segment(node.page.title),)
        deck_names[node.page.page_id] = "::".join((prefix, *current_path))
        for child in node.children:
            visit(child, current_path)

    for root in roots:
        visit(root, ())

    return deck_names


def build_deck_names_from_pages(
    pages_by_id: Mapping[str, NotionPage],
    prefix: str = "Notion",
) -> dict[str, str]:
    """Build deck names for pages that may arrive incrementally and out of order."""
    deck_names: dict[str, str] = {}
    children_map = build_children_map_from_pages(pages_by_id)
    roots: list[str] = []

    for page_id, page in pages_by_id.items():
        parent_id = page.parent_id if page.parent_type == "page_id" else None
        if not parent_id or parent_id not in pages_by_id:
            roots.append(page_id)

    def visit(page_id: str, path: tuple[str, ...], seen: set[str]) -> None:
        if page_id in seen:
            return
        page = pages_by_id.get(page_id)
        if page is None:
            return
        
        current_path = path + (_normalize_deck_segment(page.title),)
        deck_names[page_id] = "::".join((prefix, *current_path))
        next_seen = set(seen)
        next_seen.add(page_id)
        
        for child_id in children_map.get(page_id, ()):
            visit(child_id, current_path, next_seen)

    for root_id in roots:
        visit(root_id, (), set())

    for page_id in pages_by_id:
        if page_id not in deck_names:
            visit(page_id, (), set())

    return deck_names


class PagesStore:
    """Persistence helper for the `pages` table used by the Pages tab."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_pages(self) -> dict[str, StoredPage]:
        """Return all stored page rows indexed by notion page id."""
        connection = self._db.connect()
        try:
            rows = connection.execute(
                """
                SELECT
                    notion_page_id,
                    anki_deck_name,
                    anki_deck_id,
                    sync_enabled,
                    last_synced_at,
                    parent_id,
                    parent_type,
                    default_card_type
                FROM pages
                """
            ).fetchall()
        finally:
            connection.close()

        return {
            str(row["notion_page_id"]): StoredPage(
                notion_page_id=str(row["notion_page_id"]),
                anki_deck_name=str(row["anki_deck_name"]),
                anki_deck_id=int(row["anki_deck_id"]) if row["anki_deck_id"] is not None else None,
                sync_enabled=bool(row["sync_enabled"]),
                last_synced_at=row["last_synced_at"],
                parent_id=row["parent_id"],
                parent_type=row["parent_type"],
                default_card_type=row["default_card_type"],
            )
            for row in rows
        }

    def get_selected_page_ids(self) -> set[str]:
        """Return notion page ids that are currently enabled for sync."""
        return {
            page_id
            for page_id, page in self.get_pages().items()
            if page.sync_enabled
        }

    def upsert_page_selection(
        self,
        deck_names_by_page_id: Mapping[str, str],
        selected_page_ids: set[str],
        pages_by_id: Mapping[str, NotionPage] | None = None,
    ) -> None:
        """Upsert page rows for all known pages and persist `sync_enabled` flags."""
        connection = self._db.connect()
        try:
            cursor = connection.cursor()
            for page_id, deck_name in deck_names_by_page_id.items():
                page = pages_by_id.get(page_id) if pages_by_id is not None else None
                parent_id = page.parent_id if page is not None else None
                parent_type = page.parent_type if page is not None else None
                cursor.execute(
                    """
                    INSERT INTO pages (
                        notion_page_id,
                        anki_deck_name,
                        sync_enabled,
                        parent_id,
                        parent_type,
                        default_card_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(notion_page_id) DO UPDATE SET
                        anki_deck_name = excluded.anki_deck_name,
                        sync_enabled = excluded.sync_enabled,
                        parent_id = excluded.parent_id,
                        parent_type = excluded.parent_type
                    """,
                    (
                        page_id,
                        deck_name,
                        1 if page_id in selected_page_ids else 0,
                        parent_id,
                        parent_type,
                        None,
                    ),
                )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_page_snapshot(
        self,
        deck_names_by_page_id: Mapping[str, str],
        *,
        pages_by_id: Mapping[str, NotionPage],
    ) -> None:
        """Atomically replace page metadata using the latest selection state.

        Network discovery happens before this transaction starts. Taking the
        write lock before reading selections ensures a Pages-tab change either
        precedes this snapshot and is included or follows it and wins afterward.
        """
        connection = self._db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            selected_page_ids = {
                str(row["notion_page_id"])
                for row in connection.execute(
                    "SELECT notion_page_id FROM pages WHERE sync_enabled = 1"
                ).fetchall()
                if str(row["notion_page_id"]) in pages_by_id
            }

            behavior_row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("page_selection_behavior",),
            ).fetchone()
            behavior = normalize_page_selection_behavior(
                None if behavior_row is None else behavior_row["value"]
            )
            if behavior == PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS:
                roots_row = connection.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    (DYNAMIC_SELECTION_PARENT_IDS_SETTING,),
                ).fetchone()
                dynamic_parent_ids = parse_dynamic_selection_parent_ids(
                    None if roots_row is None else roots_row["value"]
                )
                selected_page_ids.update(
                    get_dynamic_locked_descendant_ids(
                        selected_page_ids,
                        dynamic_parent_ids,
                        build_children_map_from_pages(pages_by_id),
                    )
                )

            cursor = connection.cursor()
            for page_id, deck_name in deck_names_by_page_id.items():
                page = pages_by_id.get(page_id)
                parent_id = page.parent_id if page is not None else None
                parent_type = page.parent_type if page is not None else None
                cursor.execute(
                    """
                    INSERT INTO pages (
                        notion_page_id,
                        anki_deck_name,
                        sync_enabled,
                        parent_id,
                        parent_type,
                        default_card_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(notion_page_id) DO UPDATE SET
                        anki_deck_name = excluded.anki_deck_name,
                        sync_enabled = excluded.sync_enabled,
                        parent_id = excluded.parent_id,
                        parent_type = excluded.parent_type
                    """,
                    (
                        page_id,
                        deck_name,
                        1 if page_id in selected_page_ids else 0,
                        parent_id,
                        parent_type,
                        None,
                    ),
                )

            if not pages_by_id:
                cursor.execute("DELETE FROM pages")
            else:
                placeholders = ", ".join("?" for _ in pages_by_id)
                cursor.execute(
                    f"DELETE FROM pages WHERE notion_page_id NOT IN ({placeholders})",
                    tuple(pages_by_id),
                )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_page_default_card_type(self, page_id: str, card_type: str | None) -> None:
        """Persist an optional per-page default card type override."""
        normalized = None if card_type is None else normalize_default_selectable_card_type(card_type)

        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET default_card_type = ?
                WHERE notion_page_id = ?
                """,
                (normalized, page_id),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_page_default_card_types(self, page_ids: set[str], card_type: str | None) -> None:
        """Persist one default card type override for multiple pages."""
        if not page_ids:
            return

        normalized = None if card_type is None else normalize_default_selectable_card_type(card_type)
        placeholders = ", ".join("?" for _ in page_ids)

        connection = self._db.connect()
        try:
            connection.execute(
                f"""
                UPDATE pages
                SET default_card_type = ?
                WHERE notion_page_id IN ({placeholders})
                """,
                (normalized, *tuple(page_ids)),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_page_sync_enabled(self, page_id: str, enabled: bool) -> None:
        """Persist sync-enabled state for one page id."""
        self.set_pages_sync_enabled({page_id}, enabled=enabled)

    def set_pages_sync_enabled(self, page_ids: set[str], *, enabled: bool) -> None:
        """Persist sync-enabled state for multiple page ids."""
        if not page_ids:
            return

        placeholders = ", ".join("?" for _ in page_ids)
        connection = self._db.connect()
        try:
            connection.execute(
                f"""
                UPDATE pages
                SET sync_enabled = ?
                WHERE notion_page_id IN ({placeholders})
                """,
                (1 if enabled else 0, *tuple(page_ids)),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_all_pages_sync_enabled(self, enabled: bool) -> None:
        """Persist one sync-enabled value for every page row."""
        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET sync_enabled = ?
                """,
                (1 if enabled else 0,),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reset_all_page_default_card_types(self) -> None:
        """Reset all page default card types back to inherited global default."""
        connection = self._db.connect()
        try:
            connection.execute(
                """
                UPDATE pages
                SET default_card_type = NULL
                """
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_pages_not_in(self, page_ids: set[str]) -> None:
        """Delete page rows whose ids are not in `page_ids`."""
        connection = self._db.connect()
        try:
            if not page_ids:
                connection.execute("DELETE FROM pages")
            else:
                placeholders = ", ".join("?" for _ in page_ids)
                connection.execute(
                    f"DELETE FROM pages WHERE notion_page_id NOT IN ({placeholders})",
                    tuple(page_ids),
                )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()
