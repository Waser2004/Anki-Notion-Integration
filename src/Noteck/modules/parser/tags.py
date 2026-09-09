"""Recognize paragraph metadata and preserve Anki tag hierarchies."""

import re

from .renderer import _block_rich_text, _rich_text_to_plain


_TAGS_PREFIX = re.compile(r"^\s*(?:🏷\ufe0f?|\[tags\]|tags:)\s*", re.IGNORECASE)


def parse_tags(block):
    """Return None for ordinary content and a tag tuple for recognized metadata."""
    # only standard paragraphs can provide tag metadata
    if block.block_type != "paragraph":
        return None
    
    text  = _rich_text_to_plain(_block_rich_text(block))
    match = _TAGS_PREFIX.match(text)
    if match is None:
        return None

    # reject malformed hierarchies and control characters as a whole
    value = text[match.end():]
    tags  = value.split()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ()
    if any(not part for tag in tags for part in tag.split("::")):
        return ()

    # preserve spelling while deduplicating case-insensitive names
    unique = {}
    for tag in tags:
        unique.setdefault(tag.casefold(), tag)
    
    return tuple(unique.values())


def split_toggle_tags(children):
    """Remove direct Tags paragraphs while leaving nested content untouched."""
    blocks = []
    tags   = []
    for child in children:
        parsed = parse_tags(child)
        if parsed is None:
            blocks.append(child)
        else:
            tags.extend(parsed)
    
    return blocks, tuple(dict.fromkeys(tags))
