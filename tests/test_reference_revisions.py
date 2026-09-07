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
            yield data, publication


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

    def test_highest_revision_is_current_consumer_observation(self):
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

    def test_reversion_to_older_value_creates_new_revision(self):
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

    def test_coverage_counts_identities_not_revision_rows(self):
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
        self.assertEqual(2, coverage["nbpReferenceRates"]["observationCount"])


class ReferenceRevisionPublicationTests(unittest.TestCase):
    def test_gus_correction_creates_revision_and_preserves_old_snapshot(self):
        with isolated_publication_tree() as (data, publication):
            first_revision = pipeline.build_publication()
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
                for item in pipeline._current_reference_observations(
                    source["observations"], "period"
                )
                if int(item["period"][:4]) == year
            ]
            incoming = [
                corrected if item["period"] == original["period"] else item
                for item in incoming
            ]

            def fake_fetch_gus_history(_session, _start, _end, sleep=None, evidence=None):
                if evidence is not None:
                    evidence.append(("https://api-sdp.stat.gov.pl/test", b'{"fixture":true}'))
                return incoming

            with patch.object(update, "fetch_gus_history", side_effect=fake_fetch_gus_history):
                added = update.sync_gus(object(), year, year, "2026-09-04T00:00:00Z")

            self.assertEqual(1, added)
            second_revision = pipeline.build_publication()
            self.assertNotEqual(first_revision, second_revision)
            self.assertEqual(before, snapshot_bytes(first_snapshot))

            published = pipeline.load_json(
                publication / "snapshots" / second_revision / "gus-cpi.json"
            )
            revisions = [
                item["revision"]
                for item in published["observations"]
                if item["period"] == original["period"]
            ]
            self.assertEqual([1, 2], revisions)
            updated_source = pipeline.load_json(data / "reference" / "gus-cpi.json")
            self.assertIn("evidenceBundleSha256", updated_source["source"])

    def test_nbp_correction_creates_revision_with_raw_evidence(self):
        with isolated_publication_tree() as (data, publication):
            first_revision = pipeline.build_publication()
            source = pipeline.load_json(data / "reference" / "nbp-reference-rates.json")
            original = copy.deepcopy(source["observations"][0])
            corrected = copy.deepcopy(original)
            corrected["annualRatePercent"] = (
                "5.26" if original["annualRatePercent"] != "5.26" else "5.27"
            )
            archive = [copy.deepcopy(item) for item in source["observations"]]
            archive[0] = corrected
            current = [copy.deepcopy(archive[-1])]

            with (
                patch.object(update, "fetch", side_effect=[b"archive-fixture", b"current-fixture"]),
                patch.object(update, "parse_nbp_rates", side_effect=[archive, current]),
            ):
                added = update.sync_nbp(object(), "2026-09-04T00:00:00Z")

            self.assertEqual(1, added)
            second_revision = pipeline.build_publication()
            self.assertNotEqual(first_revision, second_revision)
            published = pipeline.load_json(
                publication / "snapshots" / second_revision / "nbp-reference-rates.json"
            )
            revisions = [
                item["revision"]
                for item in published["observations"]
                if item["effectiveFrom"] == original["effectiveFrom"]
            ]
            self.assertEqual([1, 2], revisions)
            updated_source = pipeline.load_json(data / "reference" / "nbp-reference-rates.json")
            self.assertIn("archiveSha256", updated_source["source"])
            self.assertIn("currentSha256", updated_source["source"])


if __name__ == "__main__":
    unittest.main()
