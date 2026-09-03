import hashlib
import json
import unittest
from pathlib import Path

from scripts.pipeline import build_dist

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

FROZEN_V1_GIT_BLOB_SHA1 = {
    "catalog-v1.json": "670973e723c4be31cf917276983ee853fd1db680",
    "reference-data-v1.json": "30e277bb138772346f1a7e2c9501099df5e5af4f",
    "metadata.json": "ae8cd5ead35e514410a3e2ed4c59ca6c8c293c34",
}


def git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


class FrozenV1ContractTests(unittest.TestCase):
    def test_frozen_v1_artifacts_are_byte_identical_to_golden_hashes(self):
        actual = {
            name: git_blob_sha1((DIST / name).read_bytes())
            for name in FROZEN_V1_GIT_BLOB_SHA1
        }
        self.assertEqual(FROZEN_V1_GIT_BLOB_SHA1, actual)

    def test_metadata_keeps_original_v1_shape(self):
        metadata = json.loads((DIST / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {"schemaVersion", "generatedAt", "catalog", "referenceData"},
            set(metadata),
        )
        self.assertEqual("1.0", metadata["schemaVersion"])
        self.assertEqual(
            {"path": "catalog-v1.json", "seriesCount": 15},
            metadata["catalog"],
        )
        self.assertEqual(
            {
                "path": "reference-data-v1.json",
                "referenceRateCount": 1,
                "inflationCount": 1,
            },
            metadata["referenceData"],
        )

    def test_v2_build_does_not_mutate_any_frozen_v1_artifact(self):
        before = {
            name: (DIST / name).read_bytes()
            for name in FROZEN_V1_GIT_BLOB_SHA1
        }

        build_dist()

        after = {
            name: (DIST / name).read_bytes()
            for name in FROZEN_V1_GIT_BLOB_SHA1
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
