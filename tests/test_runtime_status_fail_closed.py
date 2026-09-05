import unittest
from datetime import datetime, timezone

from scripts.status import build_runtime_status, derive_source_health


class RuntimeStatusFailClosedTests(unittest.TestCase):
    @staticmethod
    def _source() -> dict:
        return {
            "sources": {
                "mf": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-09-04T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-09-04T00:00:00Z",
                    "staleAfterHours": 744,
                    "message": None,
                },
                "gus": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-09-04T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-09-04T00:00:00Z",
                    "staleAfterHours": 744,
                    "message": None,
                },
                "nbp": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-09-04T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-09-04T00:00:00Z",
                    "staleAfterHours": 168,
                    "message": None,
                },
            }
        }

    def test_future_last_success_timestamp_is_unavailable(self) -> None:
        item = {
            "status": "FRESH",
            "lastSuccessAt": "2026-09-05T00:00:00Z",
            "staleAfterHours": 168,
        }

        self.assertEqual(
            "UNAVAILABLE",
            derive_source_health(
                item,
                datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
            ),
        )

    def test_future_timestamp_does_not_block_complete_runtime_status(self) -> None:
        source = self._source()
        source["sources"]["mf"]["lastAttemptAt"] = "2026-09-05T00:00:00Z"
        source["sources"]["mf"]["lastSuccessAt"] = "2026-09-05T00:00:00Z"
        source["sources"]["mf"]["message"] = "clock skew"

        status = build_runtime_status(
            "revision-a",
            source,
            datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        )

        self.assertEqual("UNAVAILABLE", status["sources"]["mf"]["status"])
        self.assertEqual("FRESH", status["sources"]["gus"]["status"])
        self.assertEqual("FRESH", status["sources"]["nbp"]["status"])

    def test_modified_freshness_threshold_is_unavailable_and_normalized(self) -> None:
        source = self._source()
        source["sources"]["nbp"]["staleAfterHours"] = 1680

        status = build_runtime_status(
            "revision-a",
            source,
            datetime(2026, 9, 5, 8, tzinfo=timezone.utc),
        )

        nbp = status["sources"]["nbp"]
        self.assertEqual("UNAVAILABLE", nbp["status"])
        self.assertEqual(168, nbp["staleAfterHours"])
        self.assertIn("expected 168, got 1680", nbp["message"])
        self.assertEqual("FRESH", status["sources"]["mf"]["status"])
        self.assertEqual("FRESH", status["sources"]["gus"]["status"])


if __name__ == "__main__":
    unittest.main()
