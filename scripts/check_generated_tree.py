from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.status import SOURCE_STALE_AFTER_HOURS

ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOTS = ("data", "dist", "publication")
SOURCE_STATUS_PATH = "data/source-status.json"
MF_SERIES_ROOT = "data/series"
MF_SOURCE_ROOT = "data/sources/mf"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout


def generated_tree_changes() -> list[str]:
    """Return tracked, staged or untracked changes under generated/data roots."""
    changed: set[str] = set()
    separator = "--"

    for args in (
        ("diff", "--name-only", separator, *GENERATED_ROOTS),
        ("diff", "--cached", "--name-only", separator, *GENERATED_ROOTS),
        ("ls-files", "--others", "--exclude-standard", separator, *GENERATED_ROOTS),
    ):
        changed.update(line for line in _git(*args).splitlines() if line)

    return sorted(changed)


def source_status_contract_violations() -> list[str]:
    """Return deviations from the production source-freshness contract."""
    path = ROOT / SOURCE_STATUS_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{SOURCE_STATUS_PATH}: cannot read valid JSON: {exc}"]

    sources = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(sources, dict):
        return [f"{SOURCE_STATUS_PATH}: sources must be an object"]

    violations: list[str] = []
    expected_names = set(SOURCE_STALE_AFTER_HOURS)
    actual_names = set(sources)
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    if missing:
        violations.append(f"{SOURCE_STATUS_PATH}: missing sources: {', '.join(missing)}")
    if extra:
        violations.append(f"{SOURCE_STATUS_PATH}: unexpected sources: {', '.join(extra)}")

    for name, expected_hours in SOURCE_STALE_AFTER_HOURS.items():
        item = sources.get(name)
        if not isinstance(item, dict):
            continue
        actual_hours = item.get("staleAfterHours")
        if actual_hours != expected_hours:
            violations.append(
                f"{SOURCE_STATUS_PATH}: {name}.staleAfterHours must be "
                f"{expected_hours}, got {actual_hours!r}"
            )
    return violations


def mf_source_artifact_violations() -> list[str]:
    """Verify every MF provenance SHA resolves to exactly the checked-in XLS bytes."""
    series_root = ROOT / MF_SERIES_ROOT
    source_root = ROOT / MF_SOURCE_ROOT
    violations: list[str] = []
    referenced: set[str] = set()

    if series_root.exists():
        for path in sorted(series_root.glob("*/*/terms-v*.json")):
            relative = path.relative_to(ROOT).as_posix()
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                violations.append(f"{relative}: cannot read valid JSON: {exc}")
                continue

            primary = document.get("provenance", {}).get("primary", {})
            digest = primary.get("sha256") if isinstance(primary, dict) else None
            if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
                violations.append(f"{relative}: invalid provenance.primary.sha256 {digest!r}")
                continue
            referenced.add(digest)

    if referenced and not source_root.is_dir():
        return violations + [
            f"{MF_SOURCE_ROOT}: directory is missing but MF provenance references source artifacts"
        ]

    for digest in sorted(referenced):
        artifact = source_root / f"{digest}.xls"
        relative = artifact.relative_to(ROOT).as_posix()
        if artifact.is_symlink() or not artifact.is_file():
            violations.append(f"{relative}: referenced MF source artifact is missing or not a regular file")
            continue
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual != digest:
            violations.append(
                f"{relative}: content SHA-256 mismatch; expected {digest}, got {actual}"
            )

    if source_root.is_dir():
        expected_names = {f"{digest}.xls" for digest in referenced}
        actual_names = {entry.name for entry in source_root.iterdir()}
        unexpected = sorted(actual_names - expected_names)
        if unexpected:
            violations.append(
                f"{MF_SOURCE_ROOT}: unreferenced or unexpected entries: {', '.join(unexpected)}"
            )

    return violations


def main() -> int:
    contract_violations = source_status_contract_violations()
    if contract_violations:
        print("ERROR: source freshness contract is invalid:", file=sys.stderr)
        for violation in contract_violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    artifact_violations = mf_source_artifact_violations()
    if artifact_violations:
        print("ERROR: MF source artifact integrity is invalid:", file=sys.stderr)
        for violation in artifact_violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    changes = generated_tree_changes()
    if changes:
        print(
            "ERROR: normalized/generated repository state is not reproducible; "
            "the following paths changed or were created:",
            file=sys.stderr,
        )
        for path in changes:
            print(f"- {path}", file=sys.stderr)
        return 1

    print("Normalized data, source artifacts and generated publication tree are reproducible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
