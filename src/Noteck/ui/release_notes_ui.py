"""Qt dialog and startup orchestration for bundled Noteck release notes."""

from __future__ import annotations

from html import escape
import logging
from math import ceil
from pathlib import Path
import re
from typing import Any, Literal

from aqt.qt import (
    QColor,
    QDialog,
    QDialogButtonBox,
    QImage,
    QMessageBox,
    QPalette,
    QPainter,
    QPainterPath,
    QRectF,
    QTextBrowser,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
    QTimer,
    QUrl,
    QVBoxLayout,
    QWidget,
    Qt,
)

from ..modules.db import Database
from ..modules.release_notes import (
    FORCE_RELEASE_NOTES_SENTINEL,
    ReleaseNotesError,
    force_release_notes_requested,
    load_release_notes,
    mark_release_notes_seen,
    release_notes_show_after_update,
    set_release_notes_show_after_update,
    startup_release_notes_required,
)


_LOG = logging.getLogger(__name__)
_open_dialogs: set["ReleaseNotesDialog"] = set()
_INITIAL_DIALOG_WIDTH = 520
_INITIAL_DIALOG_HEIGHT = 620
_IMAGE_DISPLAY_WIDTH = 450
_MEDIA_CORNER_RADIUS = 8
_CALLOUT_PATTERN = re.compile(
    r"^> \[!(?P<kind>[A-Z]+)\]\s*\n"
    r"^> \*\*(?P<title>.+?)\*\*\s*\n"
    r"^>\s*\n"
    r"(?P<body>(?:^>.*(?:\n|$))+)",
    re.MULTILINE,
)


class ReleaseNotesDialog(QDialog):
    """Modeless, scrollable Markdown viewer for Noteck release notes."""

    def __init__(
        self,
        parent: QWidget | None,
        markdown: str,
        base_directory: Path,
        db_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Noteck Release Notes")
        self.resize(_INITIAL_DIALOG_WIDTH, _INITIAL_DIALOG_HEIGHT)
        self.setMinimumSize(520, 420)

        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(True)
        browser.document().setBaseUrl(QUrl.fromLocalFile(f"{base_directory.resolve()}/"))
        palette_role = getattr(QPalette, "ColorRole", None)
        base_role = (
            palette_role.Base
            if palette_role is not None
            else getattr(QPalette, "Base")
        )
        dark_theme = browser.palette().color(base_role).lightness() < 128
        callout_background = "#494327" if dark_theme else "#fbf3db"
        callout_foreground = "#f5f5f5" if dark_theme else "#2f2f2f"
        rendered_markdown, callouts = self._extract_callouts(markdown)
        browser.document().setDefaultStyleSheet(
            "body { line-height: 1.35; }"
            "h1 { font-size: 24px; margin-bottom: 14px; }"
            "h2 { font-size: 19px; margin-top: 18px; margin-bottom: 8px; }"
            "h3 { font-size: 16px; margin-top: 14px; margin-bottom: 6px; }"
            "p, li { margin-bottom: 6px; }"
            "a { text-decoration: none; }"
            "img { max-width: 100%; }"
        )
        browser.setMarkdown(rendered_markdown)
        self._insert_callouts(
            browser.document(),
            callouts,
            background=callout_background,
            foreground=callout_foreground,
        )
        self._fit_local_images(browser.document(), base_directory)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.close)
        if db_path is not None:
            # Keep the update preference beside Close so it remains available even
            # after automatic release notes have been disabled.
            self._db = Database(db_path)
            self._automatic_button = buttons.addButton(
                "",
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            self._automatic_button.setAutoDefault(False)
            self._automatic_button.setDefault(False)
            self._automatic_button.clicked.connect(self._toggle_automatic_display)
            self._update_automatic_button_text()

        layout = QVBoxLayout(self)
        layout.addWidget(browser, 1)
        layout.addWidget(buttons)

    def _toggle_automatic_display(self, _checked: bool = False) -> None:
        """Toggle whether future updates open their release notes automatically."""
        enabled = not release_notes_show_after_update(self._db)
        set_release_notes_show_after_update(self._db, enabled)
        self._update_automatic_button_text()

    def _update_automatic_button_text(self) -> None:
        """Describe the action the preference button will perform when clicked."""
        enabled = release_notes_show_after_update(self._db)
        self._automatic_button.setText(
            "Hide after updates" if enabled else "Show after updates"
        )

    @staticmethod
    def _extract_callouts(
        markdown: str,
    ) -> tuple[str, tuple[tuple[str, str, str, str], ...]]:
        """Replace supported Markdown alerts with safe plain-text placeholders."""
        callouts: list[tuple[str, str, str, str]] = []

        def replace(match: re.Match[str]) -> str:
            placeholder = f"NOTECK_CALLOUT_PLACEHOLDER_{len(callouts)}"
            paragraphs: list[str] = []
            paragraph_lines: list[str] = []
            for line in match.group("body").splitlines():
                text = line.removeprefix("> ").removeprefix(">").strip()
                if text:
                    paragraph_lines.append(text)
                elif paragraph_lines:
                    paragraphs.append(" ".join(paragraph_lines))
                    paragraph_lines = []
            if paragraph_lines:
                paragraphs.append(" ".join(paragraph_lines))
            body = "\n\n".join(paragraphs)
            icon = "⚠️" if match.group("kind") == "WARNING" else "💡"
            callouts.append((placeholder, match.group("title"), body, icon))
            # Preserve a blank line after the placeholder so the following image or
            # paragraph remains a separate Markdown block.
            return f"{placeholder}\n"

        rendered_markdown = _CALLOUT_PATTERN.sub(replace, markdown)
        return rendered_markdown, tuple(callouts)

    @staticmethod
    def _insert_callouts(
        document: Any,
        callouts: tuple[tuple[str, str, str, str], ...],
        *,
        background: str,
        foreground: str,
    ) -> None:
        """Replace parsed placeholders with rounded Notion-style callout images."""
        for index, (placeholder, title, body, icon) in enumerate(callouts):
            cursor = document.find(placeholder)
            if cursor.isNull():
                continue

            callout_image = ReleaseNotesDialog._render_callout_image(
                document,
                title=title,
                body=body,
                icon=icon,
                background=background,
                foreground=foreground,
            )
            resource_url = QUrl(f"noteck-callout://{index}")
            resource_enum = getattr(QTextDocument, "ResourceType", None)
            image_resource = (
                resource_enum.ImageResource
                if resource_enum is not None
                else getattr(QTextDocument, "ImageResource")
            )
            document.addResource(image_resource, resource_url, callout_image)

            image_format = QTextImageFormat()
            image_format.setName(resource_url.toString())
            image_format.setWidth(_IMAGE_DISPLAY_WIDTH)
            image_format.setHeight(callout_image.height())
            cursor.insertImage(image_format)
            ReleaseNotesDialog._center_cursor_block(cursor)

    @staticmethod
    def _render_callout_image(
        document: Any,
        *,
        title: str,
        body: str,
        icon: str,
        background: str,
        foreground: str,
    ) -> QImage:
        """Render one callout with rounded corners on a transparent image."""
        padding = 12
        content_width = _IMAGE_DISPLAY_WIDTH - (2 * padding)
        content = QTextDocument()
        content.setDefaultFont(document.defaultFont())
        content.setDocumentMargin(0)
        content.setDefaultStyleSheet(
            f"body {{ color: {foreground}; margin: 0; }}"
            "table { border: 0; border-collapse: collapse; }"
            f"td {{ border: 0; color: {foreground}; padding: 0; "
            "vertical-align: top; }"
            "td.icon { width: 22px; padding-right: 6px; }"
            "td.content { line-height: 1; }"
        )
        rendered_body = escape(body).replace("\n\n", "<br><br>")
        content.setHtml(
            '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td class="icon">{escape(icon)}</td>'
            f'<td class="content"><strong>{escape(title)}</strong><br>'
            f"{rendered_body}</td>"
            "</tr></table>"
        )
        content.setTextWidth(content_width)
        image_height = ceil(content.size().height()) + (2 * padding)

        image_format_enum = getattr(QImage, "Format", None)
        argb_format = (
            image_format_enum.Format_ARGB32_Premultiplied
            if image_format_enum is not None
            else getattr(QImage, "Format_ARGB32_Premultiplied")
        )
        image = QImage(_IMAGE_DISPLAY_WIDTH, image_height, argb_format)
        image.fill(0)

        painter = QPainter(image)
        ReleaseNotesDialog._enable_antialiasing(painter)
        background_path = QPainterPath()
        background_path.addRoundedRect(
            QRectF(0, 0, image.width(), image.height()),
            _MEDIA_CORNER_RADIUS,
            _MEDIA_CORNER_RADIUS,
        )
        painter.fillPath(background_path, QColor(background))
        painter.translate(padding, padding)
        content.drawContents(painter)
        painter.end()
        return image

    @staticmethod
    def _enable_antialiasing(painter: QPainter) -> None:
        """Enable smooth rounded edges across supported Qt enum layouts."""
        render_hint_enum = getattr(QPainter, "RenderHint", None)
        antialiasing = (
            render_hint_enum.Antialiasing
            if render_hint_enum is not None
            else getattr(QPainter, "Antialiasing")
        )
        painter.setRenderHint(antialiasing, True)

    @staticmethod
    def _center_cursor_block(cursor: QTextCursor) -> None:
        """Center the current media block in the release-note document."""
        block_format = cursor.blockFormat()
        alignment_flag = getattr(Qt, "AlignmentFlag", None)
        horizontal_center = (
            alignment_flag.AlignHCenter
            if alignment_flag is not None
            else getattr(Qt, "AlignHCenter")
        )
        block_format.setAlignment(horizontal_center)
        cursor.setBlockFormat(block_format)

    @staticmethod
    def _fit_local_images(document: Any, base_directory: Path) -> None:
        """Scale bundled Markdown images to the standard 520-pixel display width."""
        resolved_base = base_directory.resolve()
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                iterator += 1
                if not fragment.isValid():
                    continue

                character_format = fragment.charFormat()
                if not character_format.isImageFormat():
                    continue

                image_format = character_format.toImageFormat()
                image_name = image_format.name()
                if "://" in image_name:
                    # Generated callouts and already-rounded resources are not files.
                    continue
                image_path = (resolved_base / image_name).resolve()
                try:
                    image_path.relative_to(resolved_base)
                except ValueError:
                    # Do not read or resize images outside the bundled release folder.
                    continue

                image = QImage(str(image_path))
                if image.isNull():
                    continue

                image_format_enum = getattr(QImage, "Format", None)
                argb_format = (
                    image_format_enum.Format_ARGB32_Premultiplied
                    if image_format_enum is not None
                    else getattr(QImage, "Format_ARGB32_Premultiplied")
                )
                rounded_image = QImage(image.size(), argb_format)
                rounded_image.fill(0)
                painter = QPainter(rounded_image)
                ReleaseNotesDialog._enable_antialiasing(painter)
                clip_path = QPainterPath()
                scaled_radius = _MEDIA_CORNER_RADIUS * (
                    image.width() / _IMAGE_DISPLAY_WIDTH
                )
                clip_path.addRoundedRect(
                    QRectF(0, 0, image.width(), image.height()),
                    scaled_radius,
                    scaled_radius,
                )
                painter.setClipPath(clip_path)
                painter.drawImage(0, 0, image)
                painter.end()

                resource_enum = getattr(QTextDocument, "ResourceType", None)
                image_resource = (
                    resource_enum.ImageResource
                    if resource_enum is not None
                    else getattr(QTextDocument, "ImageResource")
                )
                rounded_url = QUrl(f"noteck-rounded-image://{fragment.position()}")
                document.addResource(image_resource, rounded_url, rounded_image)

                # Give every release image the same predictable presentation width,
                # regardless of its original pixel dimensions.
                display_width = _IMAGE_DISPLAY_WIDTH
                display_height = round(image.height() * display_width / image.width())
                image_format.setName(rounded_url.toString())
                image_format.setWidth(display_width)
                image_format.setHeight(display_height)

                cursor = QTextCursor(document)
                cursor.setPosition(fragment.position())
                move_mode = getattr(QTextCursor, "MoveMode", None)
                keep_anchor = (
                    move_mode.KeepAnchor
                    if move_mode is not None
                    else getattr(QTextCursor, "KeepAnchor")
                )
                cursor.setPosition(fragment.position() + fragment.length(), keep_anchor)
                cursor.setCharFormat(image_format)

                # Markdown images normally occupy their own paragraph. Center that
                # paragraph so images are consistently positioned in the viewer.
                ReleaseNotesDialog._center_cursor_block(cursor)
            block = block.next()


def show_release_notes(
    parent: QWidget | None,
    *,
    content_mode: Literal["latest", "all"] = "all",
    db_path: str | Path | None = None,
) -> bool:
    """Open a modeless release-notes window and retain it until it closes."""
    try:
        document = load_release_notes()
    except ReleaseNotesError as exc:
        QMessageBox.critical(parent, "Release notes", str(exc))
        return False

    markdown = document.latest_markdown if content_mode == "latest" else document.markdown
    dialog = ReleaseNotesDialog(
        parent,
        markdown,
        document.source_path.parent,
        db_path=db_path,
    )
    _open_dialogs.add(dialog)
    dialog.finished.connect(lambda _result, item=dialog: _open_dialogs.discard(item))
    dialog.show()
    # QTextDocument calculates its content size during the first event-loop pass.
    # Reapply the intended opening dimensions afterwards so large images or long lines
    # cannot determine the dialog width. The resize runs only once, leaving the user
    # free to resize the window normally after it opens.
    QTimer.singleShot(
        0,
        lambda item=dialog: item.resize(_INITIAL_DIALOG_WIDTH, _INITIAL_DIALOG_HEIGHT),
    )
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

        if not show_release_notes(parent, content_mode="latest", db_path=db_path):
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
