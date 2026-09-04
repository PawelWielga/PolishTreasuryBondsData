from __future__ import annotations

import argparse
import json
import subprocess
import sys
import calendar
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.pipeline import DATA, ROOT, build_dist, load_json, load_series, write_json
from scripts.sources import (
    MF_PAGE_URL,
    NBP_RATES_URL,
    PRODUCT_RULES,
    SourceError,
    cross_check_series,
    default_session,
    discover_mf_workbook,
    fetch,
    fetch_gus_history,
    parse_mf_workbook,
    parse_nbp_rates,
    parse_series_html,
    sha256_bytes,
    utc_now,
)

NBP_CURRENT_RATES_URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe.xml"
NBP_HISTORY_START = "2022-05-06"

COMMON_REQUIRED_CROSS_CHECK_FIELDS = (
    "seriesCode",
    "saleFrom",
    "saleTo",
    "issuePriceMinorUnits",
    "firstPeriodAnnualRatePercent",
)


def required_cross_check_fields(series: dict[str, Any]) -> tuple[str, ...]:
    rules = PRODUCT_RULES[series["productType"]]
    if rules.rate_model in {"NbpReferencePlusMargin", "InflationPlusMargin"}:
        return (*COMMON_REQUIRED_CROSS_CHECK_FIELDS, "marginPercent")
    return COMMON_REQUIRED_CROSS_CHECK_FIELDS


def validate_cross_check_facts(
    series: dict[str, Any], html_facts: dict[str, Any], source_url: str
) -> None:
    for field in required_cross_check_fields(series):
        if html_facts.get(field) is None:
            raise SourceError(
                f"{series['seriesCode']}: required cross-check field {field} could not be parsed from {source_url}"
            )


def sync_series(parsed: list[dict[str, Any]]) -> tuple[int, int]:
    existing = load_series()
    by_code: dict[str, list[dict[str, Any]]] = {}
    for item in existing:
        by_code.setdefault(item["seriesCode"], []).append(item)

    added = corrected = 0
    for candidate in parsed:
        revisions = by_code.setdefault(candidate["seriesCode"], [])
        if any(item["contentHash"] == candidate["contentHash"] for item in revisions):
            continue
        if revisions:
            candidate["termsRevision"] = max(item["termsRevision"] for item in revisions) + 1
            corrected += 1
        else:
            added += 1
        path = (
            DATA / "series" / candidate["productType"] / candidate["seriesCode"]
            / f"terms-v{candidate['termsRevision']}.json"
        )
        write_json(path, candidate)
        revisions.append(candidate)
    return added, corrected


def _merge_revisions(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], identity: str, value: str) -> list[dict[str, Any]]:
    result = [dict(item) for item in existing]
    by_identity: dict[str, list[dict[str, Any]]] = {}
    for item in result:
        by_identity.setdefault(item[identity], []).append(item)
    for candidate in incoming:
        revisions = by_identity.setdefault(candidate[identity], [])
        if any(item[value] == candidate[value] for item in revisions):
            continue
        candidate = dict(candidate)
        candidate["revision"] = max((item["revision"] for item in revisions), default=0) + 1
        result.append(candidate)
        revisions.append(candidate)
    return sorted(result, key=lambda item: (item[identity], item["revision"]))


def sync_gus(session: Any, start_year: int, end_year: int, verified_at: str) -> int:
    path = DATA / "reference" / "gus-cpi.json"
    source = load_json(path)
    incoming = fetch_gus_history(session, start_year, end_year)
    merged = _merge_revisions(source["observations"], incoming, "period", "indexPreviousYear100")
    added = len(merged) - len(source["observations"])
    if added:
        source.update({
            "verifiedAt": verified_at,
            "source": {
                "publisher": "GUS",
                "api": "SDP",
                "baseUrl": "https://api-sdp.stat.gov.pl/api/1.1.0",
                "verifiedAt": verified_at,
            },
            "observations": merged,
        })
        write_json(path, source)
    return added


def _validated_nbp_observations(
    archive: list[dict[str, Any]], current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    scoped_archive = [item for item in archive if item["effectiveFrom"] >= NBP_HISTORY_START]
    if not scoped_archive:
        raise SourceError(f"NBP archive contains no reference-rate data from {NBP_HISTORY_START}")
    if len(current) != 1:
        raise SourceError(f"NBP current-rate file must contain exactly one reference rate, got {len(current)}")

    latest_archive = scoped_archive[-1]
    current_rate = dict(current[0])
    current_rate["source"] = NBP_CURRENT_RATES_URL

    if current_rate["effectiveFrom"] < latest_archive["effectiveFrom"]:
        raise SourceError(
            "NBP current-rate file is older than the archive: "
            f"current={current_rate['effectiveFrom']}, archive={latest_archive['effectiveFrom']}"
        )
    if current_rate["effectiveFrom"] == latest_archive["effectiveFrom"]:
        if current_rate["annualRatePercent"] != latest_archive["annualRatePercent"]:
            raise SourceError(
                "NBP archive and current-rate file disagree on the latest reference rate: "
                f"archive={latest_archive['annualRatePercent']}, current={current_rate['annualRatePercent']}"
            )
        return scoped_archive

    return [*scoped_archive, current_rate]


def sync_nbp(session: Any, verified_at: str) -> int:
    path = DATA / "reference" / "nbp-reference-rates.json"
    source = load_json(path)
    archive_xml = fetch(session, NBP_RATES_URL, "application/xml,text/xml").decode("utf-8-sig")
    current_xml = fetch(session, NBP_CURRENT_RATES_URL, "application/xml,text/xml").decode("utf-8-sig")
    archive = parse_nbp_rates(archive_xml)
    current = parse_nbp_rates(current_xml)
    incoming = _validated_nbp_observations(archive, current)
    merged = _merge_revisions(source["observations"], incoming, "effectiveFrom", "annualRatePercent")
    added = len(merged) - len(source["observations"])
    if added:
        source.update({
            "verifiedAt": verified_at,
            "source": {
                "publisher": "NBP",
                "url": NBP_RATES_URL,
                "currentUrl": NBP_CURRENT_RATES_URL,
                "verifiedAt": verified_at,
            },
            "observations": merged,
        })
        write_json(path, source)
    return added


def sync_mf(session: Any, verified_date: str, workbook_path: Path | None = None, cross_check: bool = True) -> tuple[int, int]:
    if workbook_path:
        workbook_content = workbook_path.read_bytes()
        workbook_url = MF_PAGE_URL
    else:
        page = fetch(session, MF_PAGE_URL, "text/html,application/xhtml+xml").decode("utf-8")
        workbook_url = discover_mf_workbook(page)
        workbook_content = fetch(session, workbook_url, "application/vnd.ms-excel,application/octet-stream")
    parsed = [
        item for item in parse_mf_workbook(workbook_content, workbook_url, verified_date)
        if _can_still_be_outstanding(item, date.fromisoformat(verified_date))
    ]

    if cross_check:
        for series in parsed:
            cross_source = series["provenance"].get("crossCheck")
            if not cross_source:
                continue
            html = fetch(session, cross_source["url"], "text/html,application/xhtml+xml").decode("utf-8")
            html_facts = parse_series_html(html)
            validate_cross_check_facts(series, html_facts, cross_source["url"])
            cross_check_series(series, html_facts)
            cross_source["verifiedAt"] = verified_date

    added, corrected = sync_series(parsed)
    if added or corrected:
        artifact = DATA / "sources" / "mf" / f"{sha256_bytes(workbook_content)}.xls"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if not artifact.exists():
            artifact.write_bytes(workbook_content)
    return added, corrected


def _can_still_be_outstanding(series: dict[str, Any], as_of: date) -> bool:
    months = {
        "OTS": 3, "ROR": 12, "DOR": 24, "TOS": 36,
        "COI": 48, "EDO": 120, "ROS": 72, "ROD": 144,
    }[series["productType"]]
    sold_to = date.fromisoformat(series["saleTo"])
    month_index = sold_to.month - 1 + months
    year = sold_to.year + month_index // 12
    month = month_index % 12 + 1
    maturity = date(year, month, min(sold_to.day, calendar.monthrange(year, month)[1]))
    return maturity >= as_of


def mark_source_status(source_name: str, success: bool, message: str | None = None) -> None:
    path = DATA / "source-status.json"
    document = load_json(path)
    item = document["sources"][source_name]
    transition_source_status(item, success, utc_now(), message)
    write_json(path, document)


def transition_source_status(item: dict[str, Any], success: bool, now: str, message: str | None = None) -> None:
    item["lastAttemptAt"] = now
    item["lastAttemptStatus"] = "SUCCESS" if success else "FAILED"
    item["message"] = message
    if success:
        item["status"] = "FRESH"
        item["lastSuccessAt"] = now
    elif item.get("lastSuccessAt"):
        item["status"] = "STALE"
    else:
        item["status"] = "UNAVAILABLE"


def run_live(args: argparse.Namespace) -> None:
    session = default_session()
    verified_date = args.as_of or date.today().isoformat()
    verified_at = f"{verified_date}T00:00:00Z"
    tasks = (
        ("mf", lambda: sync_mf(session, verified_date, args.mf_workbook, not args.skip_cross_check)),
        ("gus", lambda: sync_gus(session, args.gus_start_year, int(verified_date[:4]), verified_at)),
        ("nbp", lambda: sync_nbp(session, verified_at)),
    )
    for source_name, operation in tasks:
        try:
            result = operation()
            mark_source_status(source_name, True, f"Update result: {result}")
        except Exception as exc:
            mark_source_status(source_name, False, str(exc))
            raise SourceError(f"{source_name.upper()} refresh failed; financial data was not published: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Update and validate Polish Treasury Bonds datasets")
    parser.add_argument("--offline", action="store_true", help="Rebuild only from checked-in normalized sources")
    parser.add_argument("--check", action="store_true", help="Fail if generated output or normalized layout is stale")
    parser.add_argument("--mf-only", action="store_true", help="Refresh only the MF workbook/catalog")
    parser.add_argument("--mf-workbook", type=Path, help="Use a local official MF workbook instead of downloading it")
    parser.add_argument("--skip-cross-check", action="store_true", help="Skip live HTML cross-check (fixtures/bootstrap only)")
    parser.add_argument("--gus-start-year", type=int, default=2014)
    parser.add_argument("--as-of", help="Deterministic verification date (YYYY-MM-DD)")
    args = parser.parse_args()

    try:
        if not args.offline:
            if args.mf_only:
                verified_date = args.as_of or date.today().isoformat()
                sync_mf(default_session(), verified_date, args.mf_workbook, not args.skip_cross_check)
                mark_source_status("mf", True, "MF-only refresh succeeded")
            else:
                run_live(args)
        revision = build_dist()
        print(f"Validated dataset revision: {revision}")
    except (SourceError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.check:
        result = subprocess.run(
            ["git", "diff", "--exit-code", "--", "data", "dist", "publication"], cwd=ROOT, check=False
        )
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
