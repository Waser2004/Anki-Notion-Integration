"""Runtime dependency installation helpers for Noteck."""

from __future__ import annotations

import importlib.util
from typing import Any

try:
    from aqt.qt import QMessageBox  # type: ignore
except ModuleNotFoundError:
    QMessageBox = None


def install_keyring_dependency_with_progress(parent: Any = None) -> None:
    """Verify that deployment installed keyring into Anki's import path."""
    if importlib.util.find_spec("keyring") is not None:
        return

    if QMessageBox is not None:
        QMessageBox.warning(
            parent,
            "Noteck",
            "The required dependency 'keyring' is missing.\n"
            "Close Anki, run deploy/deploy_anki_addon.ps1 again, and restart Anki.",
        )
