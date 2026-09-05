from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLICATION = ROOT / "publication" / "v1"
SCHEMAS = ROOT / "schemas"
SOURCE_STALE_AFTER_HOURS = {
    "mf": 744,
    "gus": 744,
    "nbp": 168,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def canonical_instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Runtime status reference time must include a timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def derive_source_health(item: dict[str, Any], as_of: datetime) -> str:
    """Derive public freshness from durable successful verification state.

    `lastSuccessAt` and `staleAfterHours` are authoritative for freshness. A
    durable PARTIAL marker is preserved while it is still within its freshness
    window. Transient FAILED attempts are deliberately not required for this
    calculation, so a failed updater cannot keep a source FRESH merely because
    its working-tree status was never committed.

    A success timestamp in the future is invalid operational state. It is
    rendered as UNAVAILABLE instead of raising, so the scheduled Pages health
    refresh can still publish a conservative status rather than leaving an old
    public FRESH result in place indefinitely.
    """
    last_success = item.get("lastSuccessAt")
    if not last_success:
        return "UNAVAILABLE"

    as_of_utc = as_of.astimezone(timezone.utc)
    success_at = parse_instant(last_success)
    if success_at > as_of_utc:
        return "UNAVAILABLE"

    stale_at = success_at + timedelta(hours=int(item["staleAfterHours"]))
    if as_of_utc >= stale_at:
        return "STALE"

    if item.get("status") == "PARTIAL":
        return "PARTIAL"
    return "FRESH"


def build_runtime_status(
    dataset_revision: str,
    source: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": "1.0",
        "datasetRevision": dataset_revision,
        "sources": {},
    }
    for name in ("mf", "gus", "nbp"):
        item = source["sources"][name]
        expected_stale_after = SOURCE_STALE_AFTER_HOURS[name]
        configured_stale_after = item.get("staleAfterHours")
        contract_mismatch = configured_stale_after != expected_stale_after
        normalized_item = {**item, "staleAfterHours": expected_stale_after}
        message = item.get("message")
        if contract_mismatch:
            message = (
                f"Invalid staleAfterHours contract for {name}: "
                f"expected {expected_stale_after}, got {configured_stale_after!r}"
            )

        result["sources"][name] = {
            "status": (
                "UNAVAILABLE"
                if contract_mismatch
                else derive_source_health(normalized_item, as_of)
            ),
            "lastAttemptAt": item.get("lastAttemptAt"),
            "lastAttemptStatus": item.get("lastAttemptStatus", "NEVER"),
            "lastSuccessAt": item.get("lastSuccessAt"),
            "staleAfterHours": expected_stale_after,
            "message": message,
        }
    return result


def validate_status(document: dict[str, Any]) -> None:
    schema = load_json(SCHEMAS / "source-status-v1.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        details = "\n".join(f"- {'/'.join(map(str, error.path))}: {error.message}" for error in errors)
        raise ValueError(f"status.json does not satisfy its schema:\n{details}")


def render_public_status(as_of: datetime) -> dict[str, Any]:
    latest = load_json(PUBLICATION / "latest.json")
    source = load_json(DATA / "source-status.json")
    status = build_runtime_status(latest["datasetRevision"], source, as_of)
    validate_status(status)
    write_json(PUBLICATION / "status.json", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render mutable public source freshness without changing financial snapshots"
    )
    parser.add_argument(
        "--as-of",
        help="Deterministic UTC/date-time reference for tests or manual runs; defaults to current UTC time",
    )
    args = parser.parse_args()

    as_of = parse_instant(args.as_of) if args.as_of else datetime.now(timezone.utc)
    status = render_public_status(as_of)
    print(
        f"Rendered runtime status for {status['datasetRevision']} at {canonical_instant(as_of)}: "
        + ", ".join(f"{name}={item['status']}" for name, item in status["sources"].items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
