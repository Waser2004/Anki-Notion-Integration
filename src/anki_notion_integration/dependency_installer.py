"""Runtime dependency installation helpers for the Anki add-on."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from typing import Any

try:
    from aqt.qt import QEventLoop, QMessageBox, QProgressDialog, QTimer, Qt  # type: ignore
except ModuleNotFoundError:
    QEventLoop = None
    QMessageBox = None
    QProgressDialog = None
    QTimer = None
    Qt = None


def install_keyring_dependency_with_progress(parent: Any = None) -> None:
    """Install keyring and show a progress dialog so startup feedback is visible."""
    if importlib.util.find_spec("keyring") is not None:
        return

    install_command = [sys.executable, "-m", "pip", "install", "keyring"]
    if (
        QProgressDialog is None
        or QTimer is None
        or QEventLoop is None
        or Qt is None
    ):
        subprocess.run(install_command, check=False)
        return

    result: dict[str, int] = {"returncode": 1}

    def _install_worker() -> None:
        completed = subprocess.run(install_command, check=False)
        result["returncode"] = int(completed.returncode)

    worker = threading.Thread(target=_install_worker, daemon=True)
    worker.start()

    progress_dialog = QProgressDialog(
        "Installing required dependency 'keyring'.\nPlease wait...",
        "",
        0,
        0,
        parent,
    )
    progress_dialog.setWindowTitle("Anki-Notion Integration")
    progress_dialog.setCancelButton(None)
    progress_dialog.setMinimumDuration(0)
    progress_dialog.setAutoClose(False)
    progress_dialog.setAutoReset(False)
    if hasattr(Qt, "WindowModality"):
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
    else:
        progress_dialog.setWindowModality(Qt.WindowModal)
    progress_dialog.show()

    loop = QEventLoop()
    poll_timer = QTimer(progress_dialog)
    poll_timer.setInterval(100)

    def _finish_when_done() -> None:
        if worker.is_alive():
            return
        poll_timer.stop()
        progress_dialog.close()
        loop.quit()

    poll_timer.timeout.connect(_finish_when_done)
    poll_timer.start()
    if hasattr(loop, "exec"):
        loop.exec()
    else:
        loop.exec_()

    if result.get("returncode", 1) != 0 and QMessageBox is not None:
        QMessageBox.warning(
            parent,
            "Anki-Notion Integration",
            "Failed to install 'keyring' automatically.\n"
            "Please install it manually in Anki's Python environment.",
        )
