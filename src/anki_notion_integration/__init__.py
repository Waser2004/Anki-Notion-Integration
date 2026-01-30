"""Anki-Notion Integration package."""

from __future__ import annotations

from .db import Database

from aqt import mw
from aqt.qt import QTimer
from aqt import gui_hooks
from pathlib import Path

__all__ = ["Database"]

def on_profile_did_open() -> None:
    profile_folder = mw.pm.profileFolder()

    def work() -> None:
        # initialize the database for the current profile
        db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"
        db = Database(db_path) 
        db.initialize()
        
    QTimer.singleShot(0, work)

gui_hooks.profile_did_open.append(on_profile_did_open)
