import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import pipeline, update


class NormalizedIntegrityValidationTests(unittest.TestCase):
    @staticmethod
    def _gus_observation(period: str, revision: int = 1) -> dict:
        year, month = (int(part) for part in period.split("-"))
        return {
            "period": period,
            "indexPreviousYear100": "100.50",
            "yearOverYearPercent": "0.50",
            "revision": revision,
            "source": {
                "publisher": "GUS",
                "api": "SDP",
                "year": year,
                "periodId": 246 + month,
            },
        }

    @staticmethod
    def _gus_source(periods: list[str], verified_at: str) -> dict:
        return {
            "verifiedAt": verified_at,
            "observations": [
                NormalizedIntegrityValidationTests._gus_observation(period)
                for period in periods
            ],
        }

    def test_product_definitions_must_be_internally_consistent(self):
        products = copy.deepcopy(pipeline.load_product_definitions())
        products[0]["maturityMonths"] += 1
        with self.assertRaisesRegex(ValueError, "maturity must be divisible by interest period"):
            pipeline._validate_product_definitions(products)

    def test_series_product_definition_must_match_product_type(self):
        products = pipeline.load_product_definitions()
        item = copy.deepcopy(pipeline.load_series()[0])
        item["productDefinition"] = next(
            product["id"]
            for product in products
            if product["productType"] != item["productType"]
        )
        with self.assertRaisesRegex(ValueError, "productType .* does not match"):
            pipeline._validate_series([item], products)

    def test_series_terms_revisions_must_be_contiguous_from_one(self):
        products = pipeline.load_product_definitions()
        item = copy.deepcopy(pipeline.load_series()[0])
        item["termsRevision"] = 2
        with self.assertRaisesRegex(ValueError, "terms revisions must be contiguous from 1"):
            pipeline._validate_series([item], products)

    def test_reference_revisions_must_be_contiguous_from_one(self):
        with self.assertRaisesRegex(ValueError, "revisions must be contiguous from 1"):
            pipeline._validate_revisioned_observations(
                [{"period": "2014-01", "revision": 2}], "period", "GUS"
            )

    def test_gus_history_cannot_be_empty(self):
        with self.assertRaisesRegex(ValueError, "observations are empty"):
            pipeline._validate_gus(
                {"verifiedAt": "2026-09-04T00:00:00Z", "observations": []}
            )

    def test_gus_history_must_start_at_contract_boundary(self):
        with self.assertRaisesRegex(ValueError, "must start at 2014-01"):
            pipeline._validate_gus(self._gus_source(["2014-02"], "2014-02-15T00:00:00Z"))

    def test_gus_history_cannot_skip_an_entire_year(self):
        periods = [f"2014-{month:02d}" for month in range(1, 13)] + ["2016-01"]
        with self.assertRaisesRegex(ValueError, "missing years.*2015"):
            pipeline._validate_gus(self._gus_source(periods, "2016-01-15T00:00:00Z"))

    def test_gus_current_year_must_be_contiguous_from_january(self):
        with self.assertRaisesRegex(ValueError, "incomplete/non-contiguous"):
            pipeline._validate_gus(
                self._gus_source(["2014-01", "2014-03"], "2014-03-15T00:00:00Z")
            )

    def test_gus_derived_percent_must_match_index(self):
        source = self._gus_source(["2014-01"], "2014-01-15T00:00:00Z")
        source["observations"][0]["yearOverYearPercent"] = "9.99"
        with self.assertRaisesRegex(ValueError, "must equal indexPreviousYear100 - 100"):
            pipeline._validate_gus(source)

    def test_gus_source_metadata_must_match_period(self):
        source = self._gus_source(["2014-01"], "2014-01-15T00:00:00Z")
        source["observations"][0]["source"]["periodId"] = 248
        with self.assertRaisesRegex(ValueError, "source metadata does not match period"):
            pipeline._validate_gus(source)

    def test_nbp_history_cannot_be_empty(self):
        with self.assertRaisesRegex(ValueError, "observations are empty"):
            pipeline._validate_nbp({"observations": []})

    def test_nbp_history_must_start_at_exact_contract_boundary(self):
        source = {
            "observations": [
                {
                    "effectiveFrom": "2022-05-07",
                    "annualRatePercent": "5.25",
                    "revision": 1,
                    "publishedAt": None,
                    "source": "https://example.test/nbp.xml",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "must start at 2022-05-06"):
            pipeline._validate_nbp(source)

    def test_nbp_schema_accepts_historical_https_source_uri(self):
        latest = pipeline.load_json(pipeline.PUBLICATION / "latest.json")
        document = pipeline.load_json(
            pipeline.PUBLICATION
            / "snapshots"
            / latest["datasetRevision"]
            / "nbp-reference-rates.json"
        )
        candidate = copy.deepcopy(document)
        candidate["observations"][0]["source"] = "https://archive.example.test/nbp.xml"
        schema = pipeline.load_json(pipeline.SCHEMAS / "nbp-reference-rates-v2.schema.json")

        pipeline._validate_schema(candidate, schema, "nbp-reference-rates.json")

    def test_offline_nbp_rejects_future_effective_date(self):
        candidate = copy.deepcopy(
            pipeline.load_json(pipeline.DATA / "reference" / "nbp-reference-rates.json")
        )
        candidate["verifiedAt"] = "2022-05-06T00:00:00Z"

        with self.assertRaisesRegex(ValueError, "future effective date"):
            pipeline._validate_nbp(candidate)

    def test_immutable_snapshot_rejects_unexpected_directory(self):
        with TemporaryDirectory() as temp:
            snapshot = Path(temp) / "revision-a"
            snapshot.mkdir()
            (snapshot / "manifest.json").write_bytes(b"manifest\n")
            (snapshot / "extra").mkdir()
            with self.assertRaisesRegex(ValueError, "unexpected entries.*extra"):
                pipeline._write_immutable_snapshot(
                    snapshot, {"manifest.json": b"manifest\n"}
                )

    def test_immutable_snapshot_rejects_symlinked_expected_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "revision-a"
            snapshot.mkdir()
            target = root / "target.json"
            target.write_bytes(b"manifest\n")
            link = snapshot / "manifest.json"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable in test environment: {exc}")
            with self.assertRaisesRegex(ValueError, "would be rewritten: manifest.json"):
                pipeline._write_immutable_snapshot(
                    snapshot, {"manifest.json": b"manifest\n"}
                )

    @staticmethod
    def _write_snapshot(
        publication: Path, *, series=None, gus=None, nbp=None, products=None
    ) -> None:
        snapshot = publication / "snapshots" / "previous"
        pipeline.write_json(snapshot / "catalog.json", {"series": series or []})
        pipeline.write_json(
            snapshot / "product-definitions.json",
            {"productDefinitions": products or []},
        )
        pipeline.write_json(snapshot / "gus-cpi.json", {"observations": gus or []})
        pipeline.write_json(
            snapshot / "nbp-reference-rates.json", {"observations": nbp or []}
        )
        pipeline.write_json(
            publication / "latest.json",
            {
                "schemaVersion": "1.0",
                "datasetRevision": "previous",
                "manifest": "snapshots/previous/manifest.json",
            },
        )

    def test_offline_gate_rejects_deleting_entire_published_series(self):
        previous = {
            "seriesCode": "ROR0927",
            "termsRevision": 1,
            "contentHash": "sha256:previous",
        }
        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, series=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "Series revision .* was deleted"):
                    pipeline._validate_append_only_history(
                        [], [], {"observations": []}, {"observations": []}
                    )

    def test_offline_gate_rejects_mutating_published_series_provenance(self):
        previous = {
            "seriesCode": "ROR0927",
            "termsRevision": 1,
            "contentHash": "sha256:financial-terms",
            "provenance": {
                "primary": {
                    "url": "https://www.gov.pl/attachment/original",
                    "sha256": "a" * 64,
                }
            },
        }
        current = copy.deepcopy(previous)
        current["provenance"]["primary"]["url"] = (
            "https://www.gov.pl/attachment/replacement"
        )

        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, series=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "Series revision .* mutated in place"):
                    pipeline._validate_append_only_history(
                        [], [current], {"observations": []}, {"observations": []}
                    )

    def test_offline_gate_rejects_deleting_published_gus_tail(self):
        previous = {
            "period": "2026-08",
            "revision": 1,
            "indexPreviousYear100": "102.80",
        }
        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, gus=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "GUS observation .* was deleted"):
                    pipeline._validate_append_only_history(
                        [], [], {"observations": []}, {"observations": []}
                    )

    def test_offline_gate_rejects_mutating_published_gus_metadata(self):
        previous = {
            "period": "2026-08",
            "revision": 1,
            "indexPreviousYear100": "102.80",
            "yearOverYearPercent": "2.80",
            "source": {
                "publisher": "GUS",
                "api": "SDP",
                "year": 2026,
                "periodId": 254,
            },
        }
        current = copy.deepcopy(previous)
        current["publishedAt"] = "2026-09-15T00:00:00Z"

        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, gus=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "GUS observation .* mutated in place"):
                    pipeline._validate_append_only_history(
                        [], [], {"observations": [current]}, {"observations": []}
                    )

    def test_offline_gate_rejects_deleting_published_nbp_tail(self):
        previous = {
            "effectiveFrom": "2026-03-05",
            "revision": 1,
            "annualRatePercent": "3.75",
        }
        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, nbp=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "NBP observation .* was deleted"):
                    pipeline._validate_append_only_history(
                        [], [], {"observations": []}, {"observations": []}
                    )

    def test_offline_gate_rejects_mutating_published_nbp_provenance(self):
        previous = {
            "effectiveFrom": "2026-03-05",
            "revision": 1,
            "annualRatePercent": "3.75",
            "source": "https://legacy.example/nbp",
        }
        current = dict(previous)
        current["source"] = (
            "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml"
        )
        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, nbp=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "NBP observation .* mutated in place"):
                    pipeline._validate_append_only_history(
                        [], [], {"observations": []}, {"observations": [current]}
                    )

    def test_offline_and_live_nbp_boundaries_cannot_drift(self):
        self.assertEqual(update.NBP_HISTORY_START, pipeline.NBP_HISTORY_START)


if __name__ == "__main__":
    unittest.main()
