from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
SNAPSHOTS = ROOT / "publication" / "v1" / "snapshots"
ARTIFACT_SCHEMAS = {
    "catalog.json": "catalog-v2.schema.json",
    "product-definitions.json": "product-definitions-v2.schema.json",
    "gus-cpi.json": "gus-cpi-v2.schema.json",
    "nbp-reference-rates.json": "nbp-reference-rates-v2.schema.json",
}


def validation_errors(document: dict, schema: dict) -> list:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return list(validator.iter_errors(document))


class ImmutableSchemaCompatibilityTests(unittest.TestCase):
    def test_every_retained_manifest_satisfies_current_v1_contract(self) -> None:
        schema = json.loads(
            (SCHEMAS / "snapshot-manifest-v1.schema.json").read_text(encoding="utf-8")
        )
        for snapshot in sorted(path for path in SNAPSHOTS.iterdir() if path.is_dir()):
            with self.subTest(snapshot=snapshot.name):
                document = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
                errors = validation_errors(document, schema)
                self.assertEqual([], errors, [error.message for error in errors])

    def test_every_retained_artifact_satisfies_its_current_versioned_schema(self) -> None:
        schemas = {
            filename: json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
            for filename, schema_name in ARTIFACT_SCHEMAS.items()
        }
        for snapshot in sorted(path for path in SNAPSHOTS.iterdir() if path.is_dir()):
            for filename, schema in schemas.items():
                with self.subTest(snapshot=snapshot.name, filename=filename):
                    document = json.loads((snapshot / filename).read_text(encoding="utf-8"))
                    errors = validation_errors(document, schema)
                    self.assertEqual([], errors, [error.message for error in errors])


if __name__ == "__main__":
    unittest.main()
