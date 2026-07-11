"""Helpers for reading and writing page-selection state in the local database."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Mapping

from .card_types import normalize_default_selectable_card_type
from .db import Database
from .notion_client import NotionPage, PageNode

PAGE_SELECTION_BEHAVIOR_MANUAL               = "manual"
PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS = "existing_descendants"
PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS  = "dynamic_descendants"
PAGE_SELECTION_BEHAVIORS = (
    PAGE_SELECTION_BEHAVIOR_MANUAL,
    PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS,
    PAGE_SELECTION_BEHAVIOR_DYNAMIC_DESCENDANTS,
)
DEFAULT_PAGE_SELECTION_BEHAVIOR = PAGE_SELECTION_BEHAVIOR_EXISTING_DESCENDANTS
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
        "selected automatically as long as the parent remains selected.",
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
