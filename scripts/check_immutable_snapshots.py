from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_ROOT = "publication/v1/snapshots"
LATEST_PATH = "publication/v1/latest.json"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def _snapshot_directory(path: str) -> str | None:
    prefix = SNAPSHOTS_ROOT + "/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    revision, separator, _ = remainder.partition("/")
    if not separator or not revision:
        return None
    return f"{SNAPSHOTS_ROOT}/{revision}"


def _exists_in_revision(revision: str, path: str) -> bool:
    result = _git("cat-file", "-e", f"{revision}:{path}", check=False)
    return result.returncode == 0


def _selected_snapshot(revision: str) -> str:
    latest_text = _git("show", f"{revision}:{LATEST_PATH}").stdout
    try:
        latest = json.loads(latest_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{LATEST_PATH} is not valid JSON in {revision}") from exc

    dataset_revision = latest.get("datasetRevision")
    if not isinstance(dataset_revision, str) or not dataset_revision:
        raise ValueError(f"{LATEST_PATH} has no valid datasetRevision in {revision}")

    expected_manifest = f"snapshots/{dataset_revision}/manifest.json"
    if latest.get("manifest") != expected_manifest:
        raise ValueError(
            f"{LATEST_PATH} manifest does not match datasetRevision in {revision}: "
            f"expected {expected_manifest!r}, got {latest.get('manifest')!r}"
        )
    return f"{SNAPSHOTS_ROOT}/{dataset_revision}"


def immutable_snapshot_violations(base: str, head: str = "HEAD") -> list[str]:
    """Return changes that violate the immutable publication namespace.

    Every snapshot directory already present in the reviewed base revision is
    byte-immutable. A candidate may add only the single new snapshot selected by
    the candidate's ``latest.json``; arbitrary extra snapshot directories are
    rejected so Pages cannot publish unreferenced data under a permanent URL.
    """
    selected_snapshot = _selected_snapshot(head)
    diff = _git(
        "diff",
        "--name-status",
        "--find-renames",
        f"{base}...{head}",
        "--",
        SNAPSHOTS_ROOT,
    ).stdout

    violations: list[str] = []
    for raw_line in diff.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        status = parts[0]
        paths = parts[1:]
        for path in paths:
            snapshot = _snapshot_directory(path)
            if snapshot is None:
                violations.append(f"{status}\t{path} (invalid snapshot path)")
                continue
            if _exists_in_revision(base, snapshot):
                violations.append(f"{status}\t{path}")
                continue
            if not status.startswith("A"):
                violations.append(f"{status}\t{path} (non-addition in new snapshot)")
                continue
            if snapshot != selected_snapshot:
                violations.append(
                    f"{status}\t{path} (new snapshot is not selected by {LATEST_PATH})"
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reject rewrites of reviewed immutable snapshots and unselected new "
            "snapshot directories"
        )
    )
    parser.add_argument("--base", required=True, help="Reviewed base commit/ref")
    parser.add_argument("--head", default="HEAD", help="Candidate commit/ref (default: HEAD)")
    args = parser.parse_args()

    try:
        violations = immutable_snapshot_violations(args.base, args.head)
    except (subprocess.CalledProcessError, ValueError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
        else:
            detail = str(exc)
        print(f"ERROR: immutable snapshot comparison failed: {detail}", file=sys.stderr)
        return 2

    if violations:
        print("ERROR: immutable publication namespace was changed unsafely:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print(f"Immutable snapshot history is unchanged relative to {args.base}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
