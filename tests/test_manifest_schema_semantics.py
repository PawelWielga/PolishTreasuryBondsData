from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "snapshot-manifest-v1.schema.json").read_text(encoding="utf-8")
)


def current_manifest() -> dict:
    latest = json.loads(
        (ROOT / "publication" / "v1" / "latest.json").read_text(encoding="utf-8")
    )
    return json.loads(
        (ROOT / "publication" / "v1" / latest["manifest"]).read_text(encoding="utf-8")
    )


class SnapshotManifestSchemaSemanticsTests(unittest.TestCase):
    def validate(self, document: dict) -> list:
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        return list(validator.iter_errors(document))

    def test_current_manifest_satisfies_public_contract(self) -> None:
        self.assertEqual([], self.validate(current_manifest()))

    def test_unexpected_logical_file_is_rejected(self) -> None:
        document = current_manifest()
        document["files"]["extra.json"] = copy.deepcopy(document["files"]["catalog.json"])

        self.assertTrue(
            any("Additional properties are not allowed" in error.message for error in self.validate(document))
        )

    def test_manifest_file_key_must_match_path(self) -> None:
        document = current_manifest()
        document["files"]["catalog.json"]["path"] = "gus-cpi.json"

        self.assertTrue(any("catalog.json" in error.message for error in self.validate(document)))

    def test_manifest_files_must_advertise_supported_schema_version(self) -> None:
        document = current_manifest()
        document["files"]["catalog.json"]["schemaVersion"] = "9.0"

        self.assertTrue(any(error.validator == "const" for error in self.validate(document)))

    def test_mf_provenance_sha256_is_structurally_validated(self) -> None:
        document = current_manifest()
        document["provenance"]["mf"]["sha256"] = "not-a-sha256"

        self.assertTrue(any("does not match" in error.message for error in self.validate(document)))

    def test_legacy_nbp_origin_is_valid_without_current_url(self) -> None:
        legacy = current_manifest()
        verified_at = legacy["provenance"]["nbp"]["verifiedAt"]
        legacy["provenance"]["nbp"] = {
            "publisher": "NBP",
            "url": "https://nbp.pl/podstawowe-stopy-procentowe-archiwum/",
            "verifiedAt": verified_at,
        }

        self.assertEqual([], self.validate(legacy))

    def test_machine_readable_nbp_origin_requires_exact_current_url(self) -> None:
        current = current_manifest()
        self.assertEqual([], self.validate(current))

        missing_current_url = copy.deepcopy(current)
        missing_current_url["provenance"]["nbp"].pop("currentUrl", None)
        self.assertTrue(self.validate(missing_current_url))

        wrong_current_url = copy.deepcopy(current)
        wrong_current_url["provenance"]["nbp"]["currentUrl"] = "https://static.nbp.pl/other.xml"
        self.assertTrue(self.validate(wrong_current_url))

    def test_legacy_nbp_origin_rejects_current_url_field(self) -> None:
        legacy = current_manifest()
        verified_at = legacy["provenance"]["nbp"]["verifiedAt"]
        legacy["provenance"]["nbp"] = {
            "publisher": "NBP",
            "url": "https://nbp.pl/podstawowe-stopy-procentowe-archiwum/",
            "currentUrl": "https://static.nbp.pl/dane/stopy/stopy_procentowe.xml",
            "verifiedAt": verified_at,
        }

        self.assertTrue(self.validate(legacy))

    def test_catalog_coverage_requires_every_supported_family(self) -> None:
        document = current_manifest()
        document["coverage"]["catalog"].pop("OTS")

        self.assertTrue(any("OTS" in error.message and "required" in error.message for error in self.validate(document)))

    def test_reference_coverage_count_must_be_a_positive_integer(self) -> None:
        document = current_manifest()
        document["coverage"]["gusCpi"]["observationCount"] = "151"

        self.assertTrue(any("is not of type 'integer'" in error.message for error in self.validate(document)))

    def test_unexpected_top_level_manifest_property_is_rejected(self) -> None:
        document = current_manifest()
        document["unexpected"] = True

        self.assertTrue(
            any("Additional properties are not allowed" in error.message for error in self.validate(document))
        )


if __name__ == "__main__":
    unittest.main()
