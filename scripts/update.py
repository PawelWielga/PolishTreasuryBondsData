from __future__ import annotations

import argparse
import calendar
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_generated_tree import main as check_generated_tree
from scripts.pipeline import (
    DATA,
    ROOT,
    GUS_HISTORY_START,
    NBP_HISTORY_START,
    build_dist,
    load_json,
    load_series,
    write_json,
)
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
    validate_official_cross_check_url,
    validate_official_mf_workbook_url,
)

NBP_CURRENT_RATES_URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe.xml"
MANAGED_PATHS = ("data", "dist", "publication")
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
        if revisions:
            current_revision = max(revisions, key=lambda item: item["termsRevision"])
            if current_revision["contentHash"] == candidate["contentHash"]:
                continue
            candidate["termsRevision"] = current_revision["termsRevision"] + 1
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
        if revisions:
            current_revision = max(revisions, key=lambda item: item["revision"])
            if current_revision[value] == candidate[value]:
                continue
            next_revision = current_revision["revision"] + 1
        else:
            next_revision = 1
        candidate = dict(candidate)
        candidate["revision"] = next_revision
        result.append(candidate)
        revisions.append(candidate)
    return sorted(result, key=lambda item: (item[identity], item["revision"]))


def _validated_gus_observations(
    incoming: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    start_year: int,
    end_year: int,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    if start_year > end_year:
        raise SourceError(f"GUS invalid requested range: {start_year}-{end_year}")

    periods = [item["period"] for item in incoming]
    if len(periods) != len(set(periods)):
        raise SourceError("GUS source contains duplicate monthly observations")
    periods = sorted(periods)
    if not periods:
        raise SourceError("GUS source returned no CPI observations")

    expected_start = f"{start_year:04d}-01"
    if periods[0] != expected_start:
        raise SourceError(f"GUS source no longer covers requested history from {expected_start}")

    by_year: dict[str, list[str]] = {}
    for period in periods:
        by_year.setdefault(period[:4], []).append(period)
    years = sorted(by_year)
    latest_year = int(years[-1])
    if latest_year > end_year:
        raise SourceError(f"GUS source returned future year {latest_year} beyond requested {end_year}")
    if latest_year < end_year - 1:
        raise SourceError(
            f"GUS source has no recent coverage for {end_year - 1} or {end_year}; latest year is {latest_year}"
        )
    if as_of is not None and end_year >= as_of.year - 1:
        latest_period_year, latest_period_month = (int(part) for part in periods[-1].split("-"))
        lag_months = (as_of.year - latest_period_year) * 12 + as_of.month - latest_period_month
        if lag_months < 0:
            raise SourceError(
                f"GUS source returned future CPI period {periods[-1]} for verification date {as_of.isoformat()}"
            )
        if lag_months > 2:
            raise SourceError(
                f"GUS source latest CPI period {periods[-1]} is too old for verification date {as_of.isoformat()}"
            )

    expected_years = [str(year) for year in range(start_year, latest_year + 1)]
    if years != expected_years:
        missing_years = sorted(set(expected_years) - set(years))
        raise SourceError(f"GUS source coverage is missing years: {missing_years}")

    for year in years:
        periods_for_year = by_year[year]
        required_count = 12 if int(year) < latest_year else len(periods_for_year)
        expected_periods = [f"{year}-{month:02d}" for month in range(1, required_count + 1)]
        if periods_for_year != expected_periods:
            raise SourceError(f"GUS source coverage is incomplete/non-contiguous for {year}")

    incoming_periods = set(periods)
    previously_published_periods = {
        item["period"]
        for item in existing
        if start_year <= int(item["period"][:4]) <= end_year
    }
    missing_periods = sorted(previously_published_periods - incoming_periods)
    if missing_periods:
        raise SourceError(
            "GUS source lost previously published CPI periods: " + ", ".join(missing_periods)
        )
    return incoming


def sync_gus(session: Any, start_year: int, end_year: int, verified_at: str) -> int:
    path = DATA / "reference" / "gus-cpi.json"
    source = load_json(path)
    incoming = _validated_gus_observations(
        fetch_gus_history(session, start_year, end_year),
        source["observations"],
        start_year,
        end_year,
        date.fromisoformat(verified_at[:10]),
    )
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
    archive: list[dict[str, Any]],
    current: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    scoped_archive = [item for item in archive if item["effectiveFrom"] >= NBP_HISTORY_START]
    if not scoped_archive or scoped_archive[0]["effectiveFrom"] != NBP_HISTORY_START:
        raise SourceError(f"NBP archive no longer covers required history from {NBP_HISTORY_START}")
    if len(current) != 1:
        raise SourceError(f"NBP current-rate file must contain exactly one reference rate, got {len(current)}")

    latest_archive = scoped_archive[-1]
    if as_of is not None and date.fromisoformat(latest_archive["effectiveFrom"]) > as_of:
        raise SourceError(
            f"NBP source returned future reference-rate date {latest_archive['effectiveFrom']} "
            f"for verification date {as_of.isoformat()}"
        )
    archive_dates = {item["effectiveFrom"] for item in scoped_archive}
    previously_published_dates = {
        item["effectiveFrom"]
        for item in existing
        if item["effectiveFrom"] >= NBP_HISTORY_START
    }
    missing_dates = sorted(previously_published_dates - archive_dates)
    if missing_dates:
        raise SourceError(
            "NBP archive lost previously published reference-rate dates: " + ", ".join(missing_dates)
        )

    current_rate = current[0]
    if current_rate["effectiveFrom"] != latest_archive["effectiveFrom"]:
        raise SourceError(
            "NBP archive and current-rate file are not synchronized: "
            f"archive={latest_archive['effectiveFrom']}, current={current_rate['effectiveFrom']}"
        )
    if current_rate["annualRatePercent"] != latest_archive["annualRatePercent"]:
        raise SourceError(
            "NBP archive and current-rate file disagree on the latest reference rate: "
            f"archive={latest_archive['annualRatePercent']}, current={current_rate['annualRatePercent']}"
        )
    return scoped_archive


def sync_nbp(session: Any, verified_at: str) -> int:
    path = DATA / "reference" / "nbp-reference-rates.json"
    source = load_json(path)
    archive_xml = fetch(session, NBP_RATES_URL, "application/xml,text/xml").decode("utf-8-sig")
    current_xml = fetch(session, NBP_CURRENT_RATES_URL, "application/xml,text/xml").decode("utf-8-sig")
    archive = parse_nbp_rates(archive_xml)
    current = parse_nbp_rates(current_xml)
    incoming = _validated_nbp_observations(
        archive, current, source["observations"], date.fromisoformat(verified_at[:10])
    )
    merged = _merge_revisions(source["observations"], incoming, "effectiveFrom", "annualRatePercent")
    added = len(merged) - len(source["observations"])
    provenance_changed = False
    for observation in merged:
        if observation.get("source") != NBP_RATES_URL:
            observation["source"] = NBP_RATES_URL
            provenance_changed = True

    expected_source = {
        "publisher": "NBP",
        "url": NBP_RATES_URL,
        "currentUrl": NBP_CURRENT_RATES_URL,
        "verifiedAt": verified_at,
    }
    current_source = source.get("source", {})
    source_urls_changed = (
        current_source.get("url") != NBP_RATES_URL
        or current_source.get("currentUrl") != NBP_CURRENT_RATES_URL
    )
    if added or source_urls_changed or provenance_changed:
        source.update({
            "verifiedAt": verified_at,
            "source": expected_source,
            "observations": merged,
        })
        write_json(path, source)
    return added


def _validate_mf_current_offerings(parsed: list[dict[str, Any]], as_of: date) -> None:
    current_month = as_of.strftime("%Y-%m")
    current_families = {
        item["productType"]
        for item in parsed
        if item["saleFrom"][:7] == current_month
    }
    missing_families = sorted(set(PRODUCT_RULES) - current_families)
    if missing_families:
        raise SourceError(
            f"MF workbook is missing current-month {current_month} offerings for: "
            + ", ".join(missing_families)
        )


def _validated_mf_series(
    parsed: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    as_of: date,
) -> list[dict[str, Any]]:
    parsed_codes = [item["seriesCode"] for item in parsed]
    if len(parsed_codes) != len(set(parsed_codes)):
        raise SourceError("MF workbook contains duplicate supported series codes")

    current_by_code: dict[str, dict[str, Any]] = {}
    for item in existing:
        previous = current_by_code.get(item["seriesCode"])
        if previous is None or item["termsRevision"] > previous["termsRevision"]:
            current_by_code[item["seriesCode"]] = item

    required_codes = {
        series_code
        for series_code, item in current_by_code.items()
        if _can_still_be_outstanding(item, as_of)
    }
    missing_codes = sorted(required_codes - set(parsed_codes))
    if missing_codes:
        raise SourceError(
            "MF workbook lost previously published outstanding series: " + ", ".join(missing_codes)
        )
    return parsed


def _validate_official_mf_workbook_url(workbook_url: str) -> str:
    return validate_official_mf_workbook_url(workbook_url)


def sync_mf(
    session: Any,
    verified_date: str,
    workbook_path: Path | None = None,
    cross_check: bool = True,
    workbook_url: str | None = None,
) -> tuple[int, int]:
    if workbook_path:
        if not workbook_url:
            raise SourceError(
                "--mf-workbook requires --mf-workbook-url with the exact official MF attachment URL"
            )
        workbook_url = _validate_official_mf_workbook_url(workbook_url)
        workbook_content = workbook_path.read_bytes()
        official_content = fetch(
            session, workbook_url, "application/vnd.ms-excel,application/octet-stream"
        )
        if sha256_bytes(workbook_content) != sha256_bytes(official_content):
            raise SourceError(
                "Local MF workbook does not match the bytes fetched from its official provenance URL"
            )
    else:
        if workbook_url:
            raise SourceError("--mf-workbook-url can only be used together with --mf-workbook")
        page = fetch(session, MF_PAGE_URL, "text/html,application/xhtml+xml").decode("utf-8")
        workbook_url = _validate_official_mf_workbook_url(discover_mf_workbook(page))
        workbook_content = fetch(session, workbook_url, "application/vnd.ms-excel,application/octet-stream")
    as_of = date.fromisoformat(verified_date)
    parsed = [
        item for item in parse_mf_workbook(workbook_content, workbook_url, verified_date)
        if _can_still_be_outstanding(item, as_of)
    ]
    parsed = _validated_mf_series(parsed, load_series(), as_of)
    _validate_mf_current_offerings(parsed, as_of)

    if cross_check:
        for series in parsed:
            cross_source = series["provenance"].get("crossCheck")
            if not cross_source:
                continue
            cross_url = validate_official_cross_check_url(cross_source["url"])
            html = fetch(session, cross_url, "text/html,application/xhtml+xml").decode("utf-8")
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
    if date.fromisoformat(series["saleFrom"]) > as_of:
        return False
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
        if item.get("status") != "PARTIAL":
            item["status"] = "FRESH"
        item["lastSuccessAt"] = now
    elif item.get("lastSuccessAt"):
        if item.get("status") != "PARTIAL":
            item["status"] = "STALE"
    else:
        item["status"] = "UNAVAILABLE"


def _managed_tree_changes() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *MANAGED_PATHS],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _require_clean_managed_tree() -> None:
    changes = _managed_tree_changes()
    if changes:
        raise SourceError(
            "Live update requires a clean managed tree so rollback cannot destroy local work: "
            + "; ".join(changes)
        )


def _rollback_managed_tree() -> None:
    subprocess.run(
        ["git", "restore", "--worktree", "--source=HEAD", "--", *MANAGED_PATHS],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clean", "-fd", "--", *MANAGED_PATHS],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def run_live(args: argparse.Namespace) -> None:
    session = default_session()
    verified_date = args.as_of or date.today().isoformat()
    verified_at = f"{verified_date}T00:00:00Z"
    tasks = (
        (
            "mf",
            lambda: sync_mf(
                session,
                verified_date,
                args.mf_workbook,
                not args.skip_cross_check,
                args.mf_workbook_url,
            ),
        ),
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
    parser.add_argument(
        "--mf-workbook-url",
        help="Exact official https://www.gov.pl/attachment/... provenance URL required with --mf-workbook",
    )
    parser.add_argument("--skip-cross-check", action="store_true", help="Skip live HTML cross-check (fixtures/bootstrap only)")
    parser.add_argument("--gus-start-year", type=int, default=int(GUS_HISTORY_START[:4]))
    parser.add_argument("--as-of", help="Deterministic verification date (YYYY-MM-DD)")
    args = parser.parse_args()

    live_transaction = not args.offline
    try:
        if live_transaction:
            _require_clean_managed_tree()
            if args.mf_only:
                verified_date = args.as_of or date.today().isoformat()
                sync_mf(
                    default_session(),
                    verified_date,
                    args.mf_workbook,
                    not args.skip_cross_check,
                    args.mf_workbook_url,
                )
                mark_source_status("mf", True, "MF-only refresh succeeded")
            else:
                run_live(args)
        revision = build_dist()
        print(f"Validated dataset revision: {revision}")
    except Exception as exc:
        if live_transaction:
            try:
                _rollback_managed_tree()
            except Exception as rollback_exc:
                print(
                    f"ERROR: live update failed and managed-tree rollback also failed: {rollback_exc}",
                    file=sys.stderr,
                )
                print(f"ORIGINAL ERROR: {exc}", file=sys.stderr)
                return 1
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return check_generated_tree()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
