from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_generated_tree, check_immutable_snapshots


class UpdaterCandidateGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Updater Candidate Tests")

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

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def write_latest(self, revision: str) -> None:
        self.write(
            "publication/v1/latest.json",
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "datasetRevision": revision,
                    "manifest": f"snapshots/{revision}/manifest.json",
                }
            )
            + "\n",
        )

    def write_snapshot(self, revision: str) -> None:
        for filename in check_immutable_snapshots.EXPECTED_SNAPSHOT_FILES:
            self.write(f"publication/v1/snapshots/{revision}/{filename}")

    def test_staged_candidate_is_baseline_but_later_rebuild_mutation_is_rejected(self) -> None:
        self.write("data/value.json", '{"value": 1}\n')
        self.commit_all("baseline")

        self.write("data/value.json", '{"value": 2}\n')
        self.git("add", "--", "data/value.json")
        with patch.object(check_generated_tree, "ROOT", self.root):
            self.assertEqual([], check_generated_tree.generated_tree_changes(include_cached=False))
            self.assertEqual(["data/value.json"], check_generated_tree.generated_tree_changes())

        self.write("data/value.json", '{"value": 3}\n')
        with patch.object(check_generated_tree, "ROOT", self.root):
            self.assertEqual(
                ["data/value.json"],
                check_generated_tree.generated_tree_changes(include_cached=False),
            )

    def test_temporary_candidate_commit_can_validate_new_selected_snapshot(self) -> None:
        self.write_snapshot("rev-reviewed")
        self.write_latest("rev-reviewed")
        base = self.commit_all("reviewed baseline")

        self.write_snapshot("rev-new")
        self.write_latest("rev-new")
        self.git("add", "-A", "--", "publication")
        tree = self.git("write-tree")
        candidate = self.git("commit-tree", tree, "-p", base, "-m", "candidate")

        with patch.object(check_immutable_snapshots, "ROOT", self.root):
            self.assertEqual([], check_immutable_snapshots.archive_integrity_violations(candidate))
            self.assertEqual(
                [],
                check_immutable_snapshots.immutable_snapshot_violations(base, candidate),
            )


if __name__ == "__main__":
    unittest.main()