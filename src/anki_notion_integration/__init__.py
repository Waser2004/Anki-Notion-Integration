"""Anki-Notion Integration package."""

from __future__ import annotations

from .db import Database
from .settings import create_default_settings
from .notion_client import NotionClient
from .ui.ui import initialize_ui
from .ui.style_patcher import mirror_checkbox_indicator_to_tree_indicators
from .cards import ensure_notion_toggle_model

from pathlib import Path
from aqt import mw, gui_hooks
from aqt.qt import QTimer
import subprocess
import sys
import importlib.util

__all__ = ["Database", "NotionClient", "create_default_settings"]

def on_profile_did_open() -> None:
    profile_folder = mw.pm.profileFolder()

    def work() -> None:       
        # initialize the database for the current profile
        db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"
        db = Database(db_path)
        db.initialize()

        create_default_settings(db)
        ensure_notion_toggle_model(mw)
        initialize_ui()

        # Install keyring dependency if not already installed
        if importlib.util.find_spec("keyring") is None:
            subprocess.run([sys.executable, "-m", "pip", "install", "keyring"], check=False)
        
    QTimer.singleShot(0, work)

# Register hooks
gui_hooks.profile_did_open.append(on_profile_did_open)
gui_hooks.profile_did_open.append(mirror_checkbox_indicator_to_tree_indicators)

if hasattr(gui_hooks, "theme_did_change"):
    gui_hooks.theme_did_change.append(mirror_checkbox_indicator_to_tree_indicators)
