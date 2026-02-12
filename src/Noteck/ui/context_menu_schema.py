"""Schema loader for JSON-defined context menus used by Pages and Cards tabs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


class ContextMenuSchemaError(RuntimeError):
    """Raised when context menu schema JSON is missing or invalid."""


@dataclass(frozen=True)
class ContextMenuEntry:
    """One context-menu entry definition."""

    type: str
    key: str
    label: str
    target: str


@dataclass(frozen=True)
class ContextMenuGroup:
    """Context-menu entry groups for item/background click locations."""

    on_item: tuple[ContextMenuEntry, ...]
    on_background: tuple[ContextMenuEntry, ...]


class ContextMenuSchema:
    """Parsed context-menu schema for Pages and Cards tabs."""

    def __init__(self, pages: ContextMenuGroup, cards: ContextMenuGroup) -> None:
        self._pages = pages
        self._cards = cards

    @property
    def pages(self) -> ContextMenuGroup:
        """Return pages-tab menu groups."""
        return self._pages

    @property
    def cards(self) -> ContextMenuGroup:
        """Return cards-tab menu groups."""
        return self._cards


_DEFAULT_CONTEXT_MENUS_PATH = Path(__file__).resolve().parents[1] / "docs" / "context_menus.json"
_SUPPORTED_ENTRY_TYPES = {"action", "card_type_select"}
_SUPPORTED_TARGETS = {"page", "page_and_children", "all_pages", "card", "all_toggles"}


def load_context_menu_schema(path: Path | None = None) -> ContextMenuSchema:
    """Load and validate context-menu schema from JSON."""
    schema_path = path or _DEFAULT_CONTEXT_MENUS_PATH

    if not schema_path.exists():
        raise ContextMenuSchemaError(f"Context menu schema not found: {schema_path}")

    with schema_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ContextMenuSchemaError("Context menu schema root must be an object.")

    pages_group = _parse_group(payload, "pages")
    cards_group = _parse_group(payload, "cards")
    return ContextMenuSchema(pages=pages_group, cards=cards_group)


def _parse_group(payload: Mapping[str, Any], key: str) -> ContextMenuGroup:
    """Parse one top-level group (pages/cards) from schema payload."""
    group_payload = payload.get(key)
    if not isinstance(group_payload, dict):
        raise ContextMenuSchemaError(f"Context menu schema requires object key '{key}'.")

    on_item = _parse_entry_list(group_payload, group_name=key, section_name="on_item")
    on_background = _parse_entry_list(group_payload, group_name=key, section_name="on_background")
    return ContextMenuGroup(on_item=on_item, on_background=on_background)


def _parse_entry_list(
    group_payload: Mapping[str, Any],
    *,
    group_name: str,
    section_name: str,
) -> tuple[ContextMenuEntry, ...]:
    """Parse and validate one entry list under a group section."""
    entries_payload = group_payload.get(section_name)
    if not isinstance(entries_payload, list):
        raise ContextMenuSchemaError(
            f"Context menu schema key '{group_name}.{section_name}' must be a list."
        )

    entries: list[ContextMenuEntry] = []
    seen_keys: set[str] = set()
    for index, entry_payload in enumerate(entries_payload):
        if not isinstance(entry_payload, dict):
            raise ContextMenuSchemaError(
                f"Entry '{group_name}.{section_name}[{index}]' must be an object."
            )

        entry_type = entry_payload.get("type")
        entry_key = entry_payload.get("key")
        entry_label = entry_payload.get("label")
        entry_target = entry_payload.get("target")

        if not isinstance(entry_type, str) or entry_type not in _SUPPORTED_ENTRY_TYPES:
            raise ContextMenuSchemaError(
                f"Unsupported type for '{group_name}.{section_name}[{index}]': {entry_type}"
            )
        if not isinstance(entry_key, str) or not entry_key:
            raise ContextMenuSchemaError(
                f"Entry '{group_name}.{section_name}[{index}]' requires string key."
            )
        if entry_key in seen_keys:
            raise ContextMenuSchemaError(
                f"Duplicate key '{entry_key}' in '{group_name}.{section_name}'."
            )
        if not isinstance(entry_label, str) or not entry_label:
            raise ContextMenuSchemaError(
                f"Entry '{group_name}.{section_name}[{index}]' requires string label."
            )
        if not isinstance(entry_target, str) or entry_target not in _SUPPORTED_TARGETS:
            raise ContextMenuSchemaError(
                f"Unsupported target for '{group_name}.{section_name}[{index}]': {entry_target}"
            )

        seen_keys.add(entry_key)
        entries.append(
            ContextMenuEntry(
                type=entry_type,
                key=entry_key,
                label=entry_label,
                target=entry_target,
            )
        )

    return tuple(entries)
