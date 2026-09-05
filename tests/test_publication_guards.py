from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_generated_tree, check_immutable_snapshots, update
from scripts.status import SOURCE_STALE_AFTER_HOURS


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

    def write_source_status(self, overrides: dict[str, int] | None = None) -> None:
        thresholds = {**SOURCE_STALE_AFTER_HOURS, **(overrides or {})}
        self.write(
            "data/source-status.json",
            json.dumps(
                {
                    "sources": {
                        name: {"staleAfterHours": hours}
                        for name, hours in thresholds.items()
                    }
                },
                indent=2,
            )
            + "\n",
        )

    def write_mf_series_with_artifact(
        self,
        content: str = "official workbook bytes\n",
        *,
        artifact_content: str | None = None,
    ) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.write(
            "data/series/ROR/ROR0927/terms-v1.json",
            json.dumps(
                {
                    "provenance": {
                        "primary": {
                            "sha256": digest,
                        }
                    }
                },
                indent=2,
            )
            + "\n",
        )
        if artifact_content is not None:
            self.write(f"data/sources/mf/{digest}.xls", artifact_content)
        return digest

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


class ImmutableSnapshotArchiveTests(GitRepositoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_snapshot("rev-reviewed")
        self.write_latest("rev-reviewed")
        self.creation = self.commit_all("publish reviewed snapshot")

    def violations(self) -> list[str]:
        with patch.object(check_immutable_snapshots, "ROOT", self.root):
            return check_immutable_snapshots.archive_integrity_violations()

    def test_unchanged_archive_matches_first_reviewed_history(self) -> None:
        self.assertEqual([], self.violations())

    def test_old_snapshot_mutation_remains_rejected_after_unrelated_later_commit(self) -> None:
        self.write("publication/v1/snapshots/rev-reviewed/manifest.json", "mutated\n")
        self.commit_all("bypass immutable snapshot guard")
        self.write("README.md", "later unrelated change\n")
        self.commit_all("later unrelated change")

        violations = self.violations()

        self.assertTrue(any("manifest.json" in line and self.creation in line for line in violations))

    def test_old_snapshot_extension_remains_rejected_after_unrelated_later_commit(self) -> None:
        self.write("publication/v1/snapshots/rev-reviewed/notes.txt", "unexpected\n")
        self.commit_all("bypass immutable snapshot guard")
        self.write("README.md", "later unrelated change\n")
        self.commit_all("later unrelated change")

        violations = self.violations()

        self.assertTrue(any("unexpected entries: notes.txt" in line for line in violations))
        self.assertTrue(any("notes.txt" in line and self.creation in line for line in violations))

    def test_old_snapshot_file_deletion_remains_rejected_after_unrelated_later_commit(self) -> None:
        (self.root / "publication/v1/snapshots/rev-reviewed/catalog.json").unlink()
        self.commit_all("bypass immutable snapshot guard")
        self.write("README.md", "later unrelated change\n")
        self.commit_all("later unrelated change")

        violations = self.violations()

        self.assertTrue(any("missing canonical files: catalog.json" in line for line in violations))
        self.assertTrue(any("catalog.json" in line and self.creation in line for line in violations))

    def test_entire_old_snapshot_deletion_remains_rejected_after_unrelated_later_commit(self) -> None:
        shutil.rmtree(self.root / "publication/v1/snapshots/rev-reviewed")
        self.commit_all("bypass immutable snapshot guard")
        self.write("README.md", "later unrelated change\n")
        self.commit_all("later unrelated change")

        violations = self.violations()

        self.assertTrue(
            any(
                "rev-reviewed" in line
                and "historical snapshot directory is missing from current tree" in line
                for line in violations
            )
        )


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

    def test_source_freshness_thresholds_match_contract(self) -> None:
        self.write_source_status()

        with patch.object(check_generated_tree, "ROOT", self.root):
            self.assertEqual([], check_generated_tree.source_status_contract_violations())

    def test_modified_nbp_freshness_threshold_is_rejected(self) -> None:
        self.write_source_status({"nbp": 1680})

        with patch.object(check_generated_tree, "ROOT", self.root):
            violations = check_generated_tree.source_status_contract_violations()

        self.assertTrue(any("nbp.staleAfterHours must be 168" in line for line in violations))

    def test_unexpected_source_status_entry_is_rejected(self) -> None:
        self.write_source_status({"other": 24})

        with patch.object(check_generated_tree, "ROOT", self.root):
            violations = check_generated_tree.source_status_contract_violations()

        self.assertTrue(any("unexpected sources: other" in line for line in violations))

    def test_referenced_mf_source_artifact_is_verified(self) -> None:
        content = "official workbook bytes\n"
        self.write_mf_series_with_artifact(content, artifact_content=content)

        with patch.object(check_generated_tree, "ROOT", self.root):
            self.assertEqual([], check_generated_tree.mf_source_artifact_violations())

    def test_missing_referenced_mf_source_artifact_is_rejected(self) -> None:
        digest = self.write_mf_series_with_artifact()

        with patch.object(check_generated_tree, "ROOT", self.root):
            violations = check_generated_tree.mf_source_artifact_violations()

        self.assertTrue(any(digest in line and "missing" in line for line in violations))

    def test_tampered_mf_source_artifact_is_rejected(self) -> None:
        digest = self.write_mf_series_with_artifact(artifact_content="tampered\n")

        with patch.object(check_generated_tree, "ROOT", self.root):
            violations = check_generated_tree.mf_source_artifact_violations()

        self.assertTrue(any(digest in line and "SHA-256 mismatch" in line for line in violations))

    def test_unreferenced_mf_source_artifact_is_rejected(self) -> None:
        content = "orphan workbook\n"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.write(f"data/sources/mf/{digest}.xls", content)

        with patch.object(check_generated_tree, "ROOT", self.root):
            violations = check_generated_tree.mf_source_artifact_violations()

        self.assertTrue(any("unreferenced or unexpected entries" in line for line in violations))

    def test_update_check_uses_strong_guard_and_detects_untracked_output(self) -> None:
        self.write_source_status()
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
