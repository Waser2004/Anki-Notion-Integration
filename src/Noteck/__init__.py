"""Noteck package."""

from __future__ import annotations

from pathlib import Path
import sys

# Dependencies installed by the deployment scripts live next to the add-on.
_VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR_DIR.is_dir():
    _vendor_path = str(_VENDOR_DIR)
    if _vendor_path not in sys.path:
        sys.path.insert(0, _vendor_path)

from .modules.db import Database
from .modules.sync import trigger_startup_sync, trigger_sync_with_anki_button
from .modules.pages import trigger_startup_page_refresh
from .modules.cards import ensure_notion_toggle_model
from .modules.settings import create_default_settings
from .modules.notion_client import NotionClient
from .modules.release_notes import (
    ReleaseNotesError,
    consume_startup_sync_suppression,
    load_release_notes,
)

# This project is primarily an Anki add-on, but we also want the core modules to be
# importable in plain Python test environments where `aqt` is not available.
try:
    from aqt import mw, gui_hooks  # type: ignore
    from aqt.qt import QTimer  # type: ignore
    from .ui.ui import initialize_ui
    from .ui.release_notes_ui import show_release_notes_after_update
    from .ui.style_patcher import mirror_checkbox_indicator_to_tree_indicators
except ModuleNotFoundError:
    mw = None
    gui_hooks = None
    QTimer = None
    initialize_ui = None
    show_release_notes_after_update = None
    mirror_checkbox_indicator_to_tree_indicators = None

__all__ = ["Database", "NotionClient", "create_default_settings"]

def on_profile_did_open() -> None:
    """Initialize the add-on when an Anki profile opens."""
    if mw is None or QTimer is None:
        return

    profile_folder = mw.pm.profileFolder()
    db_path = Path(profile_folder) / "Noteck" / "db" / "notion_integration.db"
    database_existed = db_path.is_file()

    def work() -> None:       
        # initialize the database for the current profile
        db = Database(db_path)
        db.initialize()

        create_default_settings(db)
        ensure_notion_toggle_model(mw)
        if callable(initialize_ui):
            initialize_ui()

        # Consume the one-time guard without changing the user's auto-sync setting,
        # which keeps manual sync available and restores normal behavior next launch.
        skip_startup_sync = False
        try:
            release_notes = load_release_notes()
            skip_startup_sync = consume_startup_sync_suppression(
                db,
                latest_release=release_notes.latest.version,
                database_existed=database_existed,
            )
        except ReleaseNotesError:
            # A damaged optional changelog must not prevent startup or normal syncing.
            pass

        if callable(show_release_notes_after_update):
            show_release_notes_after_update(
                mw,
                db_path=db_path,
                database_existed=database_existed,
            )

        startup_sync_started = False
        # run a sync on startup including refreshing pages
        if not skip_startup_sync:
            startup_sync_started = trigger_startup_sync(mw=mw, db_path=db_path)

        # auto refresh pages on startup if no sync was started
        if not startup_sync_started:
            trigger_startup_page_refresh(mw=mw, db_path=db_path)
        
    QTimer.singleShot(0, work)

def _on_sync_will_start() -> None:
    """Run Notion sync before Anki sync starts."""
    if mw is None:
        return

    profile_folder = mw.pm.profileFolder()
    db_path = Path(profile_folder) / "Noteck" / "db" / "notion_integration.db"
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
