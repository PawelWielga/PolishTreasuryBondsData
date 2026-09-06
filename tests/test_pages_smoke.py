import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.smoke_pages as smoke


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, url: str | None = None):
        self.content = content
        self.status_code = status_code
        self.url = url


class FakeSession:
    def __init__(self, resources: dict[str, bytes]):
        self.resources = resources

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, **_kwargs):
        if url not in self.resources:
            return FakeResponse(b"", 404, url)
        return FakeResponse(self.resources[url], url=url)


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def status_document(revision: str, *, nbp_status: str = "FRESH") -> dict:
    return {
        "schemaVersion": "1.0",
        "datasetRevision": revision,
        "sources": {
            "mf": {"status": "FRESH"},
            "gus": {"status": "FRESH"},
            "nbp": {"status": nbp_status},
        },
    }


def snapshot_resources(base: str, revision: str, file_content: bytes) -> dict[str, bytes]:
    manifest_url = f"{base}v1/snapshots/{revision}/manifest.json"
    catalog_url = f"{base}v1/snapshots/{revision}/catalog.json"
    manifest = {
        "schemaVersion": "1.0",
        "datasetRevision": revision,
        "files": {
            "catalog.json": {
                "path": "catalog.json",
                "sha256": hashlib.sha256(file_content).hexdigest(),
                "schemaVersion": "2.0",
                "count": 0,
            }
        },
    }
    return {
        manifest_url: encoded(manifest),
        catalog_url: file_content,
    }


class PagesSmokeTests(unittest.TestCase):

    def test_cross_origin_redirect_fails(self):
        session = FakeSession({})
        session.get = lambda url, **_kwargs: FakeResponse(
            b"{}", 200, "https://evil.example/redirected.json"
        )

        with self.assertRaisesRegex(smoke.SmokeError, "redirect left expected origin"):
            smoke.fetch_bytes(
                session,
                "https://example.test/project/v1/latest.json",
                attempts=1,
                retry_delay_seconds=0,
            )
    def make_publication(self, root: Path, current: str, prior: str | None = None) -> Path:
        publication = root / "publication" / "v1"
        snapshots = publication / "snapshots"
        (snapshots / current).mkdir(parents=True)
        if prior:
            (snapshots / prior).mkdir(parents=True)
        (publication / "latest.json").write_text(
            json.dumps({
                "schemaVersion": "1.0",
                "datasetRevision": current,
                "manifest": f"snapshots/{current}/manifest.json",
            }),
            encoding="utf-8",
        )
        (publication / "status.json").write_bytes(encoded(status_document(current)))
        return publication

    def test_consumer_flow_verifies_current_public_snapshot_and_runtime_health(self):
        base = "https://example.test/project/"
        revision = "rev-current"
        catalog = encoded({"schemaVersion": "2.0", "series": []})
        resources = {
            f"{base}v1/latest.json": encoded({
                "schemaVersion": "1.0",
                "datasetRevision": revision,
                "manifest": f"snapshots/{revision}/manifest.json",
            }),
            f"{base}v1/status.json": encoded(status_document(revision)),
            **snapshot_resources(base, revision, catalog),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            publication = self.make_publication(Path(temp_dir), revision)
            with patch.object(smoke.requests, "Session", return_value=FakeSession(resources)):
                current, prior = smoke.verify_public_contract(
                    base,
                    publication_root=publication,
                    attempts=1,
                    retry_delay_seconds=0,
                )

        self.assertEqual(revision, current)
        self.assertIsNone(prior)

    def test_hash_mismatch_fails(self):
        base = "https://example.test/project/"
        revision = "rev-current"
        good_catalog = encoded({"schemaVersion": "2.0", "series": []})
        resources = {
            f"{base}v1/latest.json": encoded({
                "schemaVersion": "1.0",
                "datasetRevision": revision,
                "manifest": f"snapshots/{revision}/manifest.json",
            }),
            f"{base}v1/status.json": encoded(status_document(revision)),
            **snapshot_resources(base, revision, good_catalog),
        }
        resources[f"{base}v1/snapshots/{revision}/catalog.json"] = encoded({
            "schemaVersion": "2.0",
            "series": [{"unexpected": True}],
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            publication = self.make_publication(Path(temp_dir), revision)
            with patch.object(smoke.requests, "Session", return_value=FakeSession(resources)):
                with self.assertRaisesRegex(smoke.SmokeError, "SHA-256 mismatch"):
                    smoke.verify_public_contract(
                        base,
                        publication_root=publication,
                        attempts=1,
                        retry_delay_seconds=0,
                    )

    def test_missing_runtime_status_fails(self):
        base = "https://example.test/project/"
        revision = "rev-current"
        catalog = encoded({"schemaVersion": "2.0", "series": []})
        resources = {
            f"{base}v1/latest.json": encoded({
                "schemaVersion": "1.0",
                "datasetRevision": revision,
                "manifest": f"snapshots/{revision}/manifest.json",
            }),
            **snapshot_resources(base, revision, catalog),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            publication = self.make_publication(Path(temp_dir), revision)
            with patch.object(smoke.requests, "Session", return_value=FakeSession(resources)):
                with self.assertRaisesRegex(smoke.SmokeError, "status.json did not converge"):
                    smoke.verify_public_contract(
                        base,
                        publication_root=publication,
                        attempts=1,
                        retry_delay_seconds=0,
                    )

    def test_stale_runtime_status_artifact_fails(self):
        base = "https://example.test/project/"
        revision = "rev-current"
        catalog = encoded({"schemaVersion": "2.0", "series": []})
        resources = {
            f"{base}v1/latest.json": encoded({
                "schemaVersion": "1.0",
                "datasetRevision": revision,
                "manifest": f"snapshots/{revision}/manifest.json",
            }),
            f"{base}v1/status.json": encoded(status_document(revision, nbp_status="STALE")),
            **snapshot_resources(base, revision, catalog),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            publication = self.make_publication(Path(temp_dir), revision)
            with patch.object(smoke.requests, "Session", return_value=FakeSession(resources)):
                with self.assertRaisesRegex(smoke.SmokeError, "status.json did not converge"):
                    smoke.verify_public_contract(
                        base,
                        publication_root=publication,
                        attempts=1,
                        retry_delay_seconds=0,
                    )

    def test_prior_snapshot_is_verified_when_retained(self):
        base = "https://example.test/project/"
        current = "20260903-current"
        prior = "20260801-prior"
        current_catalog = encoded({"schemaVersion": "2.0", "series": []})
        prior_catalog = encoded({"schemaVersion": "2.0", "series": []})
        resources = {
            f"{base}v1/latest.json": encoded({
                "schemaVersion": "1.0",
                "datasetRevision": current,
                "manifest": f"snapshots/{current}/manifest.json",
            }),
            f"{base}v1/status.json": encoded(status_document(current)),
            **snapshot_resources(base, current, current_catalog),
            **snapshot_resources(base, prior, prior_catalog),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            publication = self.make_publication(Path(temp_dir), current, prior)
            with patch.object(smoke.requests, "Session", return_value=FakeSession(resources)):
                verified_current, verified_prior = smoke.verify_public_contract(
                    base,
                    publication_root=publication,
                    attempts=1,
                    retry_delay_seconds=0,
                    require_prior=True,
                )

        self.assertEqual(current, verified_current)
        self.assertEqual(prior, verified_prior)

    def test_public_latest_must_match_the_revision_just_deployed(self):
        base = "https://example.test/project/"
        expected = "rev-new"
        resources = {
            f"{base}v1/latest.json": encoded({
                "schemaVersion": "1.0",
                "datasetRevision": "rev-old",
                "manifest": "snapshots/rev-old/manifest.json",
            })
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            publication = self.make_publication(Path(temp_dir), expected)
            with patch.object(smoke.requests, "Session", return_value=FakeSession(resources)):
                with self.assertRaisesRegex(smoke.SmokeError, "did not converge"):
                    smoke.verify_public_contract(
                        base,
                        publication_root=publication,
                        attempts=1,
                        retry_delay_seconds=0,
                    )


if __name__ == "__main__":
    unittest.main()
