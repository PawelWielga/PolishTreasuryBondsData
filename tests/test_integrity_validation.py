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

    def test_product_definitions_must_match_parser_rules(self):
        products = copy.deepcopy(pipeline.load_product_definitions())
        products[0]["maturityMonths"] += 1

        with self.assertRaisesRegex(ValueError, "disagrees with parser rules"):
            pipeline._validate_product_definitions(products)

    def test_series_product_definition_must_match_product_type(self):
        products = pipeline.load_product_definitions()
        item = copy.deepcopy(pipeline.load_series()[0])
        different_definition = next(
            product["id"]
            for product in products
            if product["productType"] != item["productType"]
        )
        item["productDefinition"] = different_definition

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
                [{"period": "2014-01", "revision": 2}],
                "period",
                "GUS",
            )

    def test_gus_history_cannot_be_empty(self):
        with self.assertRaisesRegex(ValueError, "observations are empty"):
            pipeline._validate_gus(
                {"verifiedAt": "2026-09-04T00:00:00Z", "observations": []}
            )

    def test_gus_history_must_start_at_contract_boundary(self):
        source = self._gus_source(["2014-02"], "2014-02-15T00:00:00Z")

        with self.assertRaisesRegex(ValueError, "must start at 2014-01"):
            pipeline._validate_gus(source)

    def test_gus_history_cannot_skip_an_entire_year(self):
        periods = [f"2014-{month:02d}" for month in range(1, 13)]
        periods += ["2016-01"]
        source = self._gus_source(periods, "2016-01-15T00:00:00Z")

        with self.assertRaisesRegex(ValueError, "missing years.*2015"):
            pipeline._validate_gus(source)

    def test_gus_current_year_must_be_contiguous_from_january(self):
        source = self._gus_source(
            ["2014-01", "2014-03"],
            "2014-03-15T00:00:00Z",
        )

        with self.assertRaisesRegex(ValueError, "incomplete/non-contiguous"):
            pipeline._validate_gus(source)

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

    def test_immutable_snapshot_rejects_unexpected_directory(self):
        with TemporaryDirectory() as temp:
            snapshot = Path(temp) / "revision-a"
            snapshot.mkdir()
            (snapshot / "manifest.json").write_bytes(b"manifest\n")
            (snapshot / "extra").mkdir()
            (snapshot / "extra" / "payload.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unexpected entries.*extra"):
                pipeline._write_immutable_snapshot(
                    snapshot,
                    {"manifest.json": b"manifest\n"},
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
                    snapshot,
                    {"manifest.json": b"manifest\n"},
                )

    @staticmethod
    def _write_snapshot(publication: Path, *, series=None, gus=None, nbp=None, products=None) -> None:
        snapshot = publication / "snapshots" / "previous"
        pipeline.write_json(snapshot / "catalog.json", {"series": series or []})
        pipeline.write_json(
            snapshot / "product-definitions.json",
            {"productDefinitions": products or []},
        )
        pipeline.write_json(snapshot / "gus-cpi.json", {"observations": gus or []})
        pipeline.write_json(snapshot / "nbp-reference-rates.json", {"observations": nbp or []})

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
                    pipeline._validate_append_only_history([], [], {"observations": []}, {"observations": []})

    def test_offline_gate_rejects_deleting_published_gus_tail(self):
        previous = {"period": "2026-08", "revision": 1, "indexPreviousYear100": "102.80"}
        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, gus=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                with self.assertRaisesRegex(ValueError, "GUS observation .* was deleted"):
                    pipeline._validate_append_only_history([], [], {"observations": []}, {"observations": []})

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
                    pipeline._validate_append_only_history([], [], {"observations": []}, {"observations": []})

    def test_offline_gate_allows_provenance_only_nbp_migration(self):
        previous = {
            "effectiveFrom": "2026-03-05",
            "revision": 1,
            "annualRatePercent": "3.75",
            "source": "https://legacy.example/nbp",
        }
        current = dict(previous)
        current["source"] = "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml"
        with TemporaryDirectory() as temp:
            publication = Path(temp)
            self._write_snapshot(publication, nbp=[previous])
            with patch.object(pipeline, "PUBLICATION", publication):
                pipeline._validate_append_only_history(
                    [], [], {"observations": []}, {"observations": [current]}
                )

    def test_offline_and_live_nbp_boundaries_cannot_drift(self):
        self.assertEqual(update.NBP_HISTORY_START, pipeline.NBP_HISTORY_START)


if __name__ == "__main__":
    unittest.main()
