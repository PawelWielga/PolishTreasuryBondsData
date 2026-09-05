import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "source-status-v1.schema.json").read_text(encoding="utf-8"))


def source(status: str, last_success_at: str | None) -> dict:
    return {
        "status": status,
        "lastAttemptAt": "2026-09-05T06:00:00Z",
        "lastAttemptStatus": "SUCCESS",
        "lastSuccessAt": last_success_at,
        "staleAfterHours": 168,
        "message": "test",
    }


def status_document(nbp_status: str, nbp_last_success_at: str | None) -> dict:
    verified = "2026-09-05T06:00:00Z"
    return {
        "schemaVersion": "1.0",
        "datasetRevision": "20260905T060000Z-aaaaaaaaaaaa",
        "sources": {
            "mf": source("FRESH", verified),
            "gus": source("STALE", verified),
            "nbp": source(nbp_status, nbp_last_success_at),
        },
    }


class SourceStatusSchemaSemanticsTests(unittest.TestCase):
    def validate(self, document: dict) -> list:
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        return list(validator.iter_errors(document))

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


if __name__ == "__main__":
    unittest.main()
