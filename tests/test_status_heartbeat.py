import unittest

from scripts.prune_status_heartbeat import (
    compact_status_heartbeat,
    heartbeat_due,
    is_status_only_candidate,
)


def source_item(last_success: str, stale_after_hours: int) -> dict:
    return {
        "status": "FRESH",
        "lastAttemptAt": last_success,
        "lastAttemptStatus": "SUCCESS",
        "lastSuccessAt": last_success,
        "staleAfterHours": stale_after_hours,
        "message": "previous durable verification",
    }


def successful_candidate(previous: dict, verified_at: str) -> dict:
    candidate = dict(previous)
    candidate.update(
        {
            "status": "FRESH",
            "lastAttemptAt": verified_at,
            "lastAttemptStatus": "SUCCESS",
            "lastSuccessAt": verified_at,
            "message": "Update result: 0",
        }
    )
    return candidate


class StatusHeartbeatTests(unittest.TestCase):
    def test_heartbeat_is_not_due_before_three_quarters_of_freshness_window(self):
        previous = source_item("2026-09-01T00:00:00Z", 168)
        candidate = successful_candidate(previous, "2026-09-06T05:59:59Z")

        self.assertFalse(heartbeat_due(previous, candidate))

    def test_heartbeat_is_due_at_three_quarters_of_freshness_window(self):
        previous = source_item("2026-09-01T00:00:00Z", 168)
        candidate = successful_candidate(previous, "2026-09-06T06:00:00Z")

        self.assertTrue(heartbeat_due(previous, candidate))

    def test_compaction_keeps_only_sources_whose_heartbeat_is_due(self):
        previous = {
            "sources": {
                "mf": source_item("2026-09-01T00:00:00Z", 744),
                "gus": source_item("2026-09-01T00:00:00Z", 744),
                "nbp": source_item("2026-09-01T00:00:00Z", 168),
            }
        }
        candidate = {
            "sources": {
                name: successful_candidate(item, "2026-09-06T10:00:00Z")
                for name, item in previous["sources"].items()
            }
        }

        compacted = compact_status_heartbeat(previous, candidate)

        self.assertEqual(previous["sources"]["mf"], compacted["sources"]["mf"])
        self.assertEqual(previous["sources"]["gus"], compacted["sources"]["gus"])
        self.assertEqual(candidate["sources"]["nbp"], compacted["sources"]["nbp"])

    def test_status_only_candidate_includes_derived_public_status(self):
        self.assertTrue(
            is_status_only_candidate(
                {"data/source-status.json", "publication/v1/status.json"}
            )
        )
        self.assertTrue(is_status_only_candidate({"data/source-status.json"}))

    def test_substantive_managed_change_disables_heartbeat_compaction(self):
        self.assertFalse(
            is_status_only_candidate(
                {
                    "data/source-status.json",
                    "publication/v1/status.json",
                    "data/reference/nbp-reference-rates.json",
                }
            )
        )
        self.assertFalse(is_status_only_candidate({"publication/v1/status.json"}))

    def test_policy_change_is_never_hidden_as_a_heartbeat(self):
        previous = {"sources": {"nbp": source_item("2026-09-01T00:00:00Z", 168)}}
        changed = successful_candidate(previous["sources"]["nbp"], "2026-09-02T00:00:00Z")
        changed["staleAfterHours"] = 336
        candidate = {"sources": {"nbp": changed}}

        self.assertEqual(candidate, compact_status_heartbeat(previous, candidate))

    def test_failed_attempt_is_never_hidden(self):
        previous = {"sources": {"nbp": source_item("2026-09-01T00:00:00Z", 168)}}
        failed = dict(previous["sources"]["nbp"])
        failed.update(
            {
                "status": "STALE",
                "lastAttemptAt": "2026-09-02T00:00:00Z",
                "lastAttemptStatus": "FAILED",
                "message": "upstream failed",
            }
        )
        candidate = {"sources": {"nbp": failed}}

        self.assertEqual(candidate, compact_status_heartbeat(previous, candidate))


if __name__ == "__main__":
    unittest.main()
