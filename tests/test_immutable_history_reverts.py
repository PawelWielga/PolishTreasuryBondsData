from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_immutable_snapshots


class ImmutableHistoryRevertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Immutable History Tests")
        self.write_snapshot("rev-reviewed")
        self.write_latest("rev-reviewed")
        self.creation = self.commit_all("publish immutable snapshot")

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

    def write_snapshot(self, revision: str) -> None:
        for filename in check_immutable_snapshots.EXPECTED_SNAPSHOT_FILES:
            self.write(f"publication/v1/snapshots/{revision}/{filename}")

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

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def violations(self) -> list[str]:
        with patch.object(check_immutable_snapshots, "ROOT", self.root):
            return check_immutable_snapshots.archive_integrity_violations()

    def test_mutation_then_exact_revert_remains_rejected(self) -> None:
        path = "publication/v1/snapshots/rev-reviewed/catalog.json"
        self.write(path, "mutated\n")
        tamper = self.commit_all("privileged immutable mutation")
        self.write(path, "{}\n")
        self.commit_all("restore original snapshot bytes")

        violations = self.violations()

        self.assertTrue(
            any(path in item and tamper in item for item in violations),
            violations,
        )

    def test_temporary_extra_file_then_removal_remains_rejected(self) -> None:
        path = "publication/v1/snapshots/rev-reviewed/temporary.txt"
        self.write(path, "temporary\n")
        tamper = self.commit_all("temporarily extend immutable snapshot")
        (self.root / path).unlink()
        self.commit_all("remove temporary immutable content")

        violations = self.violations()

        self.assertTrue(
            any(path in item and tamper in item for item in violations),
            violations,
        )

    def test_delete_then_restore_entire_snapshot_remains_rejected(self) -> None:
        snapshot = self.root / "publication/v1/snapshots/rev-reviewed"
        shutil.rmtree(snapshot)
        tamper = self.commit_all("temporarily delete immutable snapshot")
        self.write_snapshot("rev-reviewed")
        self.commit_all("restore immutable snapshot")

        violations = self.violations()

        self.assertTrue(
            any("rev-reviewed" in item and tamper in item for item in violations),
            violations,
        )


if __name__ == "__main__":
    unittest.main()
