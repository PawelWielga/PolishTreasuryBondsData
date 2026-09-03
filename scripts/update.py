from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from pipeline import build_dist, sync_current_catalog

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Update and validate Polish Treasury Bonds datasets")
    parser.add_argument("--offline", action="store_true", help="Do not access official websites; rebuild dist from checked-in sources")
    parser.add_argument("--check", action="store_true", help="Fail when rebuilding dist changes tracked generated files")
    args = parser.parse_args()

    if not args.offline:
        sync_current_catalog()

    build_dist()

    if args.check:
        result = subprocess.run(["git", "diff", "--exit-code", "--", "dist"], cwd=ROOT, check=False)
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
