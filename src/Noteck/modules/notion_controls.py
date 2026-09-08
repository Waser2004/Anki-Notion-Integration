"""Interpret leading Notion controls without changing local card preferences."""

from dataclasses import dataclass, replace
import re

from .card_types import card_type_label

_MARKERS = re.compile(
    r"\s*(🍒|\[cherry-pick\]|🚫|\[exclude\]|➡️?|\[basic\]|↔️?|\[basic \+ reversed\]|"
    r"⌨️?|\[input\]|🧩|\[cloze\]|cloze\s*:|💡|\[extra\]|extra\s*:)\s*",
    re.IGNORECASE,
)
_TYPES = {
    "➡":                 "basic",
    "[basic]":            "basic",
    "↔":                  "basic_reversed",
    "[basic + reversed]": "basic_reversed",
    "⌨":                 "input",
    "[input]":            "input",
}


# use the same icons for text aliases and emoji prefixes
_MARKER_ICONS = {
    "[cherry-pick]":       "🍒",
    "[exclude]":           "🚫",
    "[basic]":             "➡️",
    "[basic + reversed]":  "↔️",
    "[input]":             "⌨️",
    "[cloze]":             "🧩",
    "[extra]":             "💡",
    "➡":                  "➡️",
    "↔":                   "↔️",
    "⌨":                  "⌨️",
}

_ICON_LABELS = {
    "🍒":                 "Cherry-pick",
    "🚫":                 "Excluded in Notion",
    "➡️":                 "Basic",
    "↔️":                 "Basic + Reversed",
    "⌨️":                 "Input",
    "🧩":                 "Advanced Cloze",
    "💡":                 "Extra content",
}


@dataclass(frozen=True)
class NotionControls:
    """Keep source controls independent of manual inclusion and type choices."""

    excluded:      bool       = False
    cherry_picked: bool       = False
    filtered:      bool       = False
    card_type:     str | None = None
    cloze:         bool       = False
    warning:       str        = ""
    prefix_length: int        = 0
    marker_icons:  str        = ""

    @property
    def marker_description(self):
        """Describe each source icon using its familiar marker name."""
        return "\n".join(f"{icon} {_ICON_LABELS.get(icon, icon)}" for icon in self.marker_icons.split())

    @property
    def locked(self):
        """Return whether Notion prevents local inclusion."""
        return self.excluded or self.filtered

    @property
    def explanation(self):
        """Explain the controlling marker and how to release it."""
        # include every source lock so compatible markers remain explainable
        reasons = []
        if self.excluded:
            reasons.append("Excluded in Notion. Remove 🚫 or [exclude] from the toggle to manage inclusion in Noteck.")
        if self.filtered:
            reasons.append("Not selected by cherry-pick. Add 🍒 or [cherry-pick] to this toggle, or remove all cherry-pick markers from the page.")
        if self.card_type:
            reasons.append(f"Card type controlled by Notion: {card_type_label(self.card_type)}. Remove the card-type marker to manage it in Noteck.")

        return " ".join(reasons) or self.warning


def parse_controls(block):
    """Read only the uninterrupted sequence of leading toggle controls."""
    from .parser.renderer import _block_rich_text, _rich_text_to_plain

    # stop at ordinary content so literal markers in questions remain visible
    title   = _rich_text_to_plain(_block_rich_text(block))
    markers = []
    end     = 0
    while match := _MARKERS.match(title, end):
        markers.append(match[1].lower().replace("\ufe0f", ""))
        end = match.end()

    # conflicting types fall back instead of silently selecting a winner
    types   = {_TYPES[marker] for marker in markers if marker in _TYPES}
    cloze   = any(marker == "🧩" or marker == "[cloze]" or marker.startswith("cloze") for marker in markers)
    warning = "Conflicting Notion card-type markers; using the Noteck/page/default card type." if len(types) > 1 else ""
    if cloze and types:
        labels  = ", ".join(card_type_label(value) for value in sorted(types))
        warning = ("Conflicting Notion card-type markers. " if len(types) > 1 else "")
        warning += f"Non-cloze card-type markers ({labels}) are not applicable to Cloze cards."

    # retain source markers for the cards page even when an override conflicts
    icons = []
    for marker in markers:
        alias = "[cloze]" if marker.startswith("cloze") else "[extra]" if marker.startswith("extra") else marker
        icon  = _MARKER_ICONS.get(alias, alias)
        if icon not in icons:
            icons.append(icon)

    return NotionControls(
        excluded      = bool({"🚫", "[exclude]"} & set(markers)),
        cherry_picked = bool({"🍒", "[cherry-pick]"} & set(markers)),
        card_type     = next(iter(types)) if len(types) == 1 and not cloze else None,
        cloze         = cloze,
        warning       = warning,
        prefix_length = end,
        marker_icons  = " ".join(icons),
    )


def page_controls(blocks, *, enable_gray_toggle_cloze=True):
    """Resolve page-wide cherry-pick eligibility from all root toggles."""
    # collect every root toggle before deciding page-wide eligibility
    blocks   = list(blocks)
    controls = {block.block_id: parse_controls(block) for block in blocks if block.block_type == "toggle"}
    active   = any(control.cherry_picked for control in controls.values())

    # gray cloze containers have the same precedence as explicit cloze markers
    for block in blocks:
        if block.block_id not in controls:
            continue

        control = controls[block.block_id]
        gray    = enable_gray_toggle_cloze and block.raw.get("toggle", {}).get("color") == "gray_background"
        if gray and control.card_type:
            control = replace(control, card_type=None, warning=f"The {card_type_label(control.card_type)} marker is not applicable to Cloze cards.")

        controls[block.block_id] = replace(control, filtered=active and not control.cherry_picked)
    
    return controls


def strip_controls(block, control):
    """Remove control text across rich-text runs while retaining formatting."""
    from .parser.renderer import _block_rich_text, _rich_text_to_plain

    # clone only the title payload and the runs intersecting its prefix
    remaining = control.prefix_length
    items     = []
    for original in _block_rich_text(block):
        visible = _rich_text_to_plain([original])
        if remaining and len(visible) <= remaining:
            remaining -= len(visible)
            continue
        item = dict(original)
        if remaining:
            if "text" in item:
                item["text"] = dict(item["text"], content=item["text"].get("content", "")[remaining:])
            if "plain_text" in item:
                item["plain_text"] = item["plain_text"][remaining:]
            remaining = 0
        items.append(item)

    # replace the title without touching source blocks or child content
    raw = dict(block.raw)
    raw[block.block_type] = dict(raw[block.block_type], rich_text=items)
    return replace(block, raw=raw)


def load_controls(db, page_id):
    """Read the last parsed Notion state for immediate offline UI rendering."""
    # close the read connection even when database access fails
    connection = db.connect()
    try:
        rows = connection.execute("SELECT * FROM notion_card_controls WHERE notion_page_id = ?", (page_id,)).fetchall()
        return {row["notion_block_id"]: NotionControls(
            excluded     = bool(row["excluded"]),
            filtered     = bool(row["filtered"]),
            card_type    = row["card_type"],
            warning      = row["warning"],
            marker_icons = row["marker_icons"],
        ) for row in rows}
    finally:
        connection.close()


def save_controls(db, page_id, controls):
    """Replace source state atomically and invalidate changed eligibility caches."""
    # compare source state without consulting or overwriting manual preferences
    previous   = load_controls(db, page_id)
    connection = db.connect()
    try:
        with connection:
            # an unselected sibling can become eligible without its source changing
            for block_id in previous.keys() | controls.keys():
                old = previous.get(block_id, NotionControls())
                new = controls.get(block_id, NotionControls())
                if (old.locked, old.card_type) != (new.locked, new.card_type):
                    connection.execute("DELETE FROM notion_toggle_snapshots WHERE notion_block_id = ?", (block_id,))

            # replace the complete page snapshot so removed markers cannot linger
            connection.execute("DELETE FROM notion_card_controls WHERE notion_page_id = ?", (page_id,))
            connection.executemany(
                "INSERT INTO notion_card_controls VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(block_id, page_id, int(c.excluded), int(c.filtered), c.card_type, c.warning, c.marker_icons) for block_id, c in controls.items()],
            )
    finally:
        connection.close()
