import unittest
from datetime import datetime, timezone

from scripts.status import build_runtime_status, derive_source_health


class RuntimeStatusFailClosedTests(unittest.TestCase):
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
        source = {
            "sources": {
                "mf": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-09-05T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-09-05T00:00:00Z",
                    "staleAfterHours": 744,
                    "message": "clock skew",
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

        status = build_runtime_status(
            "revision-a",
            source,
            datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        )

        self.assertEqual("UNAVAILABLE", status["sources"]["mf"]["status"])
        self.assertEqual("FRESH", status["sources"]["gus"]["status"])
        self.assertEqual("FRESH", status["sources"]["nbp"]["status"])


if __name__ == "__main__":
    unittest.main()
