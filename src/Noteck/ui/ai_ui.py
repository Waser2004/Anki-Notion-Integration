"""AI settings page UI for Noteck AI API integration."""

from __future__ import annotations

import queue
import threading
from typing import Any

from aqt.qt import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from ..modules.ai_api_client import AiApiClient
from ..modules.ai_settings import (
    AI_API_BASE_URL_KEY,
    AI_API_EMAIL_KEY,
    AI_EVALUATE_ALLOW_PARAPHRASE_KEY,
    AI_EVALUATE_ENABLED_KEY,
    AI_EVALUATE_OUTPUT_FORMAT_KEY,
    AI_EVALUATE_STRICTNESS_KEY,
    AI_GENERATE_CLOZE_VARIANTS_DIFFICULTY_KEY,
    AI_GENERATE_CLOZE_VARIANTS_ENABLED_KEY,
    AI_GENERATE_CLOZE_VARIANTS_KEEP_LENGTH_KEY,
    AI_GENERATE_CLOZE_VARIANTS_NO_TRICK_KEY,
    AI_GENERATE_CLOZE_VARIANTS_NUMBER_KEY,
    AI_GENERATE_CLOZE_VARIANTS_STYLE_KEY,
    AI_GENERATE_VARIANTS_DIFFICULTY_KEY,
    AI_GENERATE_VARIANTS_ENABLED_KEY,
    AI_GENERATE_VARIANTS_KEEP_LENGTH_KEY,
    AI_GENERATE_VARIANTS_NO_TRICK_KEY,
    AI_GENERATE_VARIANTS_NUMBER_KEY,
    AI_GENERATE_VARIANTS_STYLE_KEY,
    AI_TTS_ENABLED_KEY,
    AI_TTS_SPEED_KEY,
    AI_TTS_VOICE_KEY,
    AI_VARIANT_STYLE_OPTIONS,
    AI_VOICE_DESCRIPTIONS,
    AI_VOICE_OPTIONS,
    AiSettingsStore,
)
from ..modules.db import Database
from .ui import UiContext


class AiPage(QWidget):
    """Qt widget for AI API login and AI feature configuration."""

    def __init__(self, parent: QWidget, context: UiContext) -> None:
        super().__init__(parent)
        self._context = context
        self._db = Database(context.db_path)
        self._store = AiSettingsStore(self._db, profile_name=self._resolve_profile_name(context))
        self._client = AiApiClient(self._store)
        self._is_loading = False
        self._worker_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(11, 11, 11, 11)
        root_layout.addWidget(self._build_scroll_area(), 1)

        self._worker_timer = QTimer(self)
        self._worker_timer.setInterval(50)
        self._worker_timer.timeout.connect(self._drain_worker_queue)
        self._reload_values()

    def _build_scroll_area(self) -> QScrollArea:
        """Build a scroll area wrapping all AI configuration groups."""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )

        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_auth_group())
        layout.addWidget(self._build_variants_group())
        layout.addWidget(self._build_cloze_variants_group())
        layout.addWidget(self._build_tts_group())
        layout.addWidget(self._build_evaluate_group())
        layout.addStretch(1)

        scroll.setWidget(content)
        return scroll

    def _build_auth_group(self) -> QGroupBox:
        """Build AI authentication controls."""
        group = QGroupBox("AI Authentication", self)
        layout = QVBoxLayout(group)

        description = QLabel("Login only. Registration is intentionally not available in the add-on.", group)
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)

        self._base_url_label = QLabel("API base URL", group)
        self._base_url_input = QLineEdit(group)
        self._base_url_input.setPlaceholderText("http://127.0.0.1:8000")
        self._base_url_input.editingFinished.connect(lambda: self._save_text(AI_API_BASE_URL_KEY, self._base_url_input))
        form.addRow(self._base_url_label, self._base_url_input)

        self._email_label = QLabel("Email", group)
        self._email_input = QLineEdit(group)
        self._email_input.setPlaceholderText("name@example.com")
        self._email_input.editingFinished.connect(lambda: self._save_text(AI_API_EMAIL_KEY, self._email_input))
        form.addRow(self._email_label, self._email_input)

        self._password_label = QLabel("Password", group)
        self._password_input = QLineEdit(group)
        self._password_input.setPlaceholderText("Password")
        self._password_input.setEchoMode(self._password_echo_mode())
        form.addRow(self._password_label, self._password_input)

        actions = QHBoxLayout()
        self._login_button = QPushButton("Login", group)
        self._login_button.clicked.connect(self._on_login_clicked)
        actions.addWidget(self._login_button)

        self._logout_button = QPushButton("Logout", group)
        self._logout_button.clicked.connect(self._on_logout_clicked)
        actions.addWidget(self._logout_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._auth_status_label = QLabel("", group)
        self._auth_status_label.setWordWrap(True)
        layout.addWidget(self._auth_status_label)
        return group

    def _build_variants_group(self) -> QGroupBox:
        """Build controls for question variant generation."""
        group = QGroupBox("Generate Question Variants", self)
        form = QFormLayout(group)
        form.setContentsMargins(11, 11, 11, 11)

        self._variants_enabled_checkbox = QCheckBox("Enable generate-question-variants", group)
        self._variants_enabled_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_GENERATE_VARIANTS_ENABLED_KEY, self._variants_enabled_checkbox)
        )
        form.addRow(self._variants_enabled_checkbox)

        self._number_variations_combo = QComboBox(group)
        for value in range(1, 21):
            self._number_variations_combo.addItem(str(value), value)
        self._number_variations_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_GENERATE_VARIANTS_NUMBER_KEY, self._number_variations_combo)
        )
        form.addRow("Number variations", self._number_variations_combo)

        self._style_combo = QComboBox(group)
        for style in AI_VARIANT_STYLE_OPTIONS:
            self._style_combo.addItem(style, style)
        self._style_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_GENERATE_VARIANTS_STYLE_KEY, self._style_combo)
        )
        form.addRow("Style", self._style_combo)

        self._difficulty_combo = QComboBox(group)
        for difficulty in ("easy", "medium", "hard"):
            self._difficulty_combo.addItem(difficulty, difficulty)
        self._difficulty_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_GENERATE_VARIANTS_DIFFICULTY_KEY, self._difficulty_combo)
        )
        form.addRow("Difficulty", self._difficulty_combo)

        self._no_trick_checkbox = QCheckBox("No trick questions", group)
        self._no_trick_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_GENERATE_VARIANTS_NO_TRICK_KEY, self._no_trick_checkbox)
        )
        form.addRow(self._no_trick_checkbox)

        self._keep_length_checkbox = QCheckBox("Keep length similar", group)
        self._keep_length_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_GENERATE_VARIANTS_KEEP_LENGTH_KEY, self._keep_length_checkbox)
        )
        form.addRow(self._keep_length_checkbox)

        return group

    def _build_tts_group(self) -> QGroupBox:
        """Build controls for text-to-speech synthesis settings."""
        group = QGroupBox("Text-to-Speech", self)
        form = QFormLayout(group)
        form.setContentsMargins(11, 11, 11, 11)

        self._tts_enabled_checkbox = QCheckBox("Enable text-to-speech", group)
        self._tts_enabled_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_TTS_ENABLED_KEY, self._tts_enabled_checkbox)
        )
        form.addRow(self._tts_enabled_checkbox)

        self._voice_combo = QComboBox(group)
        for voice in AI_VOICE_OPTIONS:
            description = AI_VOICE_DESCRIPTIONS.get(voice, "")
            label = f"{voice} - {description}" if description else voice
            self._voice_combo.addItem(label, voice)
        self._voice_combo.currentIndexChanged.connect(lambda: self._save_combo_data(AI_TTS_VOICE_KEY, self._voice_combo))
        form.addRow("Voice", self._voice_combo)

        speed_row = QWidget(group)
        speed_layout = QHBoxLayout(speed_row)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        self._speed_slider = QSlider(self._orientation_horizontal(), speed_row)
        self._speed_slider.setMinimum(50)
        self._speed_slider.setMaximum(200)
        self._speed_slider.setSingleStep(5)
        self._speed_slider.setPageStep(5)
        self._speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        speed_layout.addWidget(self._speed_slider, 1)

        self._speed_value_label = QLabel("1.00x", speed_row)
        speed_layout.addWidget(self._speed_value_label)
        form.addRow("Speed (0.5-2.0)", speed_row)
        return group

    def _build_cloze_variants_group(self) -> QGroupBox:
        """Build controls for cloze variant generation."""
        group = QGroupBox("Generate Cloze Variants", self)
        form = QFormLayout(group)
        form.setContentsMargins(11, 11, 11, 11)

        self._cloze_variants_enabled_checkbox = QCheckBox("Enable generate-cloze-variants", group)
        self._cloze_variants_enabled_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_GENERATE_CLOZE_VARIANTS_ENABLED_KEY, self._cloze_variants_enabled_checkbox)
        )
        form.addRow(self._cloze_variants_enabled_checkbox)

        self._cloze_number_variations_combo = QComboBox(group)
        for value in range(1, 21):
            self._cloze_number_variations_combo.addItem(str(value), value)
        self._cloze_number_variations_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_GENERATE_CLOZE_VARIANTS_NUMBER_KEY, self._cloze_number_variations_combo)
        )
        form.addRow("Number variations", self._cloze_number_variations_combo)

        self._cloze_style_combo = QComboBox(group)
        for style in AI_VARIANT_STYLE_OPTIONS:
            self._cloze_style_combo.addItem(style, style)
        self._cloze_style_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_GENERATE_CLOZE_VARIANTS_STYLE_KEY, self._cloze_style_combo)
        )
        form.addRow("Style", self._cloze_style_combo)

        self._cloze_difficulty_combo = QComboBox(group)
        for difficulty in ("easy", "medium", "hard"):
            self._cloze_difficulty_combo.addItem(difficulty, difficulty)
        self._cloze_difficulty_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_GENERATE_CLOZE_VARIANTS_DIFFICULTY_KEY, self._cloze_difficulty_combo)
        )
        form.addRow("Difficulty", self._cloze_difficulty_combo)

        self._cloze_no_trick_checkbox = QCheckBox("No trick questions", group)
        self._cloze_no_trick_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_GENERATE_CLOZE_VARIANTS_NO_TRICK_KEY, self._cloze_no_trick_checkbox)
        )
        form.addRow(self._cloze_no_trick_checkbox)

        self._cloze_keep_length_checkbox = QCheckBox("Keep length similar", group)
        self._cloze_keep_length_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_GENERATE_CLOZE_VARIANTS_KEEP_LENGTH_KEY, self._cloze_keep_length_checkbox)
        )
        form.addRow(self._cloze_keep_length_checkbox)
        return group

    def _build_evaluate_group(self) -> QGroupBox:
        """Build controls for active answer evaluation settings."""
        group = QGroupBox("Evaluate Answer", self)
        form = QFormLayout(group)
        form.setContentsMargins(11, 11, 11, 11)

        self._evaluate_enabled_checkbox = QCheckBox("Enable evaluate-answer", group)
        self._evaluate_enabled_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_EVALUATE_ENABLED_KEY, self._evaluate_enabled_checkbox)
        )
        form.addRow(self._evaluate_enabled_checkbox)

        self._strictness_combo = QComboBox(group)
        for strictness in ("low", "medium", "high"):
            self._strictness_combo.addItem(strictness, strictness)
        self._strictness_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_EVALUATE_STRICTNESS_KEY, self._strictness_combo)
        )
        form.addRow("Strictness", self._strictness_combo)

        self._allow_paraphrase_checkbox = QCheckBox("Allow paraphrase", group)
        self._allow_paraphrase_checkbox.stateChanged.connect(
            lambda: self._save_bool(AI_EVALUATE_ALLOW_PARAPHRASE_KEY, self._allow_paraphrase_checkbox)
        )
        form.addRow(self._allow_paraphrase_checkbox)

        self._output_format_combo = QComboBox(group)
        for output_format in ("short", "full"):
            self._output_format_combo.addItem(output_format, output_format)
        self._output_format_combo.currentIndexChanged.connect(
            lambda: self._save_combo_data(AI_EVALUATE_OUTPUT_FORMAT_KEY, self._output_format_combo)
        )
        form.addRow("Output format", self._output_format_combo)
        return group

    def showEvent(self, event: Any) -> None:
        """Reload current settings every time the page becomes visible."""
        super().showEvent(event)
        self._reload_values()

    def _reload_values(self) -> None:
        """Reload persisted values into all controls."""
        settings = self._store.get_settings()
        self._is_loading = True
        try:
            self._base_url_input.setText(settings.api_base_url)
            self._email_input.setText(settings.email)
            self._password_input.clear()

            self._variants_enabled_checkbox.setChecked(settings.generate_variants_enabled)
            self._set_combo_data(self._number_variations_combo, settings.generate_number_variations, fallback=3)
            self._set_combo_data(self._style_combo, settings.generate_style, fallback="exam")
            self._set_combo_data(self._difficulty_combo, settings.generate_difficulty, fallback="medium")
            self._no_trick_checkbox.setChecked(settings.generate_no_trick_questions)
            self._keep_length_checkbox.setChecked(settings.generate_keep_length_similar)

            self._cloze_variants_enabled_checkbox.setChecked(settings.generate_cloze_variants_enabled)
            self._set_combo_data(self._cloze_number_variations_combo, settings.generate_cloze_number_variations, fallback=3)
            self._set_combo_data(self._cloze_style_combo, settings.generate_cloze_style, fallback="exam")
            self._set_combo_data(self._cloze_difficulty_combo, settings.generate_cloze_difficulty, fallback="medium")
            self._cloze_no_trick_checkbox.setChecked(settings.generate_cloze_no_trick_questions)
            self._cloze_keep_length_checkbox.setChecked(settings.generate_cloze_keep_length_similar)

            self._tts_enabled_checkbox.setChecked(settings.tts_enabled)
            self._set_combo_data(self._voice_combo, settings.tts_voice, fallback="alloy")
            self._speed_slider.setValue(int(round(settings.tts_speed * 100)))
            self._speed_value_label.setText(f"{settings.tts_speed:.2f}x")

            self._evaluate_enabled_checkbox.setChecked(settings.evaluate_enabled)
            self._set_combo_data(self._strictness_combo, settings.evaluate_strictness, fallback="medium")
            self._allow_paraphrase_checkbox.setChecked(settings.evaluate_allow_paraphrase)
            self._set_combo_data(self._output_format_combo, settings.evaluate_output_format, fallback="short")
        finally:
            self._is_loading = False

        self._update_auth_status()
        self._apply_auth_visibility()

    def _set_combo_data(self, combo: QComboBox, value: Any, fallback: Any) -> None:
        """Set one combo index by item data with fallback behavior."""
        target = value if value is not None else fallback
        index = combo.findData(target)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _save_text(self, key: str, widget: QLineEdit) -> None:
        """Persist one text value when editing finished."""
        if self._is_loading:
            return
        self._store.set_value(key, widget.text().strip())

    def _save_bool(self, key: str, widget: QCheckBox) -> None:
        """Persist one checkbox value."""
        if self._is_loading:
            return
        self._store.set_bool(key, widget.isChecked())

    def _save_combo_data(self, key: str, widget: QComboBox) -> None:
        """Persist one combo selected data value."""
        if self._is_loading:
            return
        data = widget.currentData()
        self._store.set_value(key, data if data is not None else widget.currentText())

    def _on_speed_slider_changed(self, _value: int) -> None:
        """Persist text-to-speech speed whenever the slider value changes."""
        speed_value = self._speed_slider.value() / 100.0
        self._speed_value_label.setText(f"{speed_value:.2f}x")
        if self._is_loading:
            return
        self._store.set_value(AI_TTS_SPEED_KEY, f"{speed_value:.2f}")

    def _on_login_clicked(self) -> None:
        """Authenticate against the AI API and persist returned tokens."""
        base_url = self._base_url_input.text().strip()
        email = self._email_input.text().strip()
        password = self._password_input.text()
        if not base_url:
            self._auth_status_label.setText("Please enter an AI API base URL.")
            return
        if not email or not password:
            self._auth_status_label.setText("Please enter email and password to login.")
            return

        self._set_auth_controls_enabled(False)
        self._auth_status_label.setText("Logging in...")

        def worker() -> None:
            try:
                self._client.login(base_url=base_url, email=email, password=password)
                profile = self._client.me(base_url=base_url)
                self._worker_queue.put(("login_ok", profile.email))
            except Exception as exc:
                self._worker_queue.put(("login_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self._worker_timer.start()

    def _on_logout_clicked(self) -> None:
        """Clear local token secrets and update login status."""
        self._client.logout()
        self._auth_status_label.setText("Logged out.")
        self._update_auth_status()
        self._apply_auth_visibility()

    def _drain_worker_queue(self) -> None:
        """Apply async login results on the Qt main thread."""
        processed = 0
        while processed < 20:
            try:
                event, payload = self._worker_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            self._set_auth_controls_enabled(True)
            if event == "login_ok":
                # Keep password input transient; never persist it.
                self._password_input.clear()
                resolved_email = payload if isinstance(payload, str) and payload.strip() else self._email_input.text().strip()
                self._auth_status_label.setText(f"Logged in as {resolved_email}.")
                self._update_auth_status()
                self._apply_auth_visibility()
            elif event == "login_error":
                self._auth_status_label.setText(f"Login failed: {payload}")
            self._worker_timer.stop()

    def _update_auth_status(self) -> None:
        """Refresh status text based on stored email/token availability."""
        tokens = self._store.get_tokens()
        email = self._email_input.text().strip()
        if tokens is not None and email:
            self._auth_status_label.setText(f"Logged in as {email}.")
            return
        if tokens is not None:
            self._auth_status_label.setText("Logged in.")
            return
        if not self._auth_status_label.text().strip():
            self._auth_status_label.setText("Not logged in.")

    def _apply_auth_visibility(self) -> None:
        """Hide login form inputs/buttons once a token pair is available."""
        is_logged_in = self._store.get_tokens() is not None
        login_controls_visible = not is_logged_in
        for widget in (
            self._base_url_label,
            self._base_url_input,
            self._email_label,
            self._email_input,
            self._password_label,
            self._password_input,
            self._login_button,
        ):
            widget.setVisible(login_controls_visible)

    def _set_auth_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable auth form controls while async login runs."""
        self._base_url_input.setEnabled(enabled)
        self._email_input.setEnabled(enabled)
        self._password_input.setEnabled(enabled)
        self._login_button.setEnabled(enabled)
        self._logout_button.setEnabled(enabled)

    @staticmethod
    def _password_echo_mode() -> Any:
        """Return Qt password echo mode in Qt5/Qt6 compatible form."""
        echo_mode = getattr(QLineEdit, "EchoMode", None)
        if echo_mode is not None and hasattr(echo_mode, "Password"):
            return echo_mode.Password
        return getattr(QLineEdit, "Password")

    @staticmethod
    def _orientation_horizontal() -> Any:
        """Return horizontal slider orientation across Qt API versions."""
        from aqt.qt import Qt

        orientation = getattr(Qt, "Orientation", None)
        if orientation is not None and hasattr(orientation, "Horizontal"):
            return orientation.Horizontal
        return getattr(Qt, "Horizontal")

    @staticmethod
    def _resolve_profile_name(context: UiContext) -> str | None:
        """Resolve current Anki profile for namespaced keyring secret storage."""
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


def build_page(parent: QWidget, context: UiContext) -> QWidget:
    """Factory used by `ui.json` for lazy AI page construction."""
    return AiPage(parent, context)
