from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import check_generated_tree
from scripts.status import SOURCE_STALE_AFTER_HOURS


class SourceStatusContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "data").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_status(self, nbp: dict) -> None:
        sources = {
            name: {
                "status": "FRESH",
                "lastAttemptAt": "2026-09-05T12:00:00Z",
                "lastAttemptStatus": "SUCCESS",
                "lastSuccessAt": "2026-09-05T12:00:00Z",
                "staleAfterHours": hours,
                "message": None,
            }
            for name, hours in SOURCE_STALE_AFTER_HOURS.items()
        }
        sources["nbp"].update(nbp)
        (self.root / "data" / "source-status.json").write_text(
            json.dumps({"sources": sources}),
            encoding="utf-8",
        )

    def violations(self, as_of: datetime | None = None) -> list[str]:
        with patch.object(check_generated_tree, "ROOT", self.root):
            return check_generated_tree.source_status_contract_violations(as_of=as_of)

    def test_valid_durable_status_passes(self) -> None:
        self.write_status({})
        self.assertEqual([], self.violations())

    def test_success_timestamp_must_match_successful_attempt(self) -> None:
        self.write_status({"lastSuccessAt": "2026-09-05T13:00:00Z"})
        self.assertTrue(any("successful lastAttemptAt must equal lastSuccessAt" in item for item in self.violations()))

    def test_successful_durable_attempt_cannot_claim_stale_status(self) -> None:
        self.write_status({"status": "STALE"})
        self.assertTrue(
            any("successful durable attempt must have status FRESH or PARTIAL" in item for item in self.violations())
        )

    def test_failed_attempt_cannot_predate_last_success(self) -> None:
        self.write_status(
            {
                "status": "STALE",
                "lastAttemptStatus": "FAILED",
                "lastAttemptAt": "2026-09-05T11:00:00Z",
                "lastSuccessAt": "2026-09-05T12:00:00Z",
            }
        )
        self.assertTrue(any("failed lastAttemptAt cannot predate lastSuccessAt" in item for item in self.violations()))

    def test_failed_attempt_with_prior_success_cannot_claim_fresh_status(self) -> None:
        self.write_status(
            {
                "status": "FRESH",
                "lastAttemptStatus": "FAILED",
                "lastAttemptAt": "2026-09-05T13:00:00Z",
                "lastSuccessAt": "2026-09-05T12:00:00Z",
            }
        )
        self.assertTrue(
            any("failed durable attempt with prior success must have status STALE or PARTIAL" in item for item in self.violations())
        )

    def test_failed_attempt_without_prior_success_must_be_unavailable(self) -> None:
        self.write_status(
            {
                "status": "FRESH",
                "lastAttemptStatus": "FAILED",
                "lastAttemptAt": "2026-09-05T13:00:00Z",
                "lastSuccessAt": None,
            }
        )
        self.assertTrue(
            any("failed durable attempt without prior success must have status UNAVAILABLE" in item for item in self.violations())
        )

    def test_valid_failed_attempt_without_prior_success_passes(self) -> None:
        self.write_status(
            {
                "status": "UNAVAILABLE",
                "lastAttemptStatus": "FAILED",
                "lastAttemptAt": "2026-09-05T13:00:00Z",
                "lastSuccessAt": None,
            }
        )
        self.assertEqual([], self.violations())

    def test_never_attempted_source_cannot_claim_timestamps(self) -> None:
        self.write_status({"lastAttemptStatus": "NEVER"})
        self.assertTrue(any("lastAttemptStatus NEVER" in item for item in self.violations()))


    def test_future_success_timestamp_is_rejected(self) -> None:
        self.write_status(
            {
                "lastAttemptAt": "2026-09-06T00:00:00Z",
                "lastSuccessAt": "2026-09-06T00:00:00Z",
            }
        )
        violations = self.violations(datetime(2026, 9, 5, 23, 59, tzinfo=timezone.utc))
        self.assertTrue(any("lastAttemptAt cannot be in the future" in item for item in violations))
        self.assertTrue(any("lastSuccessAt cannot be in the future" in item for item in violations))

    def test_never_attempted_source_must_be_unavailable(self) -> None:
        self.write_status(
            {
                "status": "FRESH",
                "lastAttemptStatus": "NEVER",
                "lastAttemptAt": None,
                "lastSuccessAt": None,
            }
        )
        self.assertTrue(
            any("lastAttemptStatus NEVER must have durable status UNAVAILABLE" in item for item in self.violations())
        )


if __name__ == "__main__":
    unittest.main()
