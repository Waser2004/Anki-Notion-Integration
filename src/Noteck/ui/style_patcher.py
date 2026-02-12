"""Style patching utilities for consistent UI rendering across themes."""

from __future__ import annotations

import re

from aqt.qt import QApplication

_STYLE_PATCH_MARKER = "/* anki-notion-addon:tree-indicator-mirror */"
_CHECKBOX_INDICATOR = "QCheckBox::indicator"
_TREE_VIEW_INDICATOR = "QTreeView::indicator"
_TREE_WIDGET_INDICATOR = "QTreeWidget::indicator"
_RULE_PATTERN = re.compile(r"(?s)([^{}]+)\{([^{}]*)\}")


def _split_selectors(selector_text: str) -> list[str]:
    """Split a comma-separated selector list into normalized selector strings."""
    return [part.strip() for part in selector_text.split(",") if part.strip()]


def mirror_checkbox_indicator_to_tree_indicators() -> None:
    """Mirror checkbox indicator styles into tree indicators for consistent visuals."""
    app = QApplication.instance()
    if app is None:
        return

    qss = app.styleSheet()
    if _STYLE_PATCH_MARKER in qss:
        return

    new_rules: list[str] = []
    for match in _RULE_PATTERN.finditer(qss):
        selector_text = match.group(1).strip()
        body = match.group(2)

        checkbox_selectors = [
            selector
            for selector in _split_selectors(selector_text)
            if _CHECKBOX_INDICATOR in selector
        ]
        if not checkbox_selectors:
            continue

        tree_selectors: list[str] = []
        for selector in checkbox_selectors:
            tree_selectors.append(selector.replace(_CHECKBOX_INDICATOR, _TREE_VIEW_INDICATOR))
            tree_selectors.append(selector.replace(_CHECKBOX_INDICATOR, _TREE_WIDGET_INDICATOR))

        new_rules.append(f"{', '.join(tree_selectors)} {{{body}}}")

    if not new_rules:
        return

    patched_qss = qss + "\n\n" + _STYLE_PATCH_MARKER + "\n" + "\n".join(new_rules) + "\n"
    app.setStyleSheet(patched_qss)
