from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_ROOT = "publication/v1/snapshots"
LATEST_PATH = "publication/v1/latest.json"
EXPECTED_SNAPSHOT_FILES = frozenset(
    {
        "catalog.json",
        "product-definitions.json",
        "gus-cpi.json",
        "nbp-reference-rates.json",
        "manifest.json",
    }
)


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


def _snapshot_relative_path(snapshot: str, path: str) -> str | None:
    prefix = snapshot + "/"
    if not path.startswith(prefix):
        return None
    relative = path[len(prefix) :]
    if not relative or "/" in relative:
        return None
    return relative


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


def _historical_snapshot_directories(head: str) -> set[str]:
    """Return every snapshot directory that ever appeared on first-parent history.

    Looking only at directories that still exist cannot detect a privileged full
    directory deletion after a later unrelated commit. Name-status history keeps
    the old path visible even after the directory disappears from the current
    tree, including rename/delete records.
    """
    history = _git(
        "log",
        "--first-parent",
        "--format=",
        "--name-status",
        "--find-renames",
        head,
        "--",
        SNAPSHOTS_ROOT,
    ).stdout
    snapshots: set[str] = set()
    for raw_line in history.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        for path in parts[1:]:
            snapshot = _snapshot_directory(path)
            if snapshot is not None:
                snapshots.add(snapshot)
    return snapshots


def _post_publication_history_changes(snapshot: str, first_reviewed: str, head: str) -> list[str]:
    """Return every first-parent change to a snapshot after its publication commit.

    Comparing only the first and current trees misses a privileged mutation that
    was later reverted. Immutable means that the namespace must never be touched
    again, even temporarily, so audit each first-parent commit after the first
    reviewed appearance instead of only the final tree state.
    """
    history = _git(
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--format=commit:%H",
        "--name-status",
        "--find-renames",
        f"{first_reviewed}..{head}",
        "--",
        snapshot,
    ).stdout

    changes: list[str] = []
    commit = "unknown"
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("commit:"):
            commit = line.removeprefix("commit:")
            continue
        changes.append(
            f"{line} (snapshot first published at {first_reviewed}; "
            f"changed after publication in {commit})"
        )
    return changes


def archive_integrity_violations(head: str = "HEAD") -> list[str]:
    """Verify every reviewed snapshot remains untouched after first publication.

    A per-push diff is not enough to protect an immutable archive forever. Full
    first-parent history anchors each snapshot to the commit where its manifest
    first appeared and rejects every later touch to that namespace, including a
    privileged rewrite that was subsequently reverted. Historical directory names
    are recovered from Git as well, so a snapshot that was deleted entirely cannot
    disappear from validation. This runs on scheduled Pages deploys too, preventing
    a protection bypass from becoming publishable merely with time.
    """
    root = ROOT / SNAPSHOTS_ROOT
    if root.is_symlink() or not root.is_dir():
        return [f"{SNAPSHOTS_ROOT}: snapshot archive is missing or is not a real directory"]

    current_entries = {
        f"{SNAPSHOTS_ROOT}/{entry.name}": entry
        for entry in root.iterdir()
    }
    historical_snapshots = _historical_snapshot_directories(head)

    violations: list[str] = []
    for missing_snapshot in sorted(historical_snapshots - set(current_entries)):
        violations.append(
            f"{missing_snapshot}: historical snapshot directory is missing from current tree"
        )

    for snapshot, entry in sorted(current_entries.items()):
        if entry.is_symlink() or not entry.is_dir():
            violations.append(f"{snapshot}: snapshot entry is not a real directory")
            continue

        actual_entries = {path.name for path in entry.iterdir()}
        missing = sorted(EXPECTED_SNAPSHOT_FILES - actual_entries)
        unexpected = sorted(actual_entries - EXPECTED_SNAPSHOT_FILES)
        if missing:
            violations.append(
                f"{snapshot}: missing canonical files: {', '.join(missing)}"
            )
        if unexpected:
            violations.append(
                f"{snapshot}: unexpected entries: {', '.join(unexpected)}"
            )

        for filename in sorted(EXPECTED_SNAPSHOT_FILES & actual_entries):
            path = entry / filename
            if path.is_symlink() or not path.is_file():
                violations.append(
                    f"{snapshot}/{filename}: canonical snapshot entry is not a regular file"
                )

        manifest_path = f"{snapshot}/manifest.json"
        history = _git(
            "log",
            "--first-parent",
            "--format=%H",
            "--reverse",
            head,
            "--",
            manifest_path,
        ).stdout.splitlines()
        if not history:
            violations.append(
                f"{snapshot}: manifest has no first-parent Git history in {head}"
            )
            continue

        first_reviewed = history[0]
        try:
            selected_at_first_appearance = _selected_snapshot(first_reviewed)
        except (RuntimeError, ValueError) as exc:
            violations.append(
                f"{snapshot}: cannot prove first reviewed selection at {first_reviewed}: {exc}"
            )
        else:
            if selected_at_first_appearance != snapshot:
                violations.append(
                    f"{snapshot}: first appeared at {first_reviewed} but was not selected by "
                    f"{LATEST_PATH} (selected {selected_at_first_appearance})"
                )

        violations.extend(
            _post_publication_history_changes(snapshot, first_reviewed, head)
        )

    return violations


def immutable_snapshot_violations(base: str, head: str = "HEAD") -> list[str]:
    """Return changes that violate the immutable publication namespace.

    Every snapshot directory already present in the reviewed base revision is
    byte-immutable. A candidate may add only the single new snapshot selected by
    the candidate's ``latest.json``. A new snapshot must contain exactly the
    canonical five top-level files; nested or arbitrary extra content is rejected
    before it can acquire a permanent Pages URL.
    """
    selected_snapshot = _selected_snapshot(head)
    selected_is_new = not _exists_in_revision(base, selected_snapshot)
    selected_additions: set[str] = set()
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
                continue

            relative = _snapshot_relative_path(snapshot, path)
            if relative is None or relative not in EXPECTED_SNAPSHOT_FILES:
                violations.append(
                    f"{status}\t{path} (unexpected path in selected snapshot)"
                )
                continue
            selected_additions.add(relative)

    if selected_is_new:
        missing = sorted(EXPECTED_SNAPSHOT_FILES - selected_additions)
        if missing:
            violations.append(
                "selected snapshot is incomplete; missing canonical files: " + ", ".join(missing)
            )

    return violations


def _print_violations(title: str, violations: list[str]) -> None:
    print(title, file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the complete immutable snapshot archive and, when --base is "
            "provided, reject unsafe candidate changes relative to a reviewed base"
        )
    )
    parser.add_argument(
        "--base",
        help="Reviewed base commit/ref; omit to perform archive-integrity verification only",
    )
    parser.add_argument("--head", default="HEAD", help="Candidate commit/ref (default: HEAD)")
    args = parser.parse_args()

    try:
        archive_violations = archive_integrity_violations(args.head)
        comparison_violations = (
            immutable_snapshot_violations(args.base, args.head) if args.base else []
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
        else:
            detail = str(exc)
        print(f"ERROR: immutable snapshot verification failed: {detail}", file=sys.stderr)
        return 2

    if archive_violations:
        _print_violations(
            "ERROR: immutable snapshot archive no longer matches its reviewed history:",
            archive_violations,
        )
        return 1

    if comparison_violations:
        _print_violations(
            "ERROR: immutable publication namespace was changed unsafely:",
            comparison_violations,
        )
        return 1

    if args.base:
        print(
            f"Immutable snapshot archive is intact and candidate history is safe relative to {args.base}."
        )
    else:
        print("Immutable snapshot archive matches its first reviewed Git history.")
    return 0


def _cli() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(_cli())
