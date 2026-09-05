from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.status import SOURCE_STALE_AFTER_HOURS

ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOTS = ("data", "dist", "publication")
SOURCE_STATUS_PATH = "data/source-status.json"


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


def main() -> int:
    contract_violations = source_status_contract_violations()
    if contract_violations:
        print("ERROR: source freshness contract is invalid:", file=sys.stderr)
        for violation in contract_violations:
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

    print("Normalized data and generated publication tree are reproducible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
