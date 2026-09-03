from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changed_files() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=True, text=True, capture_output=True
    )
    return [line[3:] for line in result.stdout.splitlines() if len(line) > 3]


def main() -> None:
    files = changed_files()
    latest = json.loads((ROOT / "publication" / "v1" / "latest.json").read_text(encoding="utf-8"))
    new_terms = [path for path in files if path.startswith("data/series/") and path.endswith(".json")]
    references = [path for path in files if path.startswith("data/reference/")]
    snapshots = [path for path in files if "/snapshots/" in path]
    print("## Verified official-data refresh")
    print()
    print(f"- candidate dataset revision: `{latest['datasetRevision']}`")
    print(f"- new or corrected series term files: {len(new_terms)}")
    print(f"- changed reference-data files: {len(references)}")
    print(f"- generated snapshot files: {len(snapshots)}")
    print("- primary series source: https://www.gov.pl/web/finanse/obligacje-detaliczne1")
    print("- CPI source: https://api-sdp.stat.gov.pl")
    print("- NBP source: https://nbp.pl/podstawowe-stopy-procentowe-archiwum/")
    print()
    print("All cross-source, schema, semantic, golden-fixture and immutable-snapshot checks passed.")
    print("Any source disagreement stops the workflow before this pull request is created.")


if __name__ == "__main__":
    main()
