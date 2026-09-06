from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.status import SOURCE_STALE_AFTER_HOURS, parse_instant

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


def source_status_contract_violations(as_of: datetime | None = None) -> list[str]:
    """Return deviations from freshness thresholds and durable attempt-state invariants."""
    reference = as_of or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("Source-status validation reference time must include a timezone")
    reference = reference.astimezone(timezone.utc)
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
            violations.append(f"{SOURCE_STATUS_PATH}: {name} must be an object")
            continue

        actual_hours = item.get("staleAfterHours")
        if actual_hours != expected_hours:
            violations.append(
                f"{SOURCE_STATUS_PATH}: {name}.staleAfterHours must be "
                f"{expected_hours}, got {actual_hours!r}"
            )

        attempt_status = item.get("lastAttemptStatus")
        attempt_at = item.get("lastAttemptAt")
        success_at = item.get("lastSuccessAt")
        durable_status = item.get("status")

        if attempt_status == "NEVER":
            if attempt_at is not None or success_at is not None:
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} with lastAttemptStatus NEVER "
                    "must not have attempt/success timestamps"
                )
            if durable_status != "UNAVAILABLE":
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} with lastAttemptStatus NEVER "
                    "must have durable status UNAVAILABLE"
                )
            continue

        if attempt_status in {"SUCCESS", "FAILED"} and not isinstance(attempt_at, str):
            violations.append(
                f"{SOURCE_STATUS_PATH}: {name}.lastAttemptAt is required for {attempt_status}"
            )
            continue

        attempt_instant = None
        success_instant = None
        if isinstance(attempt_at, str):
            try:
                attempt_instant = parse_instant(attempt_at)
            except ValueError as exc:
                violations.append(f"{SOURCE_STATUS_PATH}: {name}.lastAttemptAt: {exc}")
        if isinstance(success_at, str):
            try:
                success_instant = parse_instant(success_at)
            except ValueError as exc:
                violations.append(f"{SOURCE_STATUS_PATH}: {name}.lastSuccessAt: {exc}")

        if attempt_instant is not None and attempt_instant > reference:
            violations.append(
                f"{SOURCE_STATUS_PATH}: {name}.lastAttemptAt cannot be in the future "
                f"relative to {reference.isoformat()}"
            )
        if success_instant is not None and success_instant > reference:
            violations.append(
                f"{SOURCE_STATUS_PATH}: {name}.lastSuccessAt cannot be in the future "
                f"relative to {reference.isoformat()}"
            )

        if attempt_status == "SUCCESS":
            if success_instant is None:
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} successful attempt must have lastSuccessAt"
                )
            elif attempt_instant is not None and attempt_instant != success_instant:
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} successful lastAttemptAt must equal lastSuccessAt"
                )
            if durable_status not in {"FRESH", "PARTIAL"}:
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} successful durable attempt "
                    "must have status FRESH or PARTIAL"
                )
        elif attempt_status == "FAILED":
            if (
                attempt_instant is not None
                and success_instant is not None
                and attempt_instant < success_instant
            ):
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} failed lastAttemptAt cannot predate lastSuccessAt"
                )
            if success_at is None:
                if durable_status != "UNAVAILABLE":
                    violations.append(
                        f"{SOURCE_STATUS_PATH}: {name} failed durable attempt without prior success "
                        "must have status UNAVAILABLE"
                    )
            elif success_instant is not None and durable_status not in {"STALE", "PARTIAL"}:
                violations.append(
                    f"{SOURCE_STATUS_PATH}: {name} failed durable attempt with prior success "
                    "must have status STALE or PARTIAL"
                )

        if durable_status in {"FRESH", "PARTIAL", "STALE"} and success_at is None:
            violations.append(
                f"{SOURCE_STATUS_PATH}: {name} status {durable_status} requires lastSuccessAt"
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
            f"{MF_SOURCE_ROOT}: directory is missing for referenced MF source artifacts: "
            + ", ".join(sorted(referenced))
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
