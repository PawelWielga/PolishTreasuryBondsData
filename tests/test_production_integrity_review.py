from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator, FormatChecker

from scripts import pipeline, update
from scripts.sources import (
    SourceError,
    fetch,
    validate_official_cross_check_url,
    validate_official_mf_workbook_url,
)


class _Response:
    def __init__(
        self,
        url: str,
        content: bytes = b"ok",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None


class SourceBoundaryRegressionTests(unittest.TestCase):
    def test_cross_origin_redirect_is_rejected_before_target_request(self) -> None:
        session = Mock()
        session.get.return_value = _Response(
            "https://www.gov.pl/attachment/example",
            status_code=302,
            headers={"Location": "https://evil.example/payload"},
        )
        with self.assertRaisesRegex(SourceError, "redirect left trusted origin"):
            fetch(session, "https://www.gov.pl/attachment/example", "application/octet-stream")
        self.assertEqual(1, session.get.call_count)

    def test_same_origin_redirect_is_allowed(self) -> None:
        session = Mock()
        session.get.side_effect = [
            _Response(
                "https://www.gov.pl/attachment/example",
                status_code=302,
                headers={"Location": "/attachment/canonical"},
            ),
            _Response("https://www.gov.pl/attachment/canonical", b"official"),
        ]
        self.assertEqual(
            b"official",
            fetch(session, "https://www.gov.pl/attachment/example", "application/octet-stream"),
        )
        self.assertEqual(2, session.get.call_count)

    def test_mf_url_rejects_credentials_and_nonstandard_port(self) -> None:
        for url in (
            "https://user@www.gov.pl/attachment/example",
            "https://www.gov.pl:444/attachment/example",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(SourceError, "exact official"):
                    update._validate_official_mf_workbook_url(url)

    def test_mf_url_rejects_lookalike_domain_query_and_fragment(self) -> None:
        invalid = (
            "https://www.gov.pl.evil.example/attachment/example",
            "https://www.gov.pl/attachment/example?download=1",
            "https://www.gov.pl/attachment/example#fragment",
        )
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaisesRegex(SourceError, "exact official"):
                    validate_official_mf_workbook_url(url)

    def test_cross_check_url_rejects_lookalike_domain_query_and_fragment(self) -> None:
        invalid = (
            "https://www.obligacjeskarbowe.pl.evil.example/oferta-obligacji/x/y/",
            "https://www.obligacjeskarbowe.pl/oferta-obligacji/x/y/?source=test",
            "https://www.obligacjeskarbowe.pl/oferta-obligacji/x/y/#fragment",
        )
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaisesRegex(SourceError, "exact official"):
                    validate_official_cross_check_url(url)

    def test_local_workbook_must_match_official_remote_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "mf.xls"
            workbook.write_bytes(b"local-tampered")
            with patch("scripts.update.fetch", return_value=b"official-remote"):
                with self.assertRaisesRegex(SourceError, "does not match"):
                    update.sync_mf(
                        object(),
                        "2026-09-05",
                        workbook_path=workbook,
                        workbook_url="https://www.gov.pl/attachment/example",
                        cross_check=False,
                    )

    def test_future_sale_cannot_be_outstanding_yet(self) -> None:
        series = {
            "productType": "ROR",
            "saleFrom": "2026-10-01",
            "saleTo": "2026-10-31",
        }
        self.assertFalse(update._can_still_be_outstanding(series, date(2026, 9, 5)))

    def test_live_nbp_rejects_future_effective_date(self) -> None:
        archive = [
            {"effectiveFrom": "2022-05-06", "annualRatePercent": "5.25"},
            {"effectiveFrom": "2026-09-06", "annualRatePercent": "4.00"},
        ]
        with self.assertRaisesRegex(SourceError, "future reference-rate date"):
            update._validated_nbp_observations(
                archive,
                [archive[-1].copy()],
                [],
                date(2026, 9, 5),
            )

    def test_future_as_of_is_rejected(self) -> None:
        with self.assertRaisesRegex(SourceError, "cannot be in the future"):
            update._validated_verification_date("2026-09-06", today=date(2026, 9, 5))


class ContractBoundaryRegressionTests(unittest.TestCase):
    @staticmethod
    def _latest_snapshot_document(name: str) -> dict:
        latest = pipeline.load_json(pipeline.PUBLICATION / "latest.json")
        return pipeline.load_json(
            pipeline.PUBLICATION / "snapshots" / latest["datasetRevision"] / name
        )

    def test_catalog_schema_does_not_encode_current_importer_host_policy(self) -> None:
        catalog = self._latest_snapshot_document("catalog.json")
        candidate = copy.deepcopy(catalog)
        candidate["series"][0]["provenance"]["primary"]["url"] = (
            "https://archive.example.test/official-artifact.xls"
        )
        schema = pipeline.load_json(pipeline.SCHEMAS / "catalog-v2.schema.json")
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate)
        )
        self.assertEqual([], errors)

    def test_importer_still_rejects_untrusted_mf_host(self) -> None:
        with self.assertRaises(SourceError):
            validate_official_mf_workbook_url(
                "https://archive.example.test/official-artifact.xls"
            )

    def test_manifest_schema_does_not_encode_current_reference_endpoints(self) -> None:
        manifest = self._latest_snapshot_document("manifest.json")
        candidate = copy.deepcopy(manifest)
        candidate["provenance"]["gus"]["baseUrl"] = "https://archive.example.test/gus"
        candidate["provenance"]["nbp"]["url"] = "https://archive.example.test/nbp.xml"
        schema = pipeline.load_json(pipeline.SCHEMAS / "snapshot-manifest-v1.schema.json")
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate)
        )
        self.assertEqual([], errors)

    def test_nbp_schema_accepts_historical_https_source_uri(self) -> None:
        document = self._latest_snapshot_document("nbp-reference-rates.json")
        candidate = copy.deepcopy(document)
        candidate["observations"][0]["source"] = "https://archive.example.test/nbp.xml"
        schema = pipeline.load_json(pipeline.SCHEMAS / "nbp-reference-rates-v2.schema.json")
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate)
        )
        self.assertEqual([], errors)

    def test_offline_nbp_rejects_future_effective_date(self) -> None:
        nbp = pipeline.load_json(pipeline.DATA / "reference" / "nbp-reference-rates.json")
        candidate = copy.deepcopy(nbp)
        candidate["verifiedAt"] = "2022-05-06T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "future effective date"):
            pipeline._validate_nbp(candidate)


if __name__ == "__main__":
    unittest.main()
