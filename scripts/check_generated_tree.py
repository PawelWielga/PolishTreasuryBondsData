from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGED_ROOTS = ("data", "publication")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout


def generated_tree_changes(include_cached: bool = True) -> list[str]:
    """Return managed paths that differ from HEAD or the staged candidate.

    Normal CI rebuilds from the checked-in HEAD. The updater stages its live
    candidate first and then uses ``include_cached=False`` so the Git index is the
    accepted candidate baseline while any subsequent rebuild drift still fails.
    """
    changed: set[str] = set()
    commands: list[tuple[str, ...]] = [
        ("diff", "--name-only", "--", *MANAGED_ROOTS),
        ("ls-files", "--others", "--exclude-standard", "--", *MANAGED_ROOTS),
    ]
    if include_cached:
        commands.insert(1, ("diff", "--cached", "--name-only", "--", *MANAGED_ROOTS))

    for args in commands:
        changed.update(line for line in _git(*args).splitlines() if line)
    return sorted(changed)


def main(*, against_index: bool = False) -> int:
    changes = generated_tree_changes(include_cached=not against_index)
    if changes:
        baseline = "staged candidate" if against_index else "HEAD"
        print(
            "ERROR: canonical/generated repository state is not reproducible against "
            f"{baseline}; the following paths changed or were created:",
            file=sys.stderr,
        )
        for path in changes:
            print(f"- {path}", file=sys.stderr)
        return 1

    print("Canonical facts and generated publication are reproducible.")
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Verify deterministic canonical/publication repository state"
    )
    parser.add_argument(
        "--against-index",
        action="store_true",
        help=(
            "Use the staged Git index as the candidate baseline, ignoring intended "
            "staged changes while still rejecting unstaged or untracked rebuild drift"
        ),
    )
    args = parser.parse_args()
    return main(against_index=args.against_index)


if __name__ == "__main__":
    raise SystemExit(_cli())
