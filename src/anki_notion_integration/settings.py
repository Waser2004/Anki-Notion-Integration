"""Settings access layer for the Anki-Notion integration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .db import Database


class SettingsError(RuntimeError):
    """Raised when settings metadata or storage operations fail."""


@dataclass(frozen=True)
class SettingDefinition:
    """Metadata describing a single configurable setting."""
    key: str
    type: str
    name: str
    description: str
    default: Any
    options: tuple[str, ...] | None = None
    storage: str = "db"


@dataclass(frozen=True)
class SettingsCategory:
    """A logical grouping of settings for display and organization."""
    key: str
    name: str
    description: str
    settings: tuple[SettingDefinition, ...]


class SettingsSchema:
    """Parsed settings schema with lookup helpers."""
    def __init__(self, categories: Iterable[SettingsCategory]) -> None:
        self._categories = tuple(categories)
        self._settings_by_key = {
            setting.key: setting
            for category in self._categories
            for setting in category.settings
        }

    @property
    def categories(self) -> tuple[SettingsCategory, ...]:
        """Return the schema categories."""
        return self._categories

    def get_setting(self, key: str) -> SettingDefinition:
        """Return a setting definition by key."""
        try:
            return self._settings_by_key[key]
        except KeyError as exc:
            raise SettingsError(f"Unknown setting key: {key}") from exc

    def settings(self) -> tuple[SettingDefinition, ...]:
        """Return all setting definitions."""
        return tuple(self._settings_by_key.values())


_SUPPORTED_TYPES = {"text", "checkbox", "boolean", "dropdown"}
_DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "docs" / "settings.json"
_DEFAULT_SERVICE_NAME = "anki_notion_integration"
_DEFAULT_PROFILE_NAME = "default"


def load_settings_schema(path: Path | None = None) -> SettingsSchema:
    """Load and validate the settings schema from JSON."""
    schema_path = path or _DEFAULT_SETTINGS_PATH

    if not schema_path.exists():
        raise SettingsError(f"Settings schema not found: {schema_path}")
    
    with schema_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    
    if "categories" not in payload or not isinstance(payload["categories"], list):
        raise SettingsError("Settings schema must include a list of categories.")
    
    categories: list[SettingsCategory] = []
    for category_payload in payload["categories"]:
        category_key         = category_payload.get("key")
        category_name        = category_payload.get("name")
        category_description = category_payload.get("description", "")
        settings_payload     = category_payload.get("settings", [])
        
        if not category_key or not category_name:
            raise SettingsError("Each category requires a key and name.")
        
        settings: list[SettingDefinition] = []
        for setting_payload in settings_payload:
            setting = _parse_setting_definition(setting_payload)
            settings.append(setting)

        categories.append(
            SettingsCategory(
                key=category_key,
                name=category_name,
                description=category_description,
                settings=tuple(settings),
            )
        )
    
    return SettingsSchema(categories)


def _parse_setting_definition(payload: Mapping[str, Any]) -> SettingDefinition:
    """Parse and validate a single setting definition."""
    # Extract fields from payload
    key          = payload.get("key")
    setting_type = payload.get("type")
    name         = payload.get("name")
    description  = payload.get("description", "")
    default      = payload.get("default")
    options      = payload.get("options")
    storage      = payload.get("storage", "db")

    # Validate required fields and types
    if not key or not setting_type or not name:
        raise SettingsError("Each setting requires key, type, and name.")
    if setting_type not in _SUPPORTED_TYPES:
        raise SettingsError(f"Unsupported setting type: {setting_type}")
    if setting_type == "dropdown":
        if not isinstance(options, Sequence) or not options:
            raise SettingsError(f"Dropdown setting {key} requires options.")
        options_tuple = tuple(str(option) for option in options)
    else:
        options_tuple = None

    return SettingDefinition(
        key=key,
        type=setting_type,
        name=name,
        description=description,
        default=default,
        options=options_tuple,
        storage=storage,
    )


def _parse_bool(value: str) -> bool:
    """Parse common boolean string representations."""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"Invalid boolean value: {value}")


def _coerce_value(setting: SettingDefinition, raw_value: str) -> Any:
    """Convert raw string values from storage into typed values."""
    if setting.type in {"checkbox", "boolean"}:
        return _parse_bool(raw_value)
    if setting.type == "dropdown":
        if setting.options and raw_value in setting.options:
            return raw_value
        return setting.default
    return raw_value


def _serialize_value(setting: SettingDefinition, value: Any) -> str:
    """Serialize a typed value into a string for database storage."""
    if setting.type in {"checkbox", "boolean"}:
        if isinstance(value, str):
            return "1" if _parse_bool(value) else "0"
        return "1" if bool(value) else "0"
    if setting.type == "dropdown":
        if setting.options and value not in setting.options:
            raise SettingsError(
                f"Invalid option for {setting.key}: {value}. Expected one of {setting.options}."
            )
    return "" if value is None else str(value)


def _get_keyring_module():
    """Return the keyring module or raise a descriptive error."""
    try:
        import keyring  # type: ignore
    except ModuleNotFoundError as exc:
        raise SettingsError(
            "Keyring is required for secure storage of secrets. "
            "Install the 'keyring' dependency before using secret settings."
        ) from exc
    return keyring


class KeyringSecretStore:
    """Wrapper around keyring to store per-profile secrets."""
    def __init__(self, service_name: str, profile_name: str) -> None:
        self._service_name = service_name
        self._profile_name = profile_name

    def get_secret(self, key: str) -> str | None:
        """Return a secret value from the keyring."""
        keyring = _get_keyring_module()
        return keyring.get_password(self._service_name, self._username_for(key))

    def set_secret(self, key: str, value: str) -> None:
        """Persist a secret value to the keyring."""
        keyring = _get_keyring_module()
        keyring.set_password(self._service_name, self._username_for(key), value)

    def delete_secret(self, key: str) -> None:
        """Remove a secret value from the keyring."""
        keyring = _get_keyring_module()
        if keyring.get_password(self._service_name, self._username_for(key)) is None:
            return
        keyring.delete_password(self._service_name, self._username_for(key))

    def _username_for(self, key: str) -> str:
        """Build the keyring username for a given setting key."""
        return f"{self._profile_name}:{key}"


class SettingsStore:
    """Read and write settings values backed by SQLite and keyring."""

    def __init__(
        self,
        db: Database,
        profile_name: str | None = None,
        schema: SettingsSchema | None = None,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._db = db
        self._schema = schema or load_settings_schema()
        self._profile_name = profile_name or _DEFAULT_PROFILE_NAME
        self._secret_store = KeyringSecretStore(service_name, self._profile_name)

    def get_value(self, key: str) -> Any:
        """Return the current value for a setting key."""
        setting = self._schema.get_setting(key)
        if setting.storage == "keyring":
            return self._secret_store.get_secret(key) or setting.default
        raw_value = self._db.get_setting(key)
        if raw_value is None:
            return setting.default
        return _coerce_value(setting, raw_value)

    def set_value(self, key: str, value: Any) -> None:
        """Persist a value for the specified setting key."""
        setting = self._schema.get_setting(key)
        if setting.storage == "keyring":
            if value is None or value == "":
                self._secret_store.delete_secret(key)
                return
            self._secret_store.set_secret(key, str(value))
            return
        serialized = _serialize_value(setting, value)
        self._db.set_setting(key, serialized)

    def get_grouped_settings(self) -> list[dict[str, Any]]:
        """Return grouped settings metadata with current values."""
        grouped: list[dict[str, Any]] = []
        for category in self._schema.categories:
            grouped.append(
                {
                    "key": category.key,
                    "name": category.name,
                    "description": category.description,
                    "settings": [
                        self._build_setting_payload(setting)
                        for setting in category.settings
                    ],
                }
            )
        return grouped

    def _build_setting_payload(self, setting: SettingDefinition) -> dict[str, Any]:
        """Compose a UI-friendly payload for a single setting."""
        payload: dict[str, Any] = {
            "key": setting.key,
            "type": setting.type,
            "name": setting.name,
            "description": setting.description,
            "default": setting.default,
            "value": self.get_value(setting.key),
        }
        if setting.options:
            payload["options"] = list(setting.options)
        return payload


def create_default_settings(db: Database, schema: SettingsSchema | None = None) -> None:
    """Insert default settings values when they are missing."""
    resolved_schema = schema or load_settings_schema()
    for setting in resolved_schema.settings():
        if setting.storage != "db":
            continue
        if db.get_setting(setting.key) is None:
            db.set_setting(setting.key, _serialize_value(setting, setting.default))
