"""Card type constants and helpers used across parser, sync, and UI."""

from __future__ import annotations

from dataclasses import dataclass

BASIC = "basic"
BASIC_REVERSED = "basic_reversed"
INPUT = "input"
CLOZE = "cloze"

ALL_CARD_TYPES: tuple[str, ...] = (
    BASIC,
    BASIC_REVERSED,
    INPUT,
    CLOZE,
)

DEFAULT_SELECTABLE_CARD_TYPES: tuple[str, ...] = (
    BASIC,
    BASIC_REVERSED,
    INPUT,
)


@dataclass(frozen=True)
class CardTypeOption:
    """Describes one card type option for UI dropdowns."""
    value: str
    label: str
    abbreviation: str


CARD_TYPE_OPTIONS: tuple[CardTypeOption, ...] = (
    CardTypeOption(BASIC, "Basic", "Basic"),
    CardTypeOption(BASIC_REVERSED, "Basic and reversed", "Basic+Rev"),
    CardTypeOption(INPUT, "Input", "Input"),
    CardTypeOption(CLOZE, "Cloze", "Cloze"),
)


def normalize_card_type(value: str | None, *, default: str = BASIC) -> str:
    """Return a known card type or the provided default."""
    if not isinstance(value, str):
        return default
    cleaned = value.strip().lower()
    if cleaned in ALL_CARD_TYPES:
        return cleaned
    return default


def normalize_default_selectable_card_type(value: str | None) -> str:
    """Return a valid default-selectable card type."""
    normalized = normalize_card_type(value, default=BASIC)
    if normalized in DEFAULT_SELECTABLE_CARD_TYPES:
        return normalized
    return BASIC


def card_type_label(value: str, *, abbreviation: bool = False) -> str:
    """Return a stable user-facing label for a card type."""
    normalized = normalize_card_type(value)
    for option in CARD_TYPE_OPTIONS:
        if option.value == normalized:
            return option.abbreviation if abbreviation else option.label
    return "B" if abbreviation else "Basic"
