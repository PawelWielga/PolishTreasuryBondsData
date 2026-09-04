from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOTS = ("data", "dist", "publication")


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


def main() -> int:
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
