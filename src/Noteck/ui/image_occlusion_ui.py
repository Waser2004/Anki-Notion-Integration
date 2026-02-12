"""Image Occlusion page UI for selecting page images and launching IOE."""

from __future__ import annotations

import importlib
import importlib.util
import queue
from pathlib import Path
import sys
import threading
from typing import Any
from urllib.request import Request, urlopen

from aqt.qt import (
    QComboBox,
    QLabel,
    QListWidget,
    QListView,
    QListWidgetItem,
    QMessageBox,
    QSize,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from ..modules.db import Database
from ..modules.notion_client import NotionClient
from ..modules.pages import PagesStore
from ..modules.parser import ImageOcclusionCandidate, collect_image_occlusion_candidates
from ..modules.settings import SettingsStore
from ..modules.sync import _download_and_store_image, _ensure_deck_id, _resolve_media_directory
from .ui import UiContext

_IOE_URL = "https://ankiweb.net/shared/info/1374772155"


class ImageOcclusionPage(QWidget):
    """Qt widget for image candidate listing and Image Occlusion launching."""

    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context
        self._db = Database(context.db_path)
        self._store = PagesStore(self._db)
        self._settings = SettingsStore(self._db, profile_name=self._resolve_profile_name(context))
        self._candidates_by_id: dict[str, ImageOcclusionCandidate] = {}
        self._add_cards_dialog: Any | None = None
        self._load_generation = 0
        self._fetch_queue: queue.Queue[tuple[str, int, Any]] = queue.Queue()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 11, 11, 11)

        self._status_label = QLabel("Select a synced page to view image candidates.", self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._install_label = QLabel(
            f'Image Occlusion Enhanced is required. <a href="{_IOE_URL}">Install add-on</a>.',
            self,
        )
        self._install_label.setWordWrap(True)
        self._install_label.setOpenExternalLinks(True)
        self._install_label.hide()
        layout.addWidget(self._install_label)

        self._page_combo = QComboBox(self)
        self._page_combo.currentIndexChanged.connect(self._on_page_changed)
        layout.addWidget(self._page_combo)

        self._image_list = QListWidget(self)
        self._image_list.itemActivated.connect(self._on_item_activated)
        self._image_list.itemClicked.connect(self._on_item_activated)
        self._image_list.setViewMode(self._list_view_mode_icon())
        self._image_list.setResizeMode(self._list_resize_mode_adjust())
        self._image_list.setMovement(self._list_movement_static())
        self._image_list.setIconSize(QSize(220, 140))
        self._image_list.setGridSize(QSize(225, 145))
        self._image_list.setWordWrap(True)
        self._image_list.setStyleSheet("QListWidget { background-color: transparent; border: none; }")
        layout.addWidget(self._image_list, 1)

        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(40)
        self._fetch_timer.timeout.connect(self._drain_fetch_queue)

        self.reload()

    def reload(self) -> None:
        """Reload page dropdown and clear image list."""
        self._load_generation += 1
        self._fetch_timer.stop()
        self._set_loading_state(is_loading=False)
        self._reload_page_combo(autoload_images=True)

    def _reload_page_combo(self, *, autoload_images: bool) -> None:
        """Reload the page dropdown from DB and optionally load first page images."""
        self._page_combo.blockSignals(True)
        self._page_combo.clear()

        pages = self._store.get_pages()
        enabled_pages = [
            page
            for page in pages.values()
            if page.sync_enabled
        ]
        enabled_pages.sort(key=lambda page: page.anki_deck_name)

        for page in enabled_pages:
            self._page_combo.addItem(page.anki_deck_name, page.notion_page_id)

        self._page_combo.blockSignals(False)

        if self._page_combo.count() == 0:
            self._status_label.setText("No synced pages available. Enable pages in the Pages tab first.")
            self._image_list.clear()
            self._install_label.hide()
            return

        self._page_combo.setCurrentIndex(0)
        if autoload_images:
            self._load_selected_page_images()

    def on_navigation_payload(self, payload: dict[str, Any]) -> None:
        """Handle optional navigation payload from other tabs."""
        page_id = payload.get("page_id")
        if not isinstance(page_id, str) or not page_id:
            return

        target_index = self._index_for_page_id(page_id)
        if target_index < 0:
            # The dropdown can be stale when coming from Pages after recent toggles.
            # Reload it once so newly enabled pages become selectable.
            self._reload_page_combo(autoload_images=False)
            target_index = self._index_for_page_id(page_id)
            if target_index < 0:
                self._status_label.setText("Selected page is not available for Image Occlusion yet.")
                return

        self._page_combo.setCurrentIndex(target_index)
        self._load_selected_page_images()

    def _index_for_page_id(self, page_id: str) -> int:
        """Return dropdown index for one page id, or -1 when absent."""
        for index in range(self._page_combo.count()):
            if self._page_combo.itemData(index) == page_id:
                return index
        return -1

    def _on_page_changed(self, _index: int) -> None:
        """Load image candidates for selected page."""
        self._load_selected_page_images()

    def _load_selected_page_images(self) -> None:
        """Fetch page content and show image candidates."""
        self._load_generation += 1
        generation = self._load_generation
        self._fetch_timer.stop()
        self._image_list.clear()
        self._candidates_by_id.clear()
        self._install_label.hide()
        self._set_loading_state(is_loading=False)

        page_id = self._selected_page_id()
        if page_id is None:
            self._status_label.setText("Select a page to view image candidates.")
            return

        if not bool(self._settings.get_value("enable_image_occlusion_parsing")):
            self._status_label.setText("Image occlusion parsing is disabled in Settings → Cards.")
            return

        ioe_launcher = self._resolve_ioe_launcher()
        if ioe_launcher is None:
            self._status_label.setText("Image Occlusion Enhanced add-on is not installed or not enabled.")
            self._install_label.show()
            return

        self._set_loading_state(is_loading=True)
        self._status_label.setText("Loading image candidates...")
        self._fetch_timer.start()
        worker = threading.Thread(
            target=self._fetch_page_images_worker,
            args=(generation, page_id),
            daemon=True,
        )
        worker.start()

    def _fetch_page_images_worker(self, generation: int, page_id: str) -> None:
        """Fetch one page's image candidates in a background thread."""
        try:
            db = Database(self._context.db_path)
            client = NotionClient.from_settings(db, profile_name=self._resolve_profile_name(self._context))
            blocks = client.get_page_content(page_id)
            candidates = collect_image_occlusion_candidates(blocks)
            preview_payloads: list[dict[str, Any]] = []
            for candidate in candidates:
                preview_payloads.append(
                    {
                        "candidate": candidate,
                        "preview_bytes": self._download_preview_bytes(candidate.image_url),
                    }
                )
            self._fetch_queue.put(("done", generation, preview_payloads))
        except Exception as exc:
            self._fetch_queue.put(("error", generation, str(exc)))

    def _drain_fetch_queue(self) -> None:
        """Apply completed background fetch events on the Qt thread."""
        processed = 0
        while processed < 100:
            try:
                event_type, generation, payload = self._fetch_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if generation != self._load_generation:
                continue

            self._set_loading_state(is_loading=False)
            self._fetch_timer.stop()

            if event_type == "error":
                self._status_label.setText(f"Failed to load images: {payload}")
                return
            if event_type == "done":
                self._apply_loaded_candidates(payload)
                return

    def _apply_loaded_candidates(self, candidates: Any) -> None:
        """Render a completed image-candidate fetch result in the list."""
        candidate_entries: list[tuple[ImageOcclusionCandidate, bytes | None]] = []
        if isinstance(candidates, list):
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                candidate = item.get("candidate")
                if not isinstance(candidate, ImageOcclusionCandidate):
                    continue
                preview_bytes = item.get("preview_bytes")
                if not isinstance(preview_bytes, (bytes, bytearray)):
                    preview_bytes = None
                candidate_entries.append((candidate, bytes(preview_bytes) if preview_bytes is not None else None))

        if not candidate_entries:
            self._status_label.setText("No eligible images found outside toggles for this page.")
            return

        self._status_label.setText("Click an image entry to open Image Occlusion Enhanced.")
        for candidate, preview_bytes in candidate_entries:
            label = " "
            item = QListWidgetItem(label, self._image_list)
            item.setSizeHint(QSize(225, 145))
            tooltip_parts = [candidate.image_url]
            if candidate.caption_plain:
                tooltip_parts.insert(0, candidate.caption_plain)
            item.setToolTip("\n".join(tooltip_parts))
            item.setData(self._item_data_user_role(), candidate.notion_block_id)
            self._apply_preview_icon(item, preview_bytes)
            self._candidates_by_id[candidate.notion_block_id] = candidate

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Launch IOE editor for selected image candidate."""
        block_id = item.data(self._item_data_user_role())
        if not isinstance(block_id, str):
            return
        candidate = self._candidates_by_id.get(block_id)
        if candidate is None:
            return

        ioe_launcher = self._resolve_ioe_launcher()
        if ioe_launcher is None:
            self._status_label.setText("Image Occlusion Enhanced add-on is not installed or not enabled.")
            self._install_label.show()
            return

        collection = getattr(self._context.mw, "col", None)
        if collection is None:
            QMessageBox.critical(self, "Image Occlusion", "Anki collection is not available.")
            return

        media_dir = _resolve_media_directory(collection)
        if media_dir is None:
            QMessageBox.critical(self, "Image Occlusion", "Anki media directory is not available.")
            return

        image_filename = _download_and_store_image(candidate.image_url, media_dir)
        if not image_filename:
            QMessageBox.critical(self, "Image Occlusion", "Failed to download image.")
            return

        image_path = str((Path(media_dir) / image_filename).resolve())
        selected_deck_name = self._selected_deck_name()

        try:
            self._select_collection_deck(selected_deck_name)
            editor = self._ensure_add_cards_editor()
            self._select_add_cards_deck(self._add_cards_dialog, selected_deck_name)
            ioe_launcher(editor, image_path=image_path)
        except Exception as exc:
            QMessageBox.critical(self, "Image Occlusion", f"Failed to launch Image Occlusion Enhanced: {exc}")

    def _ensure_add_cards_editor(self) -> Any:
        """Create or reuse an AddCards editor instance for IOE launch."""
        from aqt.addcards import AddCards

        # The Python reference may survive after the Qt object is deleted.
        if self._is_qt_object_deleted(self._add_cards_dialog):
            self._add_cards_dialog = None

        if self._add_cards_dialog is None:
            self._add_cards_dialog = AddCards(self._context.mw)
            destroyed = getattr(self._add_cards_dialog, "destroyed", None)
            if destroyed is not None and hasattr(destroyed, "connect"):
                destroyed.connect(self._on_add_cards_destroyed)

        try:
            self._add_cards_dialog.show()
            self._add_cards_dialog.raise_()
            self._add_cards_dialog.activateWindow()
        except RuntimeError:
            # Recreate once when the previous wrapped C++ object was deleted.
            self._add_cards_dialog = AddCards(self._context.mw)
            destroyed = getattr(self._add_cards_dialog, "destroyed", None)
            if destroyed is not None and hasattr(destroyed, "connect"):
                destroyed.connect(self._on_add_cards_destroyed)
            self._add_cards_dialog.show()
            self._add_cards_dialog.raise_()
            self._add_cards_dialog.activateWindow()

        editor = getattr(self._add_cards_dialog, "editor", None)
        if editor is None:
            raise RuntimeError("AddCards editor is unavailable.")
        return editor

    def _on_add_cards_destroyed(self, *_args: object) -> None:
        """Drop stale AddCards references when the Qt dialog is destroyed."""
        self._add_cards_dialog = None

    def _selected_page_id(self) -> str | None:
        """Return selected page id from dropdown."""
        value = self._page_combo.currentData()
        if isinstance(value, str) and value:
            return value
        return None

    def _selected_deck_name(self) -> str | None:
        """Return the selected page deck name used for Image Occlusion cards."""
        page_id = self._selected_page_id()
        if page_id is not None:
            stored_page = self._store.get_pages().get(page_id)
            if stored_page is not None:
                name = stored_page.anki_deck_name.strip()
                if name:
                    return name

        combo_name = self._page_combo.currentText().strip()
        if combo_name:
            return combo_name
        return None

    def _set_loading_state(self, *, is_loading: bool) -> None:
        """Toggle list/page controls while background image loading is in progress."""
        self._page_combo.setEnabled(not is_loading)
        self._image_list.setEnabled(not is_loading)

    def _select_add_cards_deck(self, add_cards_dialog: Any, deck_name: str | None) -> None:
        """Select the current page deck in AddCards before launching IOE."""
        if not deck_name:
            return

        deck_id = self._deck_id_for_name(deck_name)
        if deck_id is None:
            return

        self._select_collection_deck(deck_name)

        chooser_candidates = (
            getattr(add_cards_dialog, "deck_chooser", None),
            getattr(add_cards_dialog, "deckChooser", None),
        )
        for chooser in chooser_candidates:
            if chooser is None:
                continue
            if self._apply_deck_to_chooser(chooser, deck_id=deck_id, deck_name=deck_name):
                return

    def _select_collection_deck(self, deck_name: str | None) -> None:
        """Preselect deck at collection level so first AddCards open uses it."""
        if not deck_name:
            return

        deck_id = self._deck_id_for_name(deck_name)
        if deck_id is None:
            return

        collection = getattr(self._context.mw, "col", None)
        if collection is None:
            return
        decks = getattr(collection, "decks", None)
        select_fn = getattr(decks, "select", None) if decks is not None else None
        if callable(select_fn):
            try:
                select_fn(deck_id)
            except Exception:
                pass

    def _deck_id_for_name(self, deck_name: str) -> int | None:
        """Resolve deck id for one deck name if possible."""
        collection = getattr(self._context.mw, "col", None)
        if collection is None:
            return None

        try:
            return int(_ensure_deck_id(collection, deck_name))
        except Exception:
            return None

    @staticmethod
    def _apply_deck_to_chooser(chooser: Any, *, deck_id: int, deck_name: str) -> bool:
        """Apply deck selection to one AddCards deck chooser variant."""
        for method_name, arg in (
            ("set_current_deck_id", deck_id),
            ("setCurrentDeckId", deck_id),
            ("set_selected_deck", deck_id),
            ("setDeckId", deck_id),
            ("setDeck", deck_id),
            ("set_selected_deck_name", deck_name),
            ("setDeckName", deck_name),
        ):
            method = getattr(chooser, method_name, None)
            if not callable(method):
                continue
            try:
                method(arg)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    def _is_qt_object_deleted(obj: Any | None) -> bool:
        """Return whether a wrapped Qt object was already deleted."""
        if obj is None:
            return False
        try:
            from aqt.qt import sip

            return bool(sip.isdeleted(obj))
        except Exception:
            return False

    def _apply_preview_icon(self, item: QListWidgetItem, preview_bytes: bytes | None) -> None:
        """Render one fixed-size rounded image widget and center the image inside it."""
        from aqt.qt import QPainter, QPainterPath, QPixmap

        image_label = QLabel(self._image_list)
        image_label.setObjectName("ioImagePreview")
        image_label.setFixedSize(220, 140)
        image_label.setAlignment(self._alignment_center())
        image_label.setStyleSheet(
            "QLabel#ioImagePreview {"
            " border-radius: 10px;"
            " border: 1px solid palette(alternate-base);"
            "}"
        )

        if preview_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(preview_bytes):
                content_size = QSize(
                    image_label.width() - 2,
                    image_label.height() - 2,
                )
                # Keep aspect ratio to avoid distortion; QLabel alignment centers smaller results.
                scaled = pixmap.scaled(
                    content_size,
                    self._aspect_ratio_mode_keep(),
                    self._transformation_mode_smooth(),
                )
                # Apply rounded corners directly to the image pixels.
                rounded = QPixmap(scaled.size())
                rounded.fill(self._global_color_transparent())
                painter = QPainter(rounded)
                painter.setRenderHint(self._painter_render_hint_antialiasing(), True)
                clip = QPainterPath()
                rounded_radius = min(10, float(scaled.width()) / 2.0, float(scaled.height()) / 2.0)
                clip.addRoundedRect(
                    0.0,
                    0.0,
                    float(scaled.width()),
                    float(scaled.height()),
                    rounded_radius,
                    rounded_radius,
                )
                painter.setClipPath(clip)
                painter.drawPixmap(0, 0, scaled)
                painter.end()
                image_label.setPixmap(rounded)

        self._image_list.setItemWidget(item, image_label)

    @staticmethod
    def _download_preview_bytes(image_url: str) -> bytes | None:
        """Download bounded image bytes for thumbnail rendering."""
        request = Request(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 (Noteck)"},
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                # Keep previews bounded to avoid excessive memory use.
                data = response.read(4 * 1024 * 1024 + 1)
        except Exception:
            return None

        if len(data) > 4 * 1024 * 1024:
            return None
        return data

    @staticmethod
    def _list_view_mode_icon():
        """Return QListView icon mode in Qt5/Qt6 compatible form."""
        view_mode = getattr(QListView, "ViewMode", None)
        if view_mode is not None and hasattr(view_mode, "IconMode"):
            return getattr(view_mode, "IconMode")
        return getattr(QListView, "IconMode")

    @staticmethod
    def _list_resize_mode_adjust():
        """Return QListView adjust resize mode in Qt5/Qt6 compatible form."""
        resize_mode = getattr(QListView, "ResizeMode", None)
        if resize_mode is not None and hasattr(resize_mode, "Adjust"):
            return getattr(resize_mode, "Adjust")
        return getattr(QListView, "Adjust")

    @staticmethod
    def _list_movement_static():
        """Return QListView static movement in Qt5/Qt6 compatible form."""
        movement = getattr(QListView, "Movement", None)
        if movement is not None and hasattr(movement, "Static"):
            return getattr(movement, "Static")
        return getattr(QListView, "Static")

    @staticmethod
    def _aspect_ratio_mode_keep():
        """Return keep-aspect-ratio enum in Qt5/Qt6 compatible form."""
        from aqt.qt import Qt

        aspect_ratio_mode = getattr(Qt, "AspectRatioMode", None)
        if aspect_ratio_mode is not None and hasattr(aspect_ratio_mode, "KeepAspectRatio"):
            return getattr(aspect_ratio_mode, "KeepAspectRatio")
        return getattr(Qt, "KeepAspectRatio")

    @staticmethod
    def _transformation_mode_smooth():
        """Return smooth transformation enum in Qt5/Qt6 compatible form."""
        from aqt.qt import Qt

        transformation_mode = getattr(Qt, "TransformationMode", None)
        if transformation_mode is not None and hasattr(transformation_mode, "SmoothTransformation"):
            return getattr(transformation_mode, "SmoothTransformation")
        return getattr(Qt, "SmoothTransformation")

    @staticmethod
    def _alignment_center():
        """Return center alignment enum in Qt5/Qt6 compatible form."""
        from aqt.qt import Qt

        alignment_flag = getattr(Qt, "AlignmentFlag", None)
        if alignment_flag is not None and hasattr(alignment_flag, "AlignCenter"):
            return getattr(alignment_flag, "AlignCenter")
        return getattr(Qt, "AlignCenter")

    @staticmethod
    def _global_color_transparent():
        """Return Qt transparent color enum in a Qt5/Qt6 compatible form."""
        from aqt.qt import Qt

        global_color = getattr(Qt, "GlobalColor", None)
        if global_color is not None and hasattr(global_color, "transparent"):
            return getattr(global_color, "transparent")
        return getattr(Qt, "transparent")

    @staticmethod
    def _painter_render_hint_antialiasing():
        """Return QPainter antialiasing render hint in a Qt5/Qt6 compatible form."""
        from aqt.qt import QPainter

        render_hint = getattr(QPainter, "RenderHint", None)
        if render_hint is not None and hasattr(render_hint, "Antialiasing"):
            return getattr(render_hint, "Antialiasing")
        return getattr(QPainter, "Antialiasing")

    def _resolve_ioe_launcher(self):
        """Return IOE launch callable when installed and importable."""
        addon_manager = getattr(self._context.mw, "addonManager", None)
        if addon_manager is None:
            return None

        addon_id = "1374772155"
        installed = self._installed_addons(addon_manager)
        if addon_id not in installed:
            return None

        if not self._is_addon_enabled(addon_manager, addon_id):
            return None

        addon_dir = self._addon_directory(addon_manager, addon_id)

        launcher = self._resolve_launcher_from_loaded_modules(addon_dir=addon_dir, addon_id=addon_id)
        if launcher is not None:
            return launcher

        for module_name in ("image_occlusion_enhanced", addon_id):
            launcher = self._resolve_launcher_from_module_name(module_name)
            if launcher is not None:
                return launcher

        if addon_dir is not None:
            launcher = self._resolve_launcher_by_loading_init(addon_dir=addon_dir, addon_id=addon_id)
            if launcher is not None:
                return launcher

        return None

    @staticmethod
    def _resolve_launcher_from_module_name(module_name: str):
        """Try to import one module and read known IOE launcher names."""
        try:
            module = importlib.import_module(module_name)
        except Exception:
            return None

        return ImageOcclusionPage._launcher_from_module(module)

    @staticmethod
    def _launcher_from_module(module: Any):
        """Return IOE launcher callable from one imported module."""
        launcher = getattr(module, "onImgOccButton", None)
        if callable(launcher):
            return launcher
        launcher = getattr(module, "on_image_occlusion_button", None)
        if callable(launcher):
            return launcher
        return None

    @staticmethod
    def _installed_addons(addon_manager: Any) -> set[str]:
        """Collect installed add-on identifiers from different Anki APIs."""
        installed: set[str] = set()

        all_addons = getattr(addon_manager, "allAddons", None)
        if callable(all_addons):
            try:
                installed.update(str(item) for item in all_addons())
            except Exception:
                pass

        addons = getattr(addon_manager, "addons", None)
        if callable(addons):
            try:
                installed.update(str(item) for item in addons())
            except Exception:
                pass

        return installed

    @staticmethod
    def _is_addon_enabled(addon_manager: Any, addon_id: str) -> bool:
        """Return whether add-on is enabled when API is available."""
        is_enabled = getattr(addon_manager, "isEnabled", None)
        if callable(is_enabled):
            try:
                return bool(is_enabled(addon_id))
            except Exception:
                return False
        return True

    @staticmethod
    def _addon_directory(addon_manager: Any, addon_id: str) -> Path | None:
        """Resolve add-on folder path for one add-on id."""
        addons_folder = getattr(addon_manager, "addonsFolder", None)
        if callable(addons_folder):
            try:
                root = addons_folder()
                if isinstance(root, str) and root:
                    return Path(root) / addon_id
            except Exception:
                return None
        return None

    @staticmethod
    def _resolve_launcher_from_loaded_modules(*, addon_dir: Path | None, addon_id: str):
        """Find IOE launcher from modules that Anki has already imported."""
        addon_dir_text = str(addon_dir.resolve()) if addon_dir is not None else None
        addon_id_segment = f"/addons21/{addon_id}/"
        for module in tuple(sys.modules.values()):
            module_file = getattr(module, "__file__", None)
            if not isinstance(module_file, str):
                continue
            module_path = Path(module_file)
            module_path_text = str(module_path.resolve())
            if addon_dir_text is not None and module_path_text.startswith(addon_dir_text):
                launcher = ImageOcclusionPage._launcher_from_module(module)
                if launcher is not None:
                    return launcher
                continue

            # Fallback when add-on folder cannot be resolved from AddonManager.
            if addon_id_segment in module_path_text.replace("\\", "/"):
                launcher = ImageOcclusionPage._launcher_from_module(module)
                if launcher is not None:
                    return launcher
        return None

    @staticmethod
    def _resolve_launcher_by_loading_init(*, addon_dir: Path, addon_id: str):
        """Load add-on __init__.py directly and discover launcher callable."""
        init_path = addon_dir / "__init__.py"
        if not init_path.is_file():
            return None

        module_name = f"anki_addon_{addon_id}"
        existing_module = sys.modules.get(module_name)
        if existing_module is not None:
            launcher = ImageOcclusionPage._launcher_from_module(existing_module)
            if launcher is not None:
                return launcher

        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                str(init_path),
                submodule_search_locations=[str(addon_dir)],
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:
            return None

        return ImageOcclusionPage._launcher_from_module(module)

    @staticmethod
    def _resolve_profile_name(context: UiContext) -> str | None:
        """Resolve active profile name for settings/keyring namespaces."""
        pm = getattr(context.mw, "pm", None)
        if pm is None:
            return None

        name_attr = getattr(pm, "name", None)
        if callable(name_attr):
            try:
                return str(name_attr())
            except Exception:
                return None
        if isinstance(name_attr, str):
            return name_attr
        return None

    @staticmethod
    def _item_data_user_role() -> int:
        """Return Qt user-data role in a version-compatible way."""
        from aqt.qt import Qt

        item_data_role = getattr(Qt, "ItemDataRole", None)
        if item_data_role is not None and hasattr(item_data_role, "UserRole"):
            return int(getattr(item_data_role, "UserRole"))
        return int(getattr(Qt, "UserRole"))


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    """Factory used by ui.json to build the Image Occlusion tab widget."""
    return ImageOcclusionPage(parent=parent, context=context)
