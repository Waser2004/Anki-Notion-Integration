"""Load and interpret Noteck release notes without depending on Anki or Qt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .db import Database


LAST_SEEN_RELEASE_KEY = "release_notes_last_seen"
SHOW_AFTER_UPDATE_KEY = "release_notes_show_after_update"
STARTUP_SYNC_RELEASE_KEY = "startup_sync_release_seen"
FORCE_RELEASE_NOTES_SENTINEL = ".force_release_notes_on_startup"
# These releases require the user to start the first sync manually because their
# parser changes can make that sync considerably longer than a normal startup sync.
MANUAL_FIRST_SYNC_RELEASES = frozenset({"1.3.0"})

_RELEASE_HEADING = re.compile(
    r"^##\s+(?P<version>\S+)\s+-\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)
_ANY_LEVEL_TWO_HEADING = re.compile(r"^##\s+.+$", re.MULTILINE)


class ReleaseNotesError(RuntimeError):
    """Raised when bundled release notes cannot be loaded or validated."""


@dataclass(frozen=True)
class ReleaseNoteEntry:
    """One versioned release section from the Markdown changelog."""

    version: str
    release_date: str
    markdown: str


@dataclass(frozen=True)
class ReleaseNotesDocument:
    """Parsed release-note content for complete-history and latest-only views."""

    source_path: Path
    markdown: str
    entries: tuple[ReleaseNoteEntry, ...]

    @property
    def latest(self) -> ReleaseNoteEntry:
        """Return the first (newest) release entry."""
        return self.entries[0]

    @property
    def latest_markdown(self) -> str:
        """Return a friendly title followed by only the newest release."""
        return f"# What's new in Noteck\n\n{self.latest.markdown}"


def default_release_notes_path() -> Path:
    """Resolve the packaged changelog, with a source-tree fallback for development."""
    packaged_path = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    if packaged_path.is_file():
        return packaged_path

    # In a source checkout CHANGELOG.md remains at the repository root and is copied
    # into the add-on by every deployment script.
    source_path = Path(__file__).resolve().parents[3] / "CHANGELOG.md"
    return source_path


def load_release_notes(path: str | Path | None = None) -> ReleaseNotesDocument:
    """Read and validate newest-first Markdown release notes."""
    source_path = Path(path) if path is not None else default_release_notes_path()
    if not source_path.is_file():
        raise ReleaseNotesError(f"Release notes not found: {source_path}")

    try:
        markdown = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseNotesError(f"Could not read release notes: {source_path}") from exc

    matches = list(_RELEASE_HEADING.finditer(markdown))
    if not matches:
        raise ReleaseNotesError(
            "Release notes must contain a heading like '## 1.3.0 - 2026-07-14'."
        )

    # Every level-two heading is reserved for a release, which keeps authoring errors
    # visible instead of silently making a release impossible to detect.
    if len(matches) != len(_ANY_LEVEL_TWO_HEADING.findall(markdown)):
        raise ReleaseNotesError(
            "Every level-two heading must use '## <version> - YYYY-MM-DD'."
        )

    entries: list[ReleaseNoteEntry] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        section = markdown[match.start():end].strip()
        entries.append(
            ReleaseNoteEntry(
                version=match.group("version"),
                release_date=match.group("date"),
                markdown=section,
            )
        )

    return ReleaseNotesDocument(
        source_path=source_path.resolve(),
        markdown=markdown.strip(),
        entries=tuple(entries),
    )


def should_show_release_notes(
    *,
    latest_release: str,
    last_seen_release: str | None,
    database_existed: bool,
    show_after_update: bool = True,
    forced: bool = False,
) -> bool:
    """Decide whether startup should show the newest release notes."""
    if forced:
        return True
    if not show_after_update:
        return False
    if last_seen_release is None:
        # Existing databases predate this feature and represent an add-on update.
        # A missing database represents a fresh installation, which stays quiet.
        return database_existed
    return last_seen_release != latest_release


def consume_startup_sync_suppression(
    db: Database,
    *,
    latest_release: str,
    database_existed: bool,
) -> bool:
    """Consume this release's first-start marker and decide whether to skip auto-sync."""
    if db.get_setting(STARTUP_SYNC_RELEASE_KEY) == latest_release:
        return False

    # Persist before any release-notes UI work so a UI failure cannot cause startup
    # sync to remain disabled on every subsequent launch.
    db.set_setting(STARTUP_SYNC_RELEASE_KEY, latest_release)
    return (
        database_existed
        and latest_release in MANUAL_FIRST_SYNC_RELEASES
    )


def force_release_notes_requested(path: str | Path) -> bool:
    """Return whether a test deployment requested a one-time startup display."""
    return Path(path).is_file()


def startup_release_notes_required(
    db: Database,
    *,
    latest_release: str,
    database_existed: bool,
    forced: bool = False,
) -> bool:
    """Check persisted state and initialize a quiet fresh-install marker."""
    last_seen_release = db.get_setting(LAST_SEEN_RELEASE_KEY)
    show_after_update = release_notes_show_after_update(db)
    should_show = should_show_release_notes(
        latest_release=latest_release,
        last_seen_release=last_seen_release,
        database_existed=database_existed,
        show_after_update=show_after_update,
        forced=forced,
    )
    if not should_show and (last_seen_release is None or not show_after_update):
        # A suppressed release is considered handled. Re-enabling the preference
        # therefore applies to the next update rather than reopening old notes.
        db.set_setting(LAST_SEEN_RELEASE_KEY, latest_release)
    return should_show


def release_notes_show_after_update(db: Database) -> bool:
    """Return the automatic-display preference, which defaults to enabled."""
    value = db.get_setting(SHOW_AFTER_UPDATE_KEY)
    return value is None or value.strip().lower() not in {"0", "false", "no", "off"}


def set_release_notes_show_after_update(db: Database, enabled: bool) -> None:
    """Persist whether future add-on updates should open release notes."""
    db.set_setting(SHOW_AFTER_UPDATE_KEY, "1" if enabled else "0")


def mark_release_notes_seen(
    db: Database,
    *,
    release: str,
    force_sentinel_path: str | Path | None = None,
) -> None:
    """Persist a displayed release and optionally consume its test marker."""
    db.set_setting(LAST_SEEN_RELEASE_KEY, release)
    if force_sentinel_path is not None:
        clear_force_release_notes(force_sentinel_path)


def clear_force_release_notes(path: str | Path) -> None:
    """Consume a test-only one-shot release-notes marker if it exists."""
    Path(path).unlink(missing_ok=True)
