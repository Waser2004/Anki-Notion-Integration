"""Anki-Notion Integration package."""

from __future__ import annotations

from pathlib import Path

from .db import Database
from .sync import trigger_startup_sync, trigger_sync_with_anki_button
from .cards import ensure_notion_toggle_model
from .settings import create_default_settings
from .notion_client import NotionClient
from .dependency_installer import install_keyring_dependency_with_progress

# This project is primarily an Anki add-on, but we also want the core modules to be
# importable in plain Python test environments where `aqt` is not available.
try:
    from aqt import mw, gui_hooks  # type: ignore
    from aqt.qt import QTimer  # type: ignore
    from .ui.ui import initialize_ui
    from .ui.style_patcher import mirror_checkbox_indicator_to_tree_indicators
except ModuleNotFoundError:
    mw = None
    gui_hooks = None
    QTimer = None
    initialize_ui = None
    mirror_checkbox_indicator_to_tree_indicators = None

__all__ = ["Database", "NotionClient", "create_default_settings"]

def on_profile_did_open() -> None:
    """Initialize the add-on when an Anki profile opens."""
    if mw is None or QTimer is None:
        return

    profile_folder = mw.pm.profileFolder()
    db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"

    def work() -> None:       
        # initialize the database for the current profile
        db = Database(db_path)
        db.initialize()

        create_default_settings(db)
        ensure_notion_toggle_model(mw)
        if callable(initialize_ui):
            initialize_ui()

        # Install keyring dependency if needed and surface progress in the UI.
        install_keyring_dependency_with_progress(parent=mw)

        trigger_startup_sync(mw=mw, db_path=db_path)
        
    QTimer.singleShot(0, work)

def _on_sync_will_start() -> None:
    """Run Notion sync before Anki sync starts."""
    if mw is None:
        return

    profile_folder = mw.pm.profileFolder()
    db_path = Path(profile_folder) / "Anki_Notion_Integration" / "db" / "notion_integration.db"
    trigger_sync_with_anki_button(mw=mw, db_path=db_path)

# Register hooks
if gui_hooks is not None:
    gui_hooks.profile_did_open.append(on_profile_did_open)
    if callable(mirror_checkbox_indicator_to_tree_indicators):
        gui_hooks.profile_did_open.append(mirror_checkbox_indicator_to_tree_indicators)
    if hasattr(gui_hooks, "sync_will_start"):
        gui_hooks.sync_will_start.append(_on_sync_will_start)

    if hasattr(gui_hooks, "theme_did_change"):
        if callable(mirror_checkbox_indicator_to_tree_indicators):
            gui_hooks.theme_did_change.append(mirror_checkbox_indicator_to_tree_indicators)
