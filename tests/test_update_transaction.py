from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import update
from scripts.sources import SourceError


class ManagedTreeTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Update Transaction Tests")
        for directory in update.MANAGED_PATHS:
            path = self.root / directory / "tracked.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{directory}-baseline\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline")

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

    def test_live_update_refuses_dirty_managed_tree(self) -> None:
        (self.root / "data" / "tracked.txt").write_text("local work\n", encoding="utf-8")
        with patch.object(update, "ROOT", self.root):
            with self.assertRaisesRegex(SourceError, "requires a clean managed tree"):
                update._require_clean_managed_tree()

    def test_rollback_restores_tracked_files_and_removes_generated_untracked_files(self) -> None:
        (self.root / "data" / "tracked.txt").write_text("partial refresh\n", encoding="utf-8")
        generated = self.root / "publication" / "generated.json"
        generated.write_text("partial\n", encoding="utf-8")

        with patch.object(update, "ROOT", self.root):
            update._rollback_managed_tree()

        self.assertEqual(
            "data-baseline\n",
            (self.root / "data" / "tracked.txt").read_text(encoding="utf-8"),
        )
        self.assertFalse(generated.exists())
        self.assertEqual("", self.git("status", "--short"))

    def test_live_source_failure_rolls_back_before_returning_failure(self) -> None:
        with (
            patch.object(update, "_require_clean_managed_tree"),
            patch.object(update, "run_live", side_effect=SourceError("GUS failed")),
            patch.object(update, "_rollback_managed_tree") as rollback,
            patch("sys.argv", ["update.py"]),
        ):
            result = update.main()

        self.assertEqual(1, result)
        rollback.assert_called_once_with()

    def test_build_failure_after_successful_fetch_rolls_back(self) -> None:
        with (
            patch.object(update, "_require_clean_managed_tree"),
            patch.object(update, "run_live"),
            patch.object(update, "build_dist", side_effect=ValueError("publication failed")),
            patch.object(update, "_rollback_managed_tree") as rollback,
            patch("sys.argv", ["update.py"]),
        ):
            result = update.main()

        self.assertEqual(1, result)
        rollback.assert_called_once_with()

    def test_future_as_of_is_rejected(self) -> None:
        with self.assertRaisesRegex(SourceError, "cannot be in the future"):
            update._validated_verification_date("2026-09-07", today=date(2026, 9, 6))

    def test_historical_as_of_remains_supported(self) -> None:
        self.assertEqual(
            "2026-09-05",
            update._validated_verification_date("2026-09-05", today=date(2026, 9, 6)),
        )

    def test_production_cli_rejects_skip_cross_check_before_live_update(self) -> None:
        with (
            patch.object(update, "_require_clean_managed_tree") as clean_tree,
            patch("sys.argv", ["update.py", "--skip-cross-check"]),
        ):
            result = update.main()

        self.assertEqual(1, result)
        clean_tree.assert_not_called()

    def test_offline_failure_does_not_rewrite_working_tree_via_transaction_rollback(self) -> None:
        with (
            patch.object(update, "build_dist", side_effect=ValueError("offline validation failed")),
            patch.object(update, "_rollback_managed_tree") as rollback,
            patch("sys.argv", ["update.py", "--offline"]),
        ):
            result = update.main()

        self.assertEqual(1, result)
        rollback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
