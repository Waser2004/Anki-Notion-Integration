"""Anki-Notion Integration package."""

from __future__ import annotations

from .db import Database
from .sync import trigger_startup_sync, trigger_sync_with_anki_button
from .cards import ensure_notion_toggle_model
from .settings import create_default_settings
from .notion_client import NotionClient
from .ui.ui import initialize_ui
from .ui.style_patcher import mirror_checkbox_indicator_to_tree_indicators

from aqt import mw, gui_hooks
from aqt.qt import QTimer
from pathlib import Path
import subprocess
import sys
import importlib.util

__all__ = ["Database", "NotionClient", "create_default_settings"]

def on_profile_did_open() -> None:
    profile_folder = mw.pm.profileFolder()
    db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"

    def work() -> None:       
        # initialize the database for the current profile
        db = Database(db_path)
        db.initialize()

        create_default_settings(db)
        ensure_notion_toggle_model(mw)
        initialize_ui()

        # Install keyring dependency if not already installed
        if importlib.util.find_spec("keyring") is None:
            subprocess.run([sys.executable, "-m", "pip", "install", "keyring"], check=False)

        trigger_startup_sync(mw=mw, db_path=db_path)
        
    QTimer.singleShot(0, work)

def _on_sync_will_start() -> None:
    """Run Notion sync before Anki sync starts."""
    profile_folder = mw.pm.profileFolder()
    db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"
    trigger_sync_with_anki_button(mw=mw, db_path=db_path)

# Register hooks
gui_hooks.profile_did_open.append(on_profile_did_open)
gui_hooks.profile_did_open.append(mirror_checkbox_indicator_to_tree_indicators)
if hasattr(gui_hooks, "sync_will_start"):
    gui_hooks.sync_will_start.append(_on_sync_will_start)

if hasattr(gui_hooks, "theme_did_change"):
    gui_hooks.theme_did_change.append(mirror_checkbox_indicator_to_tree_indicators)
