import copy
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import scripts.pipeline as pipeline
import scripts.update as update

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_publication_tree():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        data = root / "data"
        dist = root / "dist"
        publication = root / "publication" / "v1"
        shutil.copytree(ROOT / "data", data)
        dist.mkdir(parents=True)
        publication.mkdir(parents=True)

        with (
            patch.object(pipeline, "DATA", data),
            patch.object(pipeline, "DIST", dist),
            patch.object(pipeline, "PUBLICATION", publication),
            patch.object(update, "DATA", data),
        ):
            yield data, dist, publication


def snapshot_bytes(snapshot: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(snapshot)): path.read_bytes()
        for path in sorted(snapshot.rglob("*"))
        if path.is_file()
    }


class ReferenceRevisionValidationTests(unittest.TestCase):
    def test_gus_duplicate_identity_and_revision_is_rejected(self):
        observations = [
            {"period": "2026-01", "revision": 1},
            {"period": "2026-01", "revision": 1},
        ]
        with self.assertRaisesRegex(ValueError, r"unique by \(period, revision\)"):
            pipeline._validate_gus({"observations": observations})

    def test_nbp_duplicate_identity_and_revision_is_rejected(self):
        observations = [
            {"effectiveFrom": "2022-05-06", "revision": 1},
            {"effectiveFrom": "2022-05-06", "revision": 1},
        ]
        with self.assertRaisesRegex(ValueError, r"unique by \(effectiveFrom, revision\)"):
            pipeline._validate_nbp({"observations": observations})

    def test_distinct_revisions_of_same_identity_are_accepted(self):
        pipeline._validate_revisioned_observations(
            [
                {"period": "2026-01", "revision": 1},
                {"period": "2026-01", "revision": 2},
            ],
            "period",
            "GUS",
        )
        pipeline._validate_revisioned_observations(
            [
                {"effectiveFrom": "2022-05-06", "revision": 1},
                {"effectiveFrom": "2022-05-06", "revision": 2},
            ],
            "effectiveFrom",
            "NBP",
        )

    def test_highest_revision_is_the_current_consumer_observation(self):
        observations = [
            {"period": "2026-01", "revision": 1, "value": "old"},
            {"period": "2026-01", "revision": 2, "value": "corrected"},
            {"period": "2026-02", "revision": 1, "value": "next"},
        ]
        current = pipeline._current_reference_observations(observations, "period")
        self.assertEqual(
            [("2026-01", 2, "corrected"), ("2026-02", 1, "next")],
            [(item["period"], item["revision"], item["value"]) for item in current],
        )

    def test_merge_revisions_records_reversion_to_older_value_as_new_revision(self):
        existing = [
            {"period": "2026-01", "revision": 1, "value": "A"},
            {"period": "2026-01", "revision": 2, "value": "B"},
        ]

        merged = update._merge_revisions(
            existing,
            [{"period": "2026-01", "value": "A"}],
            "period",
            "value",
        )

        self.assertEqual(
            [(1, "A"), (2, "B"), (3, "A")],
            [(item["revision"], item["value"]) for item in merged],
        )

    def test_sync_series_records_reversion_to_older_terms_as_new_revision(self):
        existing = [
            {
                "seriesCode": "RORTEST",
                "productType": "ROR",
                "termsRevision": 1,
                "contentHash": "hash-a",
            },
            {
                "seriesCode": "RORTEST",
                "productType": "ROR",
                "termsRevision": 2,
                "contentHash": "hash-b",
            },
        ]
        candidate = {
            "seriesCode": "RORTEST",
            "productType": "ROR",
            "termsRevision": 1,
            "contentHash": "hash-a",
        }

        with (
            patch.object(update, "load_series", return_value=existing),
            patch.object(update, "write_json") as write_mock,
        ):
            self.assertEqual((0, 1), update.sync_series([candidate]))

        written_path, written = write_mock.call_args.args
        self.assertEqual("terms-v3.json", written_path.name)
        self.assertEqual(3, written["termsRevision"])
        self.assertEqual("hash-a", written["contentHash"])

    def test_coverage_counts_distinct_observation_identities_not_revisions(self):
        coverage = pipeline._coverage(
            [],
            [
                {"period": "2026-01", "revision": 1},
                {"period": "2026-01", "revision": 2},
                {"period": "2026-02", "revision": 1},
            ],
            [
                {"effectiveFrom": "2022-05-06", "revision": 1},
                {"effectiveFrom": "2022-05-06", "revision": 2},
                {"effectiveFrom": "2022-06-09", "revision": 1},
            ],
        )
        self.assertEqual(2, coverage["gusCpi"]["observationCount"])
        self.assertEqual("2026-01", coverage["gusCpi"]["fromPeriod"])
        self.assertEqual("2026-02", coverage["gusCpi"]["throughPeriod"])
        self.assertEqual(2, coverage["nbpReferenceRates"]["observationCount"])
        self.assertEqual("2022-05-06", coverage["nbpReferenceRates"]["fromEffectiveDate"])
        self.assertEqual("2022-06-09", coverage["nbpReferenceRates"]["throughEffectiveDate"])


class ReferenceRevisionPublicationTests(unittest.TestCase):
    def test_gus_correction_builds_revision_two_and_keeps_previous_snapshot_immutable(self):
        with isolated_publication_tree() as (data, _dist, publication):
            first_revision = pipeline.build_dist()
            first_snapshot = publication / "snapshots" / first_revision
            before = snapshot_bytes(first_snapshot)

            source = pipeline.load_json(data / "reference" / "gus-cpi.json")
            original = copy.deepcopy(source["observations"][0])
            corrected = copy.deepcopy(original)
            corrected["indexPreviousYear100"] = "103.00"
            corrected["yearOverYearPercent"] = "3.00"
            year = int(original["period"][:4])
            incoming = [
                copy.deepcopy(item)
                for item in pipeline._current_reference_observations(source["observations"], "period")
                if int(item["period"][:4]) == year
            ]
            incoming = [corrected if item["period"] == original["period"] else item for item in incoming]

            with patch.object(update, "fetch_gus_history", return_value=incoming):
                added = update.sync_gus(object(), year, year, "2026-09-04T00:00:00Z")

            self.assertEqual(1, added)
            second_revision = pipeline.build_dist()
            self.assertNotEqual(first_revision, second_revision)
            self.assertEqual(before, snapshot_bytes(first_snapshot))

            published = pipeline.load_json(publication / "snapshots" / second_revision / "gus-cpi.json")
            revisions = [
                item["revision"] for item in published["observations"]
                if item["period"] == original["period"]
            ]
            self.assertEqual([1, 2], revisions)

            manifest = pipeline.load_json(publication / "snapshots" / second_revision / "manifest.json")
            distinct_periods = {item["period"] for item in published["observations"]}
            self.assertEqual(len(distinct_periods), manifest["coverage"]["gusCpi"]["observationCount"])

    def test_nbp_correction_builds_revision_two(self):
        with isolated_publication_tree() as (data, _dist, publication):
            first_revision = pipeline.build_dist()

            source = pipeline.load_json(data / "reference" / "nbp-reference-rates.json")
            original = copy.deepcopy(source["observations"][0])
            corrected = copy.deepcopy(original)
            corrected["annualRatePercent"] = "5.26" if original["annualRatePercent"] != "5.26" else "5.27"
            archive = [copy.deepcopy(item) for item in source["observations"]]
            archive[0] = corrected
            current = [copy.deepcopy(archive[-1])]

            with (
                patch.object(update, "fetch", return_value=b"fixture"),
                patch.object(update, "parse_nbp_rates", side_effect=[archive, current]),
            ):
                added = update.sync_nbp(object(), "2026-09-04T00:00:00Z")

            self.assertEqual(1, added)
            second_revision = pipeline.build_dist()
            self.assertNotEqual(first_revision, second_revision)

            published = pipeline.load_json(publication / "snapshots" / second_revision / "nbp-reference-rates.json")
            revisions = [
                item["revision"] for item in published["observations"]
                if item["effectiveFrom"] == original["effectiveFrom"]
            ]
            self.assertEqual([1, 2], revisions)

            manifest = pipeline.load_json(publication / "snapshots" / second_revision / "manifest.json")
            distinct_dates = {item["effectiveFrom"] for item in published["observations"]}
            self.assertEqual(
                len(distinct_dates),
                manifest["coverage"]["nbpReferenceRates"]["observationCount"],
            )


if __name__ == "__main__":
    unittest.main()
