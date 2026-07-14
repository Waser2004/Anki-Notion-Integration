"""Qt dialog and startup orchestration for bundled Noteck release notes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QTextBrowser,
    QUrl,
    QVBoxLayout,
    QWidget,
)

from ..modules.db import Database
from ..modules.release_notes import (
    FORCE_RELEASE_NOTES_SENTINEL,
    ReleaseNotesError,
    force_release_notes_requested,
    load_release_notes,
    mark_release_notes_seen,
    startup_release_notes_required,
)


_LOG = logging.getLogger(__name__)
_open_dialogs: set["ReleaseNotesDialog"] = set()


class ReleaseNotesDialog(QDialog):
    """Modeless, scrollable Markdown viewer for Noteck release notes."""

    def __init__(self, parent: QWidget | None, markdown: str, base_directory: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("Noteck Release Notes")
        self.resize(700, 650)
        self.setMinimumSize(520, 420)

        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(True)
        browser.document().setBaseUrl(QUrl.fromLocalFile(f"{base_directory.resolve()}/"))
        browser.document().setDefaultStyleSheet(
            "body { line-height: 1.35; }"
            "h1 { font-size: 24px; margin-bottom: 14px; }"
            "h2 { font-size: 19px; margin-top: 18px; margin-bottom: 8px; }"
            "h3 { font-size: 16px; margin-top: 14px; margin-bottom: 6px; }"
            "p, li { margin-bottom: 6px; }"
            "a { text-decoration: none; }"
            "img { max-width: 100%; }"
        )
        browser.setMarkdown(markdown)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(browser, 1)
        layout.addWidget(buttons)


def show_release_notes(
    parent: QWidget | None,
    *,
    content_mode: Literal["latest", "all"] = "all",
) -> bool:
    """Open a modeless release-notes window and retain it until it closes."""
    try:
        document = load_release_notes()
    except ReleaseNotesError as exc:
        QMessageBox.critical(parent, "Release notes", str(exc))
        return False

    markdown = document.latest_markdown if content_mode == "latest" else document.markdown
    dialog = ReleaseNotesDialog(parent, markdown, document.source_path.parent)
    _open_dialogs.add(dialog)
    dialog.finished.connect(lambda _result, item=dialog: _open_dialogs.discard(item))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return True


def show_release_notes_after_update(
    parent: QWidget | None,
    *,
    db_path: str | Path,
    database_existed: bool,
    force_sentinel_path: str | Path | None = None,
) -> bool:
    """Show the latest notes once after an update and persist the seen release."""
    try:
        document = load_release_notes()
        db = Database(db_path)
        sentinel_path = Path(force_sentinel_path) if force_sentinel_path else (
            document.source_path.parent / FORCE_RELEASE_NOTES_SENTINEL
        )
        forced = force_release_notes_requested(sentinel_path)

        should_show = startup_release_notes_required(
            db,
            latest_release=document.latest.version,
            database_existed=database_existed,
            forced=forced,
        )
        if not should_show:
            return False

        if not show_release_notes(parent, content_mode="latest"):
            return False

        # Mark as seen only after the window opened successfully. The test sentinel is
        # consumed at the same point so a failed render is retried next startup.
        mark_release_notes_seen(
            db,
            release=document.latest.version,
            force_sentinel_path=sentinel_path if forced else None,
        )
        return True
    except Exception:
        # Release notes must never prevent a profile from opening or startup sync.
        _LOG.exception("Failed to process release notes during startup.")
        return False
