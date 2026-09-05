import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "source-status-v1.schema.json").read_text(encoding="utf-8"))


def source(status: str, last_success_at: str | None, stale_after_hours: int) -> dict:
    return {
        "status": status,
        "lastAttemptAt": "2026-09-05T06:00:00Z",
        "lastAttemptStatus": "SUCCESS",
        "lastSuccessAt": last_success_at,
        "staleAfterHours": stale_after_hours,
        "message": "test",
    }


def status_document(nbp_status: str, nbp_last_success_at: str | None) -> dict:
    verified = "2026-09-05T06:00:00Z"
    return {
        "schemaVersion": "1.0",
        "datasetRevision": "20260905T060000Z-aaaaaaaaaaaa",
        "sources": {
            "mf": source("FRESH", verified, 744),
            "gus": source("STALE", verified, 744),
            "nbp": source(nbp_status, nbp_last_success_at, 168),
        },
    }


class SourceStatusSchemaSemanticsTests(unittest.TestCase):
    def validate(self, document: dict) -> list:
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        return list(validator.iter_errors(document))

    def test_valid_status_contract_passes(self) -> None:
        verified = "2026-09-05T06:00:00Z"
        self.assertEqual([], self.validate(status_document("FRESH", verified)))

    def test_fresh_source_requires_last_success_timestamp(self) -> None:
        errors = self.validate(status_document("FRESH", None))
        self.assertTrue(errors)
        self.assertTrue(any("None is not of type 'string'" in error.message for error in errors))

    def test_partial_source_requires_last_success_timestamp(self) -> None:
        self.assertTrue(self.validate(status_document("PARTIAL", None)))

    def test_stale_source_requires_last_success_timestamp(self) -> None:
        self.assertTrue(self.validate(status_document("STALE", None)))

    def test_unavailable_source_may_have_no_last_success_timestamp(self) -> None:
        self.assertEqual([], self.validate(status_document("UNAVAILABLE", None)))

    def test_nbp_freshness_threshold_is_exact(self) -> None:
        document = status_document("FRESH", "2026-09-05T06:00:00Z")
        document["sources"]["nbp"]["staleAfterHours"] = 169
        errors = self.validate(document)
        self.assertTrue(any("168 was expected" in error.message for error in errors))

    def test_mf_and_gus_freshness_thresholds_are_exact(self) -> None:
        document = status_document("FRESH", "2026-09-05T06:00:00Z")
        document["sources"]["mf"]["staleAfterHours"] = 745
        document["sources"]["gus"]["staleAfterHours"] = 743
        errors = self.validate(document)
        self.assertGreaterEqual(sum("744 was expected" in error.message for error in errors), 2)

    def test_unexpected_source_is_rejected(self) -> None:
        document = status_document("FRESH", "2026-09-05T06:00:00Z")
        document["sources"]["other"] = copy.deepcopy(document["sources"]["nbp"])
        self.assertTrue(any("Additional properties are not allowed" in error.message for error in self.validate(document)))

    def test_dataset_revision_uses_immutable_revision_format(self) -> None:
        document = status_document("FRESH", "2026-09-05T06:00:00Z")
        document["datasetRevision"] = "revision-a"
        self.assertTrue(any("does not match" in error.message for error in self.validate(document)))


if __name__ == "__main__":
    unittest.main()
