"""Tests for release-note parsing and one-time startup decisions."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from Noteck.modules.release_notes import (
    LAST_SEEN_RELEASE_KEY,
    SHOW_AFTER_UPDATE_KEY,
    STARTUP_SYNC_RELEASE_KEY,
    ReleaseNotesError,
    clear_force_release_notes,
    consume_startup_sync_suppression,
    force_release_notes_requested,
    load_release_notes,
    mark_release_notes_seen,
    release_notes_show_after_update,
    set_release_notes_show_after_update,
    should_show_release_notes,
    startup_release_notes_required,
)
from Noteck.modules.db import Database


class ReleaseNotesTests(unittest.TestCase):
    """Validate the authoring format and update-detection policy."""

    def _write_notes(self, markdown: str) -> Path:
        """Create a temporary Markdown source for one parser test."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "CHANGELOG.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    def test_loads_complete_history_and_latest_release(self) -> None:
        path = self._write_notes(
            """# Changelog

## 2.0.0 - 2026-07-14

### Highlights

![New window](docs/release-notes-assets/2.0.0.png)

- Added a feature.

## 1.0.0 - 2026-01-01

- First release.
"""
        )

        document = load_release_notes(path)

        self.assertEqual(document.latest.version, "2.0.0")
        self.assertEqual(document.latest.release_date, "2026-07-14")
        self.assertEqual([entry.version for entry in document.entries], ["2.0.0", "1.0.0"])
        self.assertIn("# Changelog", document.markdown)
        self.assertIn("### Highlights", document.latest_markdown)
        self.assertIn("docs/release-notes-assets/2.0.0.png", document.latest_markdown)
        self.assertNotIn("First release", document.latest_markdown)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(ReleaseNotesError):
            load_release_notes(Path("missing-release-notes.md"))

    def test_rejects_changelog_without_release_heading(self) -> None:
        path = self._write_notes("# Changelog\n\nNo releases yet.\n")
        with self.assertRaises(ReleaseNotesError):
            load_release_notes(path)

    def test_rejects_malformed_level_two_heading(self) -> None:
        path = self._write_notes(
            "# Changelog\n\n## Upcoming release\n\n- Item\n\n## 1.0.0 - 2026-01-01\n"
        )
        with self.assertRaises(ReleaseNotesError):
            load_release_notes(path)

    def test_existing_install_without_marker_shows_after_feature_update(self) -> None:
        self.assertTrue(
            should_show_release_notes(
                latest_release="1.3.0",
                last_seen_release=None,
                database_existed=True,
            )
        )

    def test_fresh_install_without_marker_stays_quiet(self) -> None:
        self.assertFalse(
            should_show_release_notes(
                latest_release="1.3.0",
                last_seen_release=None,
                database_existed=False,
            )
        )

    def test_changed_release_shows_and_matching_release_stays_quiet(self) -> None:
        self.assertTrue(
            should_show_release_notes(
                latest_release="1.3.0",
                last_seen_release="1.2.0",
                database_existed=True,
            )
        )
        self.assertFalse(
            should_show_release_notes(
                latest_release="1.3.0",
                last_seen_release="1.3.0",
                database_existed=True,
            )
        )

    def test_first_startup_sync_is_skipped_once_for_parser_update(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Database(Path(temp_dir.name) / "noteck.db")
        db.initialize()

        self.assertTrue(
            consume_startup_sync_suppression(
                db,
                latest_release="1.3.0",
                database_existed=True,
            )
        )
        self.assertFalse(
            consume_startup_sync_suppression(
                db,
                latest_release="1.3.0",
                database_existed=True,
            )
        )
        self.assertEqual(db.get_setting(STARTUP_SYNC_RELEASE_KEY), "1.3.0")

    def test_startup_sync_is_not_skipped_for_fresh_install_or_normal_release(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        fresh_db = Database(Path(temp_dir.name) / "fresh.db")
        fresh_db.initialize()

        self.assertFalse(
            consume_startup_sync_suppression(
                fresh_db,
                latest_release="1.3.0",
                database_existed=False,
            )
        )

        existing_db = Database(Path(temp_dir.name) / "existing.db")
        existing_db.initialize()
        self.assertFalse(
            consume_startup_sync_suppression(
                existing_db,
                latest_release="1.4.0",
                database_existed=True,
            )
        )

    def test_disabled_automatic_display_suppresses_updates_but_not_test_force(self) -> None:
        self.assertFalse(
            should_show_release_notes(
                latest_release="1.4.0",
                last_seen_release="1.3.0",
                database_existed=True,
                show_after_update=False,
            )
        )
        self.assertTrue(
            should_show_release_notes(
                latest_release="1.4.0",
                last_seen_release="1.3.0",
                database_existed=True,
                show_after_update=False,
                forced=True,
            )
        )

    def test_automatic_display_preference_defaults_on_and_persists(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Database(Path(temp_dir.name) / "noteck.db")
        db.initialize()

        self.assertTrue(release_notes_show_after_update(db))
        set_release_notes_show_after_update(db, False)
        self.assertFalse(release_notes_show_after_update(db))
        self.assertEqual(db.get_setting(SHOW_AFTER_UPDATE_KEY), "0")
        set_release_notes_show_after_update(db, True)
        self.assertTrue(release_notes_show_after_update(db))

    def test_suppressed_update_is_marked_seen_for_the_next_enabled_release(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Database(Path(temp_dir.name) / "noteck.db")
        db.initialize()
        db.set_setting(LAST_SEEN_RELEASE_KEY, "1.3.0")
        set_release_notes_show_after_update(db, False)

        self.assertFalse(
            startup_release_notes_required(
                db,
                latest_release="1.4.0",
                database_existed=True,
            )
        )
        self.assertEqual(db.get_setting(LAST_SEEN_RELEASE_KEY), "1.4.0")

    def test_force_marker_overrides_matching_release_and_is_consumable(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        marker = Path(temp_dir.name) / ".force_release_notes_on_startup"
        marker.write_text("test", encoding="utf-8")

        self.assertTrue(force_release_notes_requested(marker))
        self.assertTrue(
            should_show_release_notes(
                latest_release="1.3.0",
                last_seen_release="1.3.0",
                database_existed=True,
                forced=True,
            )
        )

        clear_force_release_notes(marker)

    def test_startup_state_persists_fresh_install_and_displayed_update(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Database(Path(temp_dir.name) / "noteck.db")
        db.initialize()

        self.assertFalse(
            startup_release_notes_required(
                db,
                latest_release="1.3.0",
                database_existed=False,
            )
        )
        self.assertEqual(db.get_setting(LAST_SEEN_RELEASE_KEY), "1.3.0")

        self.assertTrue(
            startup_release_notes_required(
                db,
                latest_release="1.4.0",
                database_existed=True,
            )
        )
        marker = Path(temp_dir.name) / ".force_release_notes_on_startup"
        marker.write_text("test", encoding="utf-8")
        mark_release_notes_seen(
            db,
            release="1.4.0",
            force_sentinel_path=marker,
        )
        self.assertEqual(db.get_setting(LAST_SEEN_RELEASE_KEY), "1.4.0")
        self.assertFalse(marker.exists())
        self.assertFalse(force_release_notes_requested(marker))
        # Consuming an already absent marker remains safe and idempotent.
        clear_force_release_notes(marker)

    def test_repository_changelog_uses_supported_format(self) -> None:
        document = load_release_notes()
        self.assertEqual(document.latest.version, "1.3.0")
        self.assertIn(
            "docs/release-notes-assets/notion_anki_colored_blocks_visualisation.png",
            document.latest.markdown,
        )
        image_path = document.source_path.parent / "docs" / "release-notes-assets" / (
            "notion_anki_colored_blocks_visualisation.png"
        )
        self.assertTrue(image_path.is_file())


class ReleaseNotesDeploymentTests(unittest.TestCase):
    """Keep the canonical changelog wired into every packaging path."""

    def test_deploy_scripts_bundle_changelog_and_assets(self) -> None:
        root = Path(__file__).resolve().parents[1]
        scripts = (
            root / "deploy" / "create_noteck_zip.ps1",
            root / "deploy" / "deploy_anki_addon.ps1",
            root / "deploy" / "deploy_anki_addon_test.ps1",
        )
        for script in scripts:
            content = script.read_text(encoding="utf-8")
            self.assertIn('Join-Path $ProjectRoot "CHANGELOG.md"', content, script.name)
            self.assertIn('"docs\\release-notes-assets"', content, script.name)
            self.assertIn("Copy-Item", content, script.name)

    def test_test_deploy_creates_one_shot_marker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "deploy" / "deploy_anki_addon_test.ps1").read_text(encoding="utf-8")
        self.assertIn('Join-Path $Dest ".force_release_notes_on_startup"', script)
        self.assertIn("Set-Content", script)
