from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLICATION_ROOT = ROOT / "publication" / "v1"


class SmokeError(RuntimeError):
    """The deployed GitHub Pages contract is incomplete or inconsistent."""


def _safe_relative_path(value: str, label: str) -> str:
    parsed = urlparse(value)
    path = PurePosixPath(parsed.path)
    if parsed.scheme or parsed.netloc or path.is_absolute() or ".." in path.parts:
        raise SmokeError(f"{label} must be a safe relative path, got {value!r}")
    return value


def fetch_bytes(
    session: requests.Session,
    url: str,
    attempts: int = 6,
    retry_delay_seconds: float = 2.0,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url,
                headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                timeout=(10, 30),
            )
            if response.status_code == 200:
                return response.content
            last_error = SmokeError(f"HTTP {response.status_code} for {url}")
        except requests.RequestException as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(retry_delay_seconds * attempt)

    raise SmokeError(f"Could not fetch deployed Pages resource {url}: {last_error}")


def parse_json(content: bytes, url: str) -> dict[str, Any]:
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"Deployed resource is not valid JSON: {url}: {exc}") from exc
    if not isinstance(document, dict):
        raise SmokeError(f"Expected a JSON object at {url}")
    return document


def fetch_json(
    session: requests.Session,
    url: str,
    attempts: int,
    retry_delay_seconds: float,
) -> dict[str, Any]:
    return parse_json(fetch_bytes(session, url, attempts, retry_delay_seconds), url)


def _document_count(document: dict[str, Any]) -> int | None:
    for key in ("series", "productDefinitions", "observations"):
        value = document.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def verify_manifest_and_files(
    session: requests.Session,
    manifest_url: str,
    expected_revision: str,
    attempts: int,
    retry_delay_seconds: float,
) -> None:
    manifest = fetch_json(session, manifest_url, attempts, retry_delay_seconds)
    if manifest.get("schemaVersion") != "1.0":
        raise SmokeError(
            f"Unexpected manifest schemaVersion at {manifest_url}: {manifest.get('schemaVersion')!r}"
        )
    if manifest.get("datasetRevision") != expected_revision:
        raise SmokeError(
            f"Manifest revision mismatch at {manifest_url}: "
            f"expected {expected_revision!r}, got {manifest.get('datasetRevision')!r}"
        )

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise SmokeError(f"Manifest at {manifest_url} has no files")

    for logical_name, metadata in sorted(files.items()):
        if not isinstance(metadata, dict):
            raise SmokeError(f"Invalid manifest entry for {logical_name!r}")
        relative_path = _safe_relative_path(str(metadata.get("path", "")), f"{logical_name} path")
        if not relative_path:
            raise SmokeError(f"Manifest entry {logical_name!r} has an empty path")
        file_url = urljoin(manifest_url, relative_path)
        content = fetch_bytes(session, file_url, attempts, retry_delay_seconds)

        actual_hash = hashlib.sha256(content).hexdigest()
        expected_hash = metadata.get("sha256")
        if actual_hash != expected_hash:
            raise SmokeError(
                f"SHA-256 mismatch for {file_url}: expected {expected_hash!r}, got {actual_hash!r}"
            )

        document = parse_json(content, file_url)
        expected_schema = metadata.get("schemaVersion")
        if document.get("schemaVersion") != expected_schema:
            raise SmokeError(
                f"schemaVersion mismatch for {file_url}: expected {expected_schema!r}, "
                f"got {document.get('schemaVersion')!r}"
            )

        expected_count = metadata.get("count")
        actual_count = _document_count(document)
        if actual_count is not None and actual_count != expected_count:
            raise SmokeError(
                f"Count mismatch for {file_url}: expected {expected_count!r}, got {actual_count!r}"
            )


def find_prior_revision(publication_root: Path, current_revision: str) -> str | None:
    snapshots = publication_root / "snapshots"
    if not snapshots.exists():
        return None
    revisions = sorted(
        path.name
        for path in snapshots.iterdir()
        if path.is_dir() and path.name != current_revision
    )
    return revisions[-1] if revisions else None


def verify_public_contract(
    base_url: str,
    publication_root: Path = DEFAULT_PUBLICATION_ROOT,
    attempts: int = 6,
    retry_delay_seconds: float = 2.0,
    require_prior: bool = False,
) -> tuple[str, str | None]:
    normalized_base = base_url.rstrip("/") + "/"
    latest_url = urljoin(normalized_base, "v1/latest.json")

    with requests.Session() as session:
        latest = fetch_json(session, latest_url, attempts, retry_delay_seconds)
        if latest.get("schemaVersion") != "1.0":
            raise SmokeError(f"Unexpected latest.json schemaVersion at {latest_url}")

        revision = latest.get("datasetRevision")
        if not isinstance(revision, str) or not revision:
            raise SmokeError(f"latest.json at {latest_url} has no datasetRevision")

        manifest_relative = latest.get("manifest")
        if not isinstance(manifest_relative, str):
            raise SmokeError(f"latest.json at {latest_url} has no manifest path")
        _safe_relative_path(manifest_relative, "latest manifest")
        expected_manifest = f"snapshots/{revision}/manifest.json"
        if manifest_relative != expected_manifest:
            raise SmokeError(
                f"latest.json manifest path mismatch: expected {expected_manifest!r}, "
                f"got {manifest_relative!r}"
            )

        manifest_url = urljoin(latest_url, manifest_relative)
        verify_manifest_and_files(
            session,
            manifest_url,
            revision,
            attempts,
            retry_delay_seconds,
        )

        prior_revision = find_prior_revision(publication_root, revision)
        if require_prior and prior_revision is None:
            raise SmokeError("No prior immutable snapshot is retained yet")
        if prior_revision is not None:
            prior_manifest_url = urljoin(
                normalized_base,
                f"v1/snapshots/{prior_revision}/manifest.json",
            )
            verify_manifest_and_files(
                session,
                prior_manifest_url,
                prior_revision,
                attempts,
                retry_delay_seconds,
            )

    return revision, prior_revision


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the deployed GitHub Pages consumer contract"
    )
    parser.add_argument("--base-url", required=True, help="GitHub Pages project base URL")
    parser.add_argument(
        "--publication-root",
        type=Path,
        default=DEFAULT_PUBLICATION_ROOT,
        help="Local publication/v1 tree used only to discover a retained prior revision",
    )
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--retry-delay-seconds", type=float, default=2.0)
    parser.add_argument("--require-prior", action="store_true")
    args = parser.parse_args()

    try:
        revision, prior = verify_public_contract(
            args.base_url,
            args.publication_root,
            args.attempts,
            args.retry_delay_seconds,
            args.require_prior,
        )
    except (SmokeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    if prior:
        print(f"Pages contract verified: current={revision}, prior={prior}")
    else:
        print(
            f"Pages contract verified: current={revision}; "
            "no earlier real datasetRevision is retained yet"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
