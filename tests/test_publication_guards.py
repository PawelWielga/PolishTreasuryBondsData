from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_generated_tree, check_immutable_snapshots, update


class GitRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Publication Guard Tests")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def write(self, path: str, content: str = "{}\n") -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def write_latest(self, revision: str) -> None:
        self.write(
            "publication/v1/latest.json",
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "datasetRevision": revision,
                    "manifest": f"snapshots/{revision}/manifest.json",
                },
                indent=2,
            )
            + "\n",
        )

    def write_snapshot(self, revision: str) -> None:
        for filename in check_immutable_snapshots.EXPECTED_SNAPSHOT_FILES:
            self.write(f"publication/v1/snapshots/{revision}/{filename}")

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")


class ImmutableSnapshotGuardTests(GitRepositoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("publication/v1/snapshots/rev-reviewed/manifest.json", "reviewed\n")
        self.write_latest("rev-reviewed")
        self.base = self.commit_all("reviewed base")

    def violations(self) -> list[str]:
        with patch.object(check_immutable_snapshots, "ROOT", self.root):
            return check_immutable_snapshots.immutable_snapshot_violations(self.base)

    def test_modifying_reviewed_snapshot_is_rejected(self) -> None:
        self.write("publication/v1/snapshots/rev-reviewed/manifest.json", "mutated\n")
        self.commit_all("mutate snapshot")
        self.assertTrue(any(line.startswith("M\t") for line in self.violations()))

    def test_deleting_reviewed_snapshot_is_rejected(self) -> None:
        (self.root / "publication/v1/snapshots/rev-reviewed/manifest.json").unlink()
        self.commit_all("delete snapshot")
        self.assertTrue(any(line.startswith("D\t") for line in self.violations()))

    def test_adding_file_to_reviewed_snapshot_is_rejected(self) -> None:
        self.write("publication/v1/snapshots/rev-reviewed/unexpected.json")
        self.commit_all("extend snapshot")
        self.assertTrue(any("unexpected.json" in line for line in self.violations()))

    def test_selected_entirely_new_snapshot_is_allowed(self) -> None:
        self.write_snapshot("rev-new")
        self.write_latest("rev-new")
        self.commit_all("add selected snapshot")
        self.assertEqual([], self.violations())

    def test_selected_new_snapshot_must_be_complete(self) -> None:
        self.write("publication/v1/snapshots/rev-new/manifest.json")
        self.write_latest("rev-new")
        self.commit_all("add incomplete selected snapshot")

        violations = self.violations()

        self.assertTrue(any("incomplete" in line for line in violations))

    def test_nested_content_in_selected_new_snapshot_is_rejected(self) -> None:
        self.write_snapshot("rev-new")
        self.write("publication/v1/snapshots/rev-new/extra/payload.json")
        self.write_latest("rev-new")
        self.commit_all("add nested snapshot content")

        violations = self.violations()

        self.assertTrue(any("extra/payload.json" in line and "unexpected path" in line for line in violations))

    def test_extra_top_level_file_in_selected_new_snapshot_is_rejected(self) -> None:
        self.write_snapshot("rev-new")
        self.write("publication/v1/snapshots/rev-new/notes.txt", "unexpected\n")
        self.write_latest("rev-new")
        self.commit_all("add unexpected snapshot file")

        violations = self.violations()

        self.assertTrue(any("notes.txt" in line and "unexpected path" in line for line in violations))

    def test_unreferenced_new_snapshot_is_rejected(self) -> None:
        self.write("publication/v1/snapshots/rev-extra/manifest.json")
        self.write("publication/v1/snapshots/rev-extra/catalog.json")
        self.commit_all("add unreferenced snapshot")

        violations = self.violations()

        self.assertTrue(any("rev-extra" in line and "not selected" in line for line in violations))

    def test_extra_new_snapshot_is_rejected_when_another_new_snapshot_is_selected(self) -> None:
        self.write_snapshot("rev-selected")
        self.write("publication/v1/snapshots/rev-extra/manifest.json")
        self.write_latest("rev-selected")
        self.commit_all("add selected and extra snapshots")

        violations = self.violations()

        self.assertFalse(any("rev-selected" in line for line in violations))
        self.assertTrue(any("rev-extra" in line and "not selected" in line for line in violations))


class GeneratedTreeGuardTests(GitRepositoryTestCase):
    def test_untracked_generated_file_is_detected(self) -> None:
        self.write("data/tracked.json")
        self.commit_all("baseline")
        self.write("publication/v1/snapshots/rev-new/manifest.json")

        with patch.object(check_generated_tree, "ROOT", self.root):
            changes = check_generated_tree.generated_tree_changes()

        self.assertIn("publication/v1/snapshots/rev-new/manifest.json", changes)

    def test_clean_generated_tree_passes(self) -> None:
        self.write("data/tracked.json")
        self.write("dist/catalog.json")
        self.commit_all("baseline")

        with patch.object(check_generated_tree, "ROOT", self.root):
            self.assertEqual([], check_generated_tree.generated_tree_changes())

    def test_update_check_uses_strong_guard_and_detects_untracked_output(self) -> None:
        self.write("data/tracked.json")
        self.commit_all("baseline")
        self.write("publication/v1/snapshots/rev-untracked/manifest.json")

        with (
            patch.object(check_generated_tree, "ROOT", self.root),
            patch.object(update, "build_dist", return_value="revision-a"),
            patch("sys.argv", ["update.py", "--offline", "--check"]),
        ):
            result = update.main()

        self.assertEqual(1, result)


if __name__ == "__main__":
    unittest.main()
