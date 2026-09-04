import unittest
from datetime import datetime, timezone

from scripts.status import derive_source_health


class RuntimeStatusFailClosedTests(unittest.TestCase):
    def test_future_last_success_timestamp_is_rejected(self) -> None:
        item = {
            "status": "FRESH",
            "lastSuccessAt": "2026-09-05T00:00:00Z",
            "staleAfterHours": 168,
        }

        with self.assertRaisesRegex(ValueError, "in the future"):
            derive_source_health(
                item,
                datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
