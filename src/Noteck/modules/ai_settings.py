"""Helpers for persisted AI feature settings and token secrets."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .db import Database
from .settings import KeyringSecretStore

AI_API_BASE_URL_KEY = "ai_api_base_url"
AI_API_EMAIL_KEY = "ai_api_email"

AI_GENERATE_VARIANTS_ENABLED_KEY = "ai_generate_variants_enabled"
AI_GENERATE_VARIANTS_NUMBER_KEY = "ai_generate_variants_number_variations"
AI_GENERATE_VARIANTS_STYLE_KEY = "ai_generate_variants_style"
AI_GENERATE_VARIANTS_DIFFICULTY_KEY = "ai_generate_variants_difficulty"
AI_GENERATE_VARIANTS_NO_TRICK_KEY = "ai_generate_variants_no_trick_questions"
AI_GENERATE_VARIANTS_KEEP_LENGTH_KEY = "ai_generate_variants_keep_length_similar"

AI_TTS_ENABLED_KEY = "ai_tts_enabled"
AI_TTS_VOICE_KEY = "ai_tts_voice"
AI_TTS_SPEED_KEY = "ai_tts_speed"

AI_EVALUATE_ENABLED_KEY = "ai_evaluate_answer_enabled"
AI_EVALUATE_STRICTNESS_KEY = "ai_evaluate_answer_strictness"
AI_EVALUATE_ALLOW_PARAPHRASE_KEY = "ai_evaluate_answer_allow_paraphrase"
AI_EVALUATE_OUTPUT_FORMAT_KEY = "ai_evaluate_answer_output_format"

AI_ACCESS_TOKEN_SECRET_KEY = "ai_api_access_token"
AI_REFRESH_TOKEN_SECRET_KEY = "ai_api_refresh_token"

AI_VOICE_OPTIONS: tuple[str, ...] = (
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "fable",
    "marin",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
)

AI_VOICE_DESCRIPTIONS: dict[str, str] = {
    "alloy": "Balanced and neutral",
    "ash": "Clear and grounded",
    "ballad": "Warm and expressive",
    "cedar": "Deep and steady",
    "coral": "Bright and upbeat",
    "echo": "Calm and measured",
    "fable": "Soft and storytelling",
    "marin": "Smooth and relaxed",
    "nova": "Friendly and energetic",
    "onyx": "Dark and authoritative",
    "sage": "Thoughtful and composed",
    "shimmer": "Light and lively",
    "verse": "Articulate and polished",
}

AI_VARIANT_STYLE_OPTIONS: tuple[str, ...] = (
    "exam",
    "concise",
    "socratic",
    "oral",
    "flashcard",
)

_DEFAULT_PROFILE_NAME = "default"


@dataclass(frozen=True)
class AiSettings:
    """Resolved AI feature settings with validated defaults."""

    api_base_url: str
    email: str

    generate_variants_enabled: bool
    generate_number_variations: int
    generate_style: str
    generate_difficulty: str
    generate_no_trick_questions: bool
    generate_keep_length_similar: bool

    tts_enabled: bool
    tts_voice: str
    tts_speed: float

    evaluate_enabled: bool
    evaluate_strictness: str
    evaluate_allow_paraphrase: bool
    evaluate_output_format: str


@dataclass(frozen=True)
class TokenPair:
    """Stored token pair used to authenticate AI API calls."""

    access_token: str
    refresh_token: str


def _parse_bool(value: str | None, default: bool) -> bool:
    """Parse one settings value to bool with permissive string support."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    """Parse one integer setting and clamp to the supported range."""
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _parse_float(value: str | None, default: float, minimum: float, maximum: float) -> float:
    """Parse one float setting and clamp to the supported range."""
    try:
        parsed = float(str(value).strip()) if value is not None else default
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _normalized_choice(value: str | None, default: str, allowed: tuple[str, ...]) -> str:
    """Normalize one enum-like setting against an allowed list."""
    if value is None:
        return default
    cleaned = str(value).strip().lower()
    if cleaned in allowed:
        return cleaned
    return default


class AiSettingsStore:
    """Read/write AI settings from the local DB and tokens from keyring."""

    def __init__(self, db: Database, profile_name: str | None = None) -> None:
        self._db = db
        self._profile_name = profile_name or _DEFAULT_PROFILE_NAME
        self._secret_store = KeyringSecretStore("Noteck", self._profile_name)

    def get_settings(self) -> AiSettings:
        """Return all AI settings with validated defaults."""
        base_url = (self._db.get_setting(AI_API_BASE_URL_KEY) or "http://127.0.0.1:8000").strip()
        email = (self._db.get_setting(AI_API_EMAIL_KEY) or "").strip()

        generate_difficulty = _normalized_choice(
            self._db.get_setting(AI_GENERATE_VARIANTS_DIFFICULTY_KEY),
            "medium",
            ("easy", "medium", "hard"),
        )
        generate_style = _normalized_choice(
            self._db.get_setting(AI_GENERATE_VARIANTS_STYLE_KEY),
            "exam",
            AI_VARIANT_STYLE_OPTIONS,
        )
        tts_voice = _normalized_choice(
            self._db.get_setting(AI_TTS_VOICE_KEY),
            "alloy",
            AI_VOICE_OPTIONS,
        )
        evaluate_strictness = _normalized_choice(
            self._db.get_setting(AI_EVALUATE_STRICTNESS_KEY),
            "medium",
            ("low", "medium", "high"),
        )
        evaluate_output_format = _normalized_choice(
            self._db.get_setting(AI_EVALUATE_OUTPUT_FORMAT_KEY),
            "short",
            ("short", "full"),
        )

        return AiSettings(
            api_base_url=base_url or "http://127.0.0.1:8000",
            email=email,
            generate_variants_enabled=_parse_bool(
                self._db.get_setting(AI_GENERATE_VARIANTS_ENABLED_KEY),
                default=False,
            ),
            generate_number_variations=_parse_int(
                self._db.get_setting(AI_GENERATE_VARIANTS_NUMBER_KEY),
                default=3,
                minimum=1,
                maximum=20,
            ),
            generate_style=generate_style,
            generate_difficulty=generate_difficulty,
            generate_no_trick_questions=_parse_bool(
                self._db.get_setting(AI_GENERATE_VARIANTS_NO_TRICK_KEY),
                default=True,
            ),
            generate_keep_length_similar=_parse_bool(
                self._db.get_setting(AI_GENERATE_VARIANTS_KEEP_LENGTH_KEY),
                default=True,
            ),
            tts_enabled=_parse_bool(
                self._db.get_setting(AI_TTS_ENABLED_KEY),
                default=False,
            ),
            tts_voice=tts_voice,
            tts_speed=_parse_float(
                self._db.get_setting(AI_TTS_SPEED_KEY),
                default=1.0,
                minimum=0.5,
                maximum=2.0,
            ),
            evaluate_enabled=_parse_bool(
                self._db.get_setting(AI_EVALUATE_ENABLED_KEY),
                default=False,
            ),
            evaluate_strictness=evaluate_strictness,
            evaluate_allow_paraphrase=_parse_bool(
                self._db.get_setting(AI_EVALUATE_ALLOW_PARAPHRASE_KEY),
                default=True,
            ),
            evaluate_output_format=evaluate_output_format,
        )

    def set_value(self, key: str, value: Any) -> None:
        """Persist one DB-backed AI setting value."""
        self._db.set_setting(key, "" if value is None else str(value))

    def set_bool(self, key: str, value: bool) -> None:
        """Persist one boolean setting value in SQLite-friendly form."""
        self._db.set_setting(key, "1" if value else "0")

    def get_tokens(self) -> TokenPair | None:
        """Return stored tokens when both access and refresh tokens exist."""
        access_token = self._secret_store.get_secret(AI_ACCESS_TOKEN_SECRET_KEY)
        refresh_token = self._secret_store.get_secret(AI_REFRESH_TOKEN_SECRET_KEY)
        if not access_token or not refresh_token:
            return None
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        """Persist the latest access/refresh token pair."""
        self._secret_store.set_secret(AI_ACCESS_TOKEN_SECRET_KEY, access_token)
        self._secret_store.set_secret(AI_REFRESH_TOKEN_SECRET_KEY, refresh_token)

    def clear_tokens(self) -> None:
        """Delete stored access/refresh tokens."""
        self._secret_store.delete_secret(AI_ACCESS_TOKEN_SECRET_KEY)
        self._secret_store.delete_secret(AI_REFRESH_TOKEN_SECRET_KEY)

    @staticmethod
    def encode_text_list(values: list[str]) -> str:
        """Serialize one list of strings into compact JSON."""
        return json.dumps(values, ensure_ascii=True, separators=(",", ":"))
