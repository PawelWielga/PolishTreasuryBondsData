import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import scripts.status as runtime_status


class RuntimeFreshnessTests(unittest.TestCase):
    def test_sources_age_independently_from_last_success(self):
        source = {
            "sources": {
                "mf": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-09-01T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-09-01T00:00:00Z",
                    "staleAfterHours": 744,
                    "message": None,
                },
                "gus": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-07-01T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-07-01T00:00:00Z",
                    "staleAfterHours": 744,
                    "message": None,
                },
                "nbp": {
                    "status": "FRESH",
                    "lastAttemptAt": "2026-08-20T00:00:00Z",
                    "lastAttemptStatus": "SUCCESS",
                    "lastSuccessAt": "2026-08-20T00:00:00Z",
                    "staleAfterHours": 168,
                    "message": None,
                },
            }
        }
        status = runtime_status.build_runtime_status(
            "revision-a",
            source,
            datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
        )
        self.assertEqual("FRESH", status["sources"]["mf"]["status"])
        self.assertEqual("STALE", status["sources"]["gus"]["status"])
        self.assertEqual("STALE", status["sources"]["nbp"]["status"])

    def test_source_becomes_stale_at_threshold_without_a_failed_attempt_record(self):
        item = {
            "status": "FRESH",
            "lastAttemptAt": "2026-08-27T12:00:00Z",
            "lastAttemptStatus": "SUCCESS",
            "lastSuccessAt": "2026-08-27T12:00:00Z",
            "staleAfterHours": 168,
            "message": "last durable success",
        }
        self.assertEqual(
            "FRESH",
            runtime_status.derive_source_health(
                item, datetime(2026, 9, 3, 11, 59, 59, tzinfo=timezone.utc)
            ),
        )
        self.assertEqual(
            "STALE",
            runtime_status.derive_source_health(
                item, datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
            ),
        )

    def test_missing_success_is_unavailable(self):
        item = {
            "status": "FRESH",
            "lastSuccessAt": None,
            "staleAfterHours": 168,
        }
        self.assertEqual(
            "UNAVAILABLE",
            runtime_status.derive_source_health(
                item, datetime(2026, 9, 3, tzinfo=timezone.utc)
            ),
        )

    def test_partial_is_preserved_only_while_not_stale(self):
        item = {
            "status": "PARTIAL",
            "lastSuccessAt": "2026-09-03T00:00:00Z",
            "staleAfterHours": 24,
        }
        self.assertEqual(
            "PARTIAL",
            runtime_status.derive_source_health(
                item, datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
            ),
        )
        self.assertEqual(
            "STALE",
            runtime_status.derive_source_health(
                item, datetime(2026, 9, 4, 0, tzinfo=timezone.utc)
            ),
        )

    def test_runtime_render_changes_only_status_and_keeps_dataset_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            publication = root / "publication" / "v1"
            schemas = root / "schemas"
            data.mkdir(parents=True)
            publication.mkdir(parents=True)
            schemas.mkdir(parents=True)

            source = {
                "sources": {
                    name: {
                        "status": "FRESH",
                        "lastAttemptAt": "2026-08-01T00:00:00Z",
                        "lastAttemptStatus": "SUCCESS",
                        "lastSuccessAt": "2026-08-01T00:00:00Z",
                        "staleAfterHours": 168 if name == "nbp" else 744,
                        "message": None,
                    }
                    for name in ("mf", "gus", "nbp")
                }
            }
            (data / "source-status.json").write_text(json.dumps(source), encoding="utf-8")
            selected_revision = "20260903T000000Z-aaaaaaaaaaaa"
            latest = {
                "schemaVersion": "1.0",
                "datasetRevision": selected_revision,
                "manifest": f"snapshots/{selected_revision}/manifest.json",
            }
            latest_path = publication / "latest.json"
            latest_path.write_text(json.dumps(latest), encoding="utf-8")
            latest_before = latest_path.read_bytes()

            real_schema = Path(__file__).resolve().parents[1] / "schemas" / "source-status-v1.schema.json"
            (schemas / "source-status-v1.schema.json").write_bytes(real_schema.read_bytes())

            with (
                patch.object(runtime_status, "DATA", data),
                patch.object(runtime_status, "PUBLICATION", publication),
                patch.object(runtime_status, "SCHEMAS", schemas),
            ):
                rendered = runtime_status.render_public_status(
                    datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
                )

            self.assertEqual(latest_before, latest_path.read_bytes())
            self.assertEqual(selected_revision, rendered["datasetRevision"])
            self.assertEqual("STALE", rendered["sources"]["nbp"]["status"])


if __name__ == "__main__":
    unittest.main()
