from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_ROOT = "publication/v1/snapshots"


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


def immutable_snapshot_violations(base: str, head: str = "HEAD") -> list[str]:
    """Return changes that rewrite a snapshot already present in *base*.

    New snapshot directories are allowed. Once a snapshot directory exists in
    the reviewed base revision, every file and the directory membership itself
    are immutable: modifications, deletions, renames and additional files are
    all rejected.
    """
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
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject rewrites of immutable publication snapshots that already exist in a reviewed base revision"
    )
    parser.add_argument("--base", required=True, help="Reviewed base commit/ref")
    parser.add_argument("--head", default="HEAD", help="Candidate commit/ref (default: HEAD)")
    args = parser.parse_args()

    try:
        violations = immutable_snapshot_violations(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"ERROR: immutable snapshot comparison failed: {detail}", file=sys.stderr)
        return 2

    if violations:
        print("ERROR: immutable published snapshots were changed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print(f"Immutable snapshot history is unchanged relative to {args.base}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
