"""Anki-Notion Integration package."""

from __future__ import annotations

from .db import Database
from .settings import create_default_settings

from pathlib import Path

try:
    from aqt import mw, gui_hooks
    from aqt.qt import QTimer
except ImportError:
    mw = None
    gui_hooks = None
    QTimer = None

__all__ = ["Database", "create_default_settings"]

def on_profile_did_open() -> None:
    if mw is None or gui_hooks is None or QTimer is None:
        return

    profile_folder = mw.pm.profileFolder()

    def work() -> None:
        # initialize the database for the current profile
        db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"
        db = Database(db_path)
        db.initialize()
        create_default_settings(db)
        
    QTimer.singleShot(0, work)

if gui_hooks is not None:
    gui_hooks.profile_did_open.append(on_profile_did_open)
