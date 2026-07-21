"""Public parser package API."""

from .parser import (
    CardParseResult,
    CardParseWarning,
    ImageOcclusionCandidate,
    ToggleCardPayload,
    collect_image_occlusion_candidates,
    extract_root_toggle_blocks,
    normalize_typed_answer,
    parse_page_to_cards,
)
from .renderer import render_blocks, render_rich_text

__all__ = [
    "CardParseResult",
    "CardParseWarning",
    "ImageOcclusionCandidate",
    "ToggleCardPayload",
    "collect_image_occlusion_candidates",
    "extract_root_toggle_blocks",
    "normalize_typed_answer",
    "parse_page_to_cards",
    "render_blocks",
    "render_rich_text",
]
