from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_ROOT = "publication/v1/snapshots"
LATEST_PATH = "publication/v1/latest.json"
EXPECTED_DATA_FILES = frozenset(
    {
        "catalog.json",
        "product-definitions.json",
        "gus-cpi.json",
        "nbp-reference-rates.json",
    }
)
EXPECTED_SNAPSHOT_FILES = EXPECTED_DATA_FILES | {"manifest.json"}


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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


def _selected_snapshot_from_json(text: str, label: str) -> str:
    try:
        latest = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{LATEST_PATH} is not valid JSON in {label}") from exc
    revision = latest.get("datasetRevision")
    if not isinstance(revision, str) or not revision:
        raise ValueError(f"{LATEST_PATH} has no valid datasetRevision in {label}")
    expected_manifest = f"snapshots/{revision}/manifest.json"
    if latest.get("manifest") != expected_manifest:
        raise ValueError(
            f"{LATEST_PATH} manifest does not match datasetRevision in {label}: "
            f"expected {expected_manifest!r}, got {latest.get('manifest')!r}"
        )
    return f"{SNAPSHOTS_ROOT}/{revision}"


def _selected_snapshot(revision: str) -> str:
    text = _git("show", f"{revision}:{LATEST_PATH}").stdout
    return _selected_snapshot_from_json(text, revision)


def _paths_in_revision(revision: str, root: str) -> set[str]:
    result = _git("ls-tree", "-r", "--name-only", revision, "--", root)
    return {line for line in result.stdout.splitlines() if line}


def _snapshot_dirs_in_revision(revision: str) -> set[str]:
    return {
        snapshot
        for path in _paths_in_revision(revision, SNAPSHOTS_ROOT)
        if (snapshot := _snapshot_directory(path)) is not None
    }


def _document_count(document: dict) -> int:
    for key in ("series", "productDefinitions", "observations"):
        value = document.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _validate_snapshot_directory(snapshot: Path) -> list[str]:
    violations: list[str] = []
    if snapshot.is_symlink() or not snapshot.is_dir():
        return [f"{snapshot.relative_to(ROOT)}: snapshot entry is not a real directory"]

    entries = {entry.name: entry for entry in snapshot.iterdir()}
    missing = sorted(EXPECTED_SNAPSHOT_FILES - set(entries))
    unexpected = sorted(set(entries) - EXPECTED_SNAPSHOT_FILES)
    if missing:
        violations.append(
            f"{snapshot.relative_to(ROOT)}: missing canonical files: {', '.join(missing)}"
        )
    if unexpected:
        violations.append(
            f"{snapshot.relative_to(ROOT)}: unexpected entries: {', '.join(unexpected)}"
        )

    for name in EXPECTED_SNAPSHOT_FILES & set(entries):
        path = entries[name]
        if path.is_symlink() or not path.is_file():
            violations.append(
                f"{path.relative_to(ROOT)}: canonical snapshot entry is not a regular file"
            )

    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        return violations
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        violations.append(f"{manifest_path.relative_to(ROOT)}: invalid JSON: {exc}")
        return violations

    revision = snapshot.name
    if manifest.get("datasetRevision") != revision:
        violations.append(
            f"{manifest_path.relative_to(ROOT)}: datasetRevision does not match directory name"
        )

    files = manifest.get("files")
    if not isinstance(files, dict):
        violations.append(f"{manifest_path.relative_to(ROOT)}: files must be an object")
        return violations
    if set(files) != EXPECTED_DATA_FILES:
        violations.append(
            f"{manifest_path.relative_to(ROOT)}: manifest files must be exactly "
            f"{sorted(EXPECTED_DATA_FILES)}"
        )
        return violations

    for name in sorted(EXPECTED_DATA_FILES):
        entry = files[name]
        if not isinstance(entry, dict):
            violations.append(f"{manifest_path.relative_to(ROOT)}: {name} entry is invalid")
            continue
        if entry.get("path") != name:
            violations.append(
                f"{manifest_path.relative_to(ROOT)}: {name}.path must equal {name!r}"
            )
        data_path = snapshot / name
        if not data_path.is_file() or data_path.is_symlink():
            continue
        content = data_path.read_bytes()
        actual_hash = _sha256(content)
        if entry.get("sha256") != actual_hash:
            violations.append(
                f"{data_path.relative_to(ROOT)}: manifest SHA-256 mismatch; "
                f"expected {entry.get('sha256')!r}, got {actual_hash}"
            )
        try:
            document = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            violations.append(f"{data_path.relative_to(ROOT)}: invalid JSON: {exc}")
            continue
        if document.get("schemaVersion") != entry.get("schemaVersion"):
            violations.append(
                f"{data_path.relative_to(ROOT)}: schemaVersion disagrees with manifest"
            )
        if _document_count(document) != entry.get("count"):
            violations.append(
                f"{data_path.relative_to(ROOT)}: record count disagrees with manifest"
            )
    return violations


def archive_integrity_violations() -> list[str]:
    """Validate the immutable archive as it exists in the checked-out tree.

    This deliberately does not attempt to defend against a repository administrator
    rewriting both data and validation history. The trust boundary is the reviewed
    PR base plus the current tree, not self-attestation across all Git history.
    """
    root = ROOT / SNAPSHOTS_ROOT
    if root.is_symlink() or not root.is_dir():
        return [f"{SNAPSHOTS_ROOT}: snapshot archive is missing or not a real directory"]

    violations: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        violations.extend(_validate_snapshot_directory(entry))

    latest_path = ROOT / LATEST_PATH
    if not latest_path.is_file() or latest_path.is_symlink():
        violations.append(f"{LATEST_PATH}: latest pointer is missing or not a regular file")
        return violations
    try:
        selected = _selected_snapshot_from_json(
            latest_path.read_text(encoding="utf-8"), "working tree"
        )
    except ValueError as exc:
        violations.append(str(exc))
        return violations
    if not (ROOT / selected).is_dir():
        violations.append(f"{LATEST_PATH}: selected snapshot does not exist: {selected}")
    return violations


def immutable_snapshot_violations(base: str, head: str = "HEAD") -> list[str]:
    """Reject mutation of snapshots already present in the reviewed base.

    A candidate may keep the current selected snapshot or add exactly one complete
    new snapshot selected by ``latest.json``. Existing snapshot namespaces are
    append-only and cannot be modified, deleted or revisited as a rollback target.
    """
    base_selected = _selected_snapshot(base)
    head_selected = _selected_snapshot(head)
    base_snapshots = _snapshot_dirs_in_revision(base)
    head_paths = _paths_in_revision(head, SNAPSHOTS_ROOT)
    head_snapshots = {
        snapshot
        for path in head_paths
        if (snapshot := _snapshot_directory(path)) is not None
    }

    violations: list[str] = []
    if head_selected != base_selected and head_selected in base_snapshots:
        violations.append(
            f"{LATEST_PATH}: candidate selects older existing snapshot {head_selected}; "
            f"reviewed base selected {base_selected}"
        )

    diff = _git(
        "diff",
        "--name-status",
        "--find-renames",
        f"{base}...{head}",
        "--",
        SNAPSHOTS_ROOT,
    ).stdout
    new_files: dict[str, set[str]] = {}
    for raw_line in diff.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        status = parts[0]
        for path in parts[1:]:
            snapshot = _snapshot_directory(path)
            if snapshot is None:
                violations.append(f"{status}\t{path} (invalid snapshot path)")
                continue
            if snapshot in base_snapshots:
                violations.append(f"{status}\t{path} (reviewed snapshot is immutable)")
                continue
            if not status.startswith("A"):
                violations.append(f"{status}\t{path} (new snapshot may contain additions only)")
                continue
            if snapshot != head_selected:
                violations.append(
                    f"{status}\t{path} (new snapshot is not selected by {LATEST_PATH})"
                )
                continue
            relative = _snapshot_relative_path(snapshot, path)
            if relative is None or relative not in EXPECTED_SNAPSHOT_FILES:
                violations.append(f"{status}\t{path} (unexpected path in new snapshot)")
                continue
            new_files.setdefault(snapshot, set()).add(relative)

    newly_present = head_snapshots - base_snapshots
    if head_selected not in base_snapshots:
        if newly_present != {head_selected}:
            violations.append(
                f"candidate must add exactly the selected snapshot {head_selected}; "
                f"new snapshots are {sorted(newly_present)}"
            )
        selected_paths = {
            relative
            for path in head_paths
            if _snapshot_directory(path) == head_selected
            if (relative := _snapshot_relative_path(head_selected, path)) is not None
        }
        missing = sorted(EXPECTED_SNAPSHOT_FILES - selected_paths)
        unexpected = sorted(selected_paths - EXPECTED_SNAPSHOT_FILES)
        if missing:
            violations.append(
                "selected new snapshot is incomplete; missing: " + ", ".join(missing)
            )
        if unexpected:
            violations.append(
                "selected new snapshot contains unexpected paths: " + ", ".join(unexpected)
            )
    elif newly_present:
        violations.append(
            "candidate added unselected snapshot directories: " + ", ".join(sorted(newly_present))
        )

    return violations


def _print_violations(title: str, violations: list[str]) -> None:
    print(title, file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the current immutable snapshot archive and optionally reject "
            "candidate changes to snapshot namespaces already present in a reviewed base"
        )
    )
    parser.add_argument(
        "--base",
        help="Reviewed base commit/ref; omit for current-tree archive validation only",
    )
    args = parser.parse_args()

    try:
        archive_violations = archive_integrity_violations()
        comparison_violations = (
            immutable_snapshot_violations(args.base) if args.base else []
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
        else:
            detail = str(exc)
        print(f"ERROR: immutable snapshot verification failed: {detail}", file=sys.stderr)
        return 2

    if archive_violations:
        _print_violations("ERROR: immutable snapshot archive is invalid:", archive_violations)
        return 1
    if comparison_violations:
        _print_violations(
            "ERROR: candidate violates immutable snapshot invariants:",
            comparison_violations,
        )
        return 1

    if args.base:
        print(f"Snapshot archive is valid and candidate is safe relative to {args.base}.")
    else:
        print("Snapshot archive and manifests are internally consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
