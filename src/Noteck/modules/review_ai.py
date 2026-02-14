"""Reviewer hook integration for active AI answer evaluation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .ai_api_client import AiApiClient
from .ai_settings import AiSettingsStore
from .cards import MODEL_NAME_INPUT
from .db import Database


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_controller: "ReviewAiController | None" = None


class ReviewAiController:
    """Coordinates evaluate-answer calls when Input cards are reviewed."""

    def __init__(self, mw: Any, db_path: Path) -> None:
        self._mw = mw
        self._db = Database(db_path)
        self._store = AiSettingsStore(self._db, profile_name=self._resolve_profile_name())
        self._client = AiApiClient(self._store)

    def on_reviewer_did_show_answer(self, card: Any) -> None:
        """Auto-run evaluate-answer for Input cards when answer side is shown."""
        settings = self._store.get_settings()
        if not settings.evaluate_enabled:
            return
        if self._store.get_tokens() is None:
            return
        if not self._is_input_card(card):
            return

        note = self._note_from_card(card)
        if note is None:
            return
        question = self._normalized_text(self._safe_note_field(note, "Front"))
        expected_answer = self._normalized_text(self._safe_note_field(note, "Expected Answer"))
        if not question or not expected_answer:
            return

        reviewer = getattr(self._mw, "reviewer", None)
        user_answer = self._normalized_text(self._extract_typed_answer(reviewer))
        if not user_answer:
            self._inject_eval_error("No typed answer was detected for AI evaluation.")
            return

        card_id = self._card_id(card)
        self._hide_standard_typed_answer_feedback()
        self._inject_eval_loading()

        def work() -> dict[str, Any]:
            result = self._client.evaluate_answer(
                base_url=settings.api_base_url,
                question=question,
                expected_answer=expected_answer,
                user_answer=user_answer,
                strictness=settings.evaluate_strictness,
                allow_paraphrase=settings.evaluate_allow_paraphrase,
                output_format=settings.evaluate_output_format,
            )
            return {
                "verdict": result.verdict,
                "score": result.score,
                "feedback": result.feedback,
                "missing_points": result.missing_points,
            }

        def done(future: Any) -> None:
            if self._card_id(getattr(getattr(self._mw, "reviewer", None), "card", None)) != card_id:
                return
            try:
                payload = future.result()
            except Exception as exc:
                self._inject_eval_error(f"AI evaluation failed: {exc}")
                return
            self._inject_eval_result(payload)

        taskman = getattr(self._mw, "taskman", None)
        run_in_background = getattr(taskman, "run_in_background", None)
        if callable(run_in_background):
            run_in_background(work, done)
            return

        try:
            payload = work()
        except Exception as exc:
            self._inject_eval_error(f"AI evaluation failed: {exc}")
            return
        self._inject_eval_result(payload)

    def _inject_eval_loading(self) -> None:
        """Render loading state in the review webview eval section."""
        self._eval_js("if(window.NoteckAiEvalRenderLoading){window.NoteckAiEvalRenderLoading();}")

    def _hide_standard_typed_answer_feedback(self) -> None:
        """Hide Anki's built-in typed-answer comparison when AI evaluation is active."""
        self._eval_js(
            "(function(){"
            "var selectors=['#typeans','.typeGood','.typeBad','.typeMissed','.typePrompt','.typeOff'];"
            "selectors.forEach(function(selector){"
            "document.querySelectorAll(selector).forEach(function(node){"
            "if(node&&node.style){node.style.display='none';}"
            "});"
            "});"
            "})();"
        )

    def _inject_eval_result(self, payload: dict[str, Any]) -> None:
        """Render successful evaluate-answer payload in the review webview."""
        encoded = json.dumps(payload, ensure_ascii=True)
        self._eval_js(f"if(window.NoteckAiEvalRender){{window.NoteckAiEvalRender({encoded});}}")

    def _inject_eval_error(self, message: str) -> None:
        """Render error state in the review webview."""
        encoded = json.dumps(str(message), ensure_ascii=True)
        self._eval_js(f"if(window.NoteckAiEvalRenderError){{window.NoteckAiEvalRenderError({encoded});}}")

    def _eval_js(self, script: str) -> None:
        """Execute one JavaScript snippet in the reviewer webview."""
        reviewer = getattr(self._mw, "reviewer", None)
        web = getattr(reviewer, "web", None)
        eval_fn = getattr(web, "eval", None)
        if callable(eval_fn):
            eval_fn(script)

    @staticmethod
    def _safe_note_field(note: Any, field_name: str) -> str:
        """Read one note field safely across note implementations."""
        try:
            value = note[field_name]
        except Exception:
            return ""
        return str(value) if value is not None else ""

    @staticmethod
    def _normalized_text(value: str) -> str:
        """Strip HTML tags and collapse whitespace for AI request text."""
        without_html = _HTML_TAG_RE.sub(" ", value or "")
        return _WHITESPACE_RE.sub(" ", without_html).strip()

    @staticmethod
    def _note_from_card(card: Any) -> Any | None:
        """Resolve note object from a card instance."""
        if card is None:
            return None
        note_fn = getattr(card, "note", None)
        if callable(note_fn):
            try:
                return note_fn()
            except Exception:
                return None
        return None

    @staticmethod
    def _card_id(card: Any) -> int | None:
        """Resolve stable card id when available."""
        if card is None:
            return None
        raw_id = getattr(card, "id", None)
        try:
            return int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_input_card(card: Any) -> bool:
        """Return whether a reviewer card belongs to Noteck Input model."""
        note = ReviewAiController._note_from_card(card)
        if note is None:
            return False
        note_type_fn = getattr(note, "note_type", None)
        if callable(note_type_fn):
            try:
                note_type_payload = note_type_fn()
                if isinstance(note_type_payload, dict):
                    return str(note_type_payload.get("name") or "") == MODEL_NAME_INPUT
            except Exception:
                return False
        model = getattr(note, "model", None)
        if callable(model):
            try:
                payload = model()
                if isinstance(payload, dict):
                    return str(payload.get("name") or "") == MODEL_NAME_INPUT
            except Exception:
                return False
        return False

    @staticmethod
    def _extract_typed_answer(reviewer: Any) -> str:
        """Best-effort extraction of current typed-answer text from reviewer state."""
        if reviewer is None:
            return ""
        for attr_name in ("typedAnswer", "typed_answer", "typeAns"):
            raw = getattr(reviewer, attr_name, None)
            if callable(raw):
                try:
                    value = raw()
                except Exception:
                    value = None
            else:
                value = raw
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _resolve_profile_name(self) -> str | None:
        """Resolve active profile name for per-profile keyring namespacing."""
        pm = getattr(self._mw, "pm", None)
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


def initialize_review_ai_hooks(mw: Any, db_path: Path, gui_hooks: Any) -> None:
    """Register reviewer hooks for active AI evaluation once per profile."""
    global _controller
    if _controller is not None:
        return
    reviewer_hook = getattr(gui_hooks, "reviewer_did_show_answer", None)
    if reviewer_hook is None:
        return
    _controller = ReviewAiController(mw, db_path)
    reviewer_hook.append(_controller.on_reviewer_did_show_answer)
