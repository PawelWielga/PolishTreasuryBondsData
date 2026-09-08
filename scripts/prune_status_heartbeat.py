from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS_RELATIVE_PATH = Path("data/source-status.json")
PUBLIC_STATUS_RELATIVE_PATH = Path("publication/v1/status.json")
STATUS_PATH = ROOT / STATUS_RELATIVE_PATH
MANAGED_PATHS = ("data", "publication")
HEARTBEAT_MANAGED_PATHS = {
    STATUS_RELATIVE_PATH.as_posix(),
    PUBLIC_STATUS_RELATIVE_PATH.as_posix(),
}
HEARTBEAT_REFRESH_NUMERATOR = 3
HEARTBEAT_REFRESH_DENOMINATOR = 4
HEARTBEAT_FIELDS = {
    "status",
    "lastAttemptAt",
    "lastAttemptStatus",
    "lastSuccessAt",
    "message",
}


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _non_heartbeat_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in HEARTBEAT_FIELDS}


def heartbeat_due(previous: dict[str, Any], candidate: dict[str, Any]) -> bool:
    stale_after_hours = previous.get("staleAfterHours")
    if not isinstance(stale_after_hours, int) or stale_after_hours <= 0:
        raise ValueError("staleAfterHours must be a positive integer")

    previous_success = _parse_utc(previous.get("lastSuccessAt"), "lastSuccessAt")
    candidate_success = _parse_utc(candidate.get("lastSuccessAt"), "lastSuccessAt")
    if candidate_success < previous_success:
        raise ValueError("candidate lastSuccessAt cannot move backwards")

    elapsed_seconds = (candidate_success - previous_success).total_seconds()
    stale_seconds = stale_after_hours * 60 * 60
    return (
        elapsed_seconds * HEARTBEAT_REFRESH_DENOMINATOR
        >= stale_seconds * HEARTBEAT_REFRESH_NUMERATOR
    )


def should_publish_status_candidate(
    previous: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    """Keep a status-only candidate when it is meaningful or its heartbeat is due."""
    if set(previous) != set(candidate) or set(previous) != {"sources"}:
        return True

    previous_sources = previous.get("sources")
    candidate_sources = candidate.get("sources")
    if not isinstance(previous_sources, dict) or not isinstance(candidate_sources, dict):
        return True
    if set(previous_sources) != set(candidate_sources):
        return True

    heartbeat_is_due = False
    for source_name in sorted(previous_sources):
        old_item = previous_sources[source_name]
        new_item = candidate_sources[source_name]
        if not isinstance(old_item, dict) or not isinstance(new_item, dict):
            return True
        if old_item == new_item:
            continue

        # Configuration/policy changes are substantive even inside source-status.json.
        if _non_heartbeat_fields(old_item) != _non_heartbeat_fields(new_item):
            return True

        # Never suppress failures or ambiguous transitions.
        if new_item.get("lastAttemptStatus") != "SUCCESS":
            return True
        if new_item.get("lastAttemptAt") != new_item.get("lastSuccessAt"):
            return True

        heartbeat_is_due = heartbeat_is_due or heartbeat_due(old_item, new_item)

    return heartbeat_is_due


def is_status_only_candidate(changed_paths: set[str]) -> bool:
    return (
        STATUS_RELATIVE_PATH.as_posix() in changed_paths
        and changed_paths <= HEARTBEAT_MANAGED_PATHS
    )


def _changed_managed_paths() -> set[str]:
    result = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *MANAGED_PATHS,
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return paths


def _load_head_status() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"HEAD:{STATUS_RELATIVE_PATH.as_posix()}"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _restore_status_files() -> None:
    subprocess.run(
        [
            "git",
            "restore",
            "--worktree",
            "--source=HEAD",
            "--",
            STATUS_RELATIVE_PATH.as_posix(),
            PUBLIC_STATUS_RELATIVE_PATH.as_posix(),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    changed_paths = _changed_managed_paths()
    if not is_status_only_candidate(changed_paths):
        print("Heartbeat pruning skipped: candidate contains substantive managed changes.")
        return 0

    previous = _load_head_status()
    candidate = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    if should_publish_status_candidate(previous, candidate):
        print("Status-only candidate is meaningful or its heartbeat is due.")
        return 0

    _restore_status_files()
    print("Dropped premature source-status heartbeat and its public projection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
