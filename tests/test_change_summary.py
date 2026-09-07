from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from scripts.sources import NBP_RATES_URL


ROOT = Path(__file__).resolve().parents[1]
LEGACY_NBP_PAGE = "https://nbp.pl/podstawowe-stopy-procentowe-archiwum/"


class ChangeSummaryTests(unittest.TestCase):
    def test_script_uses_canonical_machine_readable_nbp_source(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/change_summary.py"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )

        self.assertIn(f"- NBP source: {NBP_RATES_URL}", result.stdout)
        self.assertNotIn(f"- NBP source: {LEGACY_NBP_PAGE}", result.stdout)

    def test_script_does_not_claim_pull_request_gate_already_passed(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/change_summary.py"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )

        self.assertIn("The required `Validate data` pull-request gate runs separately", result.stdout)
        self.assertNotIn("golden-fixture and immutable-snapshot checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
