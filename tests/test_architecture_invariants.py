from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_immutable_snapshots, pipeline, update
from scripts.sources import PRODUCT_RULES

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DIST_V2 = (
    "catalog-v2.json",
    "product-definitions-v2.json",
    "gus-cpi-v2.json",
    "nbp-reference-rates-v2.json",
)


class ArchitectureInvariantTests(unittest.TestCase):
    def test_python_product_rules_are_loaded_from_canonical_documents(self) -> None:
        products = pipeline.load_product_definitions()
        latest = {}
        for document in products:
            product_type = document["productType"]
            previous = latest.get(product_type)
            if previous is None or document["rulesRevision"] > previous["rulesRevision"]:
                latest[product_type] = document

        self.assertEqual(set(latest), set(PRODUCT_RULES))
        for product_type, rules in PRODUCT_RULES.items():
            document = latest[product_type]
            self.assertEqual(document["id"], rules.id)
            self.assertEqual(document["maturityMonths"], rules.maturity_months)
            self.assertEqual(document["interestPeriodMonths"], rules.interest_period_months)
            self.assertEqual(document["rateModel"], rules.rate_model)
            self.assertEqual(document["capitalizationRule"], rules.capitalization_rule)
            self.assertEqual(document["interestPaymentRule"], rules.interest_payment_rule)
            self.assertEqual(document["accrualRule"], rules.accrual_rule)

    def test_active_v2_dist_representations_do_not_exist(self) -> None:
        for filename in ACTIVE_DIST_V2:
            self.assertFalse((ROOT / "dist" / filename).exists(), filename)
        self.assertTrue((ROOT / "dist" / "catalog-v1.json").is_file())
        self.assertTrue((ROOT / "dist" / "reference-data-v1.json").is_file())

    def test_manifest_hash_detects_snapshot_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(ROOT / "publication", temp_root / "publication")
            latest = json.loads(
                (temp_root / "publication" / "v1" / "latest.json").read_text(encoding="utf-8")
            )
            catalog = (
                temp_root
                / "publication"
                / "v1"
                / "snapshots"
                / latest["datasetRevision"]
                / "catalog.json"
            )
            catalog.write_bytes(catalog.read_bytes() + b"\n")
            with patch.object(check_immutable_snapshots, "ROOT", temp_root):
                violations = check_immutable_snapshots.archive_integrity_violations()
            self.assertTrue(any("SHA-256 mismatch" in item for item in violations))

    def test_content_addressed_evidence_hash_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "evidence.bin"
            path.write_bytes(b"official")
            wrong = hashlib.sha256(b"tampered").hexdigest()
            with self.assertRaisesRegex(ValueError, "evidence SHA-256 mismatch"):
                pipeline._verify_content_addressed_file(path, wrong, "test evidence")

    def test_gus_evidence_is_compressed_but_addressed_by_raw_content(self) -> None:
        raw = b'[{"wartosc":103.2}]\n'
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_data = Path(temp_dir) / "data"
            with patch.object(update, "DATA", temp_data):
                bundle_digest = update._write_gus_evidence(
                    [("https://api-sdp.stat.gov.pl/api/example", raw)]
                )

            artifact = temp_data / "sources" / "gus" / f"{digest}.json.gz"
            self.assertEqual(raw, gzip.decompress(artifact.read_bytes()))
            self.assertFalse((temp_data / "sources" / "gus" / f"{digest}.json").exists())

            manifest = json.loads(
                (
                    temp_data
                    / "sources"
                    / "gus"
                    / f"{bundle_digest}.manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("2.0", manifest["schemaVersion"])
            self.assertEqual("gzip", manifest["responses"][0]["contentEncoding"])
            self.assertEqual(digest, manifest["responses"][0]["sha256"])

    def test_compressed_content_addressed_evidence_hash_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "evidence.json.gz"
            path.write_bytes(gzip.compress(b"official", mtime=0))
            wrong = hashlib.sha256(b"tampered").hexdigest()
            with self.assertRaisesRegex(ValueError, "evidence SHA-256 mismatch"):
                pipeline._verify_gzip_content_addressed_file(
                    path, wrong, "test compressed evidence"
                )

    def test_offline_builder_is_deterministic(self) -> None:
        first = pipeline.build_publication()
        second = pipeline.build_publication()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
