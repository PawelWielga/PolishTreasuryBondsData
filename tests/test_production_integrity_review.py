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
from scripts.sources import SourceError, fetch


class _Response:
    def __init__(self, url: str, content: bytes = b"ok", status_code: int = 200) -> None:
        self.url = url
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


class SourceBoundaryRegressionTests(unittest.TestCase):
    def test_cross_origin_redirect_is_rejected(self) -> None:
        session = Mock()
        session.get.return_value = _Response("https://evil.example/payload")
        with self.assertRaisesRegex(SourceError, "redirect left trusted origin"):
            fetch(session, "https://www.gov.pl/attachment/example", "application/octet-stream")

    def test_same_origin_redirect_is_allowed(self) -> None:
        session = Mock()
        session.get.return_value = _Response("https://www.gov.pl/attachment/canonical", b"official")
        self.assertEqual(b"official", fetch(session, "https://www.gov.pl/attachment/example", "application/octet-stream"))

    def test_mf_url_rejects_credentials_and_nonstandard_port(self) -> None:
        for url in ("https://user@www.gov.pl/attachment/example", "https://www.gov.pl:444/attachment/example"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(SourceError, "exact official"):
                    update._validate_official_mf_workbook_url(url)

    def test_local_workbook_must_match_official_remote_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "mf.xls"
            workbook.write_bytes(b"local-tampered")
            with patch("scripts.update.fetch", return_value=b"official-remote"):
                with self.assertRaisesRegex(SourceError, "does not match"):
                    update.sync_mf(object(), "2026-09-05", workbook_path=workbook, workbook_url="https://www.gov.pl/attachment/example", cross_check=False)

    def test_future_sale_cannot_be_outstanding_yet(self) -> None:
        series = {"productType": "ROR", "saleFrom": "2026-10-01", "saleTo": "2026-10-31"}
        self.assertFalse(update._can_still_be_outstanding(series, date(2026, 9, 5)))

    def test_live_nbp_rejects_future_effective_date(self) -> None:
        archive = [{"effectiveFrom": "2022-05-06", "annualRatePercent": "5.25"}, {"effectiveFrom": "2026-09-06", "annualRatePercent": "4.00"}]
        with self.assertRaisesRegex(SourceError, "future reference-rate date"):
            update._validated_nbp_observations(archive, [archive[-1].copy()], [], date(2026, 9, 5))


class OfflineContractRegressionTests(unittest.TestCase):
    def test_catalog_schema_rejects_unofficial_provenance_hosts(self) -> None:
        catalog = json.loads((pipeline.DIST / "catalog-v2.json").read_text(encoding="utf-8"))
        candidate = copy.deepcopy(catalog)
        candidate["series"][0]["provenance"]["primary"]["url"] = "https://evil.example/attachment/fake"
        schema = json.loads((pipeline.SCHEMAS / "catalog-v2.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate))
        self.assertTrue(errors)

    def test_manifest_schema_rejects_unofficial_reference_endpoints(self) -> None:
        latest = json.loads((pipeline.PUBLICATION / "latest.json").read_text(encoding="utf-8"))
        manifest = json.loads((pipeline.PUBLICATION / latest["manifest"]).read_text(encoding="utf-8"))
        schema = json.loads((pipeline.SCHEMAS / "snapshot-manifest-v1.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for source, field, value in (("mf", "url", "https://evil.example/attachment/fake"), ("gus", "baseUrl", "https://evil.example/api"), ("nbp", "url", "https://evil.example/rates.xml")):
            candidate = copy.deepcopy(manifest)
            candidate["provenance"][source][field] = value
            with self.subTest(source=source, field=field):
                self.assertTrue(list(validator.iter_errors(candidate)))

    def test_offline_nbp_rejects_future_effective_date(self) -> None:
        nbp = json.loads((pipeline.DATA / "reference" / "nbp-reference-rates.json").read_text(encoding="utf-8"))
        candidate = copy.deepcopy(nbp)
        candidate["verifiedAt"] = "2022-05-06T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "future effective date"):
            pipeline._validate_nbp(candidate)


if __name__ == "__main__":
    unittest.main()
