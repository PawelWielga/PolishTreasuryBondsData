from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CATALOG_SOURCE = ROOT / "data" / "series" / "catalog-source.json"
REFERENCE_SOURCE = ROOT / "data" / "reference" / "reference-source.json"
CATALOG_SCHEMA = ROOT / "schemas" / "catalog-v1.schema.json"
REFERENCE_SCHEMA = ROOT / "schemas" / "reference-data-v1.schema.json"
DIST = ROOT / "dist"
OFFER_URL = "https://www.obligacjeskarbowe.pl/oferta/"
USER_AGENT = "PolishTreasuryBondsData/1.0 (+https://github.com/PawelWielga/PolishTreasuryBondsData)"


@dataclass(frozen=True)
class Family:
    code: str
    type_name: str
    slug: str
    maturity_months: int
    interest_period_months: int
    interest_payments_per_year: int
    rate_kind: str
    capitalization_rule: str
    interest_payment_rule: str
    accrual_rule: str


FAMILIES: dict[str, Family] = {
    "OTS": Family("OTS", "Ots", "obligacje-3-miesieczne-ots", 3, 3, 1, "Fixed", "None", "AtMaturity", "FixedMaturityOnly"),
    "ROR": Family("ROR", "Ror", "obligacje-roczne-ror", 12, 1, 12, "NbpReferencePlusMargin", "None", "AtPeriodEnd", "ActualPeriodProRata"),
    "DOR": Family("DOR", "Dor", "obligacje-2-letnie-dor", 24, 1, 12, "NbpReferencePlusMargin", "None", "AtPeriodEnd", "ActualPeriodProRata"),
    "TOS": Family("TOS", "Tos", "obligacje-3-letnie-tos", 36, 12, 1, "Fixed", "EndOfPeriod", "AtMaturity", "ActualPeriodProRata"),
    "COI": Family("COI", "Coi", "obligacje-4-letnie-coi", 48, 12, 1, "InflationPlusMargin", "None", "AtPeriodEnd", "ActualPeriodProRata"),
    "EDO": Family("EDO", "Edo", "obligacje-10-letnie-edo", 120, 12, 1, "InflationPlusMargin", "EndOfPeriod", "AtMaturity", "ActualPeriodProRata"),
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_text(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def visible_text(html: str) -> str:
    return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)


def decimal_pl(raw: str) -> float:
    return float(raw.replace("\xa0", "").replace(" ", "").replace(",", "."))


def iso_date_pl(raw: str) -> str:
    return datetime.strptime(raw, "%d.%m.%Y").date().isoformat()


def discover_series_codes(offer_html: str) -> dict[str, str]:
    text = visible_text(offer_html)
    result: dict[str, str] = {}
    for prefix in FAMILIES:
        match = re.search(rf"\bSeria:\s*({prefix}\d{{4}})\b", text, re.IGNORECASE)
        if match:
            result[prefix] = match.group(1).upper()
    missing = sorted(set(FAMILIES) - set(result))
    if missing:
        raise ValueError(f"Official offer page did not expose current series for: {', '.join(missing)}")
    return result


def parse_series_detail(family: Family, series_code: str, html: str, source_url: str, verified_at: str) -> dict[str, Any]:
    text = visible_text(html)
    page_series = _required(r"\bSeria:\s*([A-Z]{3}\d{4})\b", text, "series code").upper()
    if page_series != series_code:
        raise ValueError(f"Expected {series_code}, official detail page contains {page_series}")

    sale = re.search(r"Sprzedaż:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    if not sale:
        raise ValueError(f"{series_code}: sale window not found")

    nominal = decimal_pl(_required(r"Cena sprzedaży jednej obligacji:\s*([0-9]+(?:,[0-9]+)?)\s*zł", text, "sale price"))
    first_rate = decimal_pl(_required(r"Oprocentowanie:\s*([0-9]+(?:,[0-9]+)?)%", text, "first annual rate")) / 100.0
    margin = 0.0
    if family.rate_kind == "NbpReferencePlusMargin":
        margin = decimal_pl(_required(r"stopa referencyjna NBP\s*\+\s*([0-9]+(?:,[0-9]+)?)%", text, "NBP margin")) / 100.0
    elif family.rate_kind == "InflationPlusMargin":
        margin = decimal_pl(_required(r"marża\s*([0-9]+(?:,[0-9]+)?)%\s*\+\s*inflacja", text, "inflation margin")) / 100.0

    fixed_interest = None
    if family.code == "OTS":
        fixed_interest = decimal_pl(_required(r"Odsetki:\s*([0-9]+(?:,[0-9]+)?)\s*zł", text, "fixed maturity interest"))

    item: dict[str, Any] = {
        "seriesCode": series_code,
        "type": family.type_name,
        "saleFrom": iso_date_pl(sale.group(1)),
        "saleTo": iso_date_pl(sale.group(2)),
        "nominalValue": nominal,
        "currency": "PLN",
        "maturityMonths": family.maturity_months,
        "interestPeriodMonths": family.interest_period_months,
        "interestPaymentsPerYear": family.interest_payments_per_year,
        "firstPeriodAnnualRate": first_rate,
        "rateRule": {"kind": family.rate_kind, "margin": margin},
        "capitalizationRule": family.capitalization_rule,
        "interestPaymentRule": family.interest_payment_rule,
        "accrualRule": family.accrual_rule,
        "fixedMaturityInterestPerBond": fixed_interest,
        "termsVersion": "",
        "source": source_url,
        "verifiedAt": verified_at,
    }
    item["termsVersion"] = terms_fingerprint(item)
    return item


def _required(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not parse {label} from official page")
    return match.group(1)


def financial_view(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"termsVersion", "source", "verifiedAt"}}


def terms_fingerprint(item: dict[str, Any]) -> str:
    payload = json.dumps(financial_view(item), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "source-sha256-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def semantically_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return financial_view(left) == financial_view(right)


def sync_current_catalog() -> bool:
    source = load_json(CATALOG_SOURCE)
    existing: list[dict[str, Any]] = source["series"]
    current = discover_series_codes(fetch_text(OFFER_URL))
    verified_at = date.today().isoformat()
    changed = False

    for prefix, series_code in current.items():
        family = FAMILIES[prefix]
        source_url = f"https://www.obligacjeskarbowe.pl/oferta-obligacji/{family.slug}/{series_code.lower()}/"
        parsed = parse_series_detail(family, series_code, fetch_text(source_url), source_url, verified_at)
        same_series = [item for item in existing if item["seriesCode"] == series_code]
        if any(semantically_equal(item, parsed) for item in same_series):
            continue
        existing.append(parsed)
        changed = True

    if changed:
        existing.sort(key=lambda item: (item["saleFrom"], item["seriesCode"], item["termsVersion"]))
        write_json(CATALOG_SOURCE, source)
    return changed


def build_dist() -> None:
    catalog_source = load_json(CATALOG_SOURCE)
    reference_source = load_json(REFERENCE_SOURCE)
    _validate_source_semantics(catalog_source, reference_source)

    catalog = {
        "schemaVersion": "1.0",
        "generatedAt": _catalog_generated_at(catalog_source["series"]),
        "series": catalog_source["series"],
    }
    reference = {
        "schemaVersion": "1.0",
        "generatedAt": _reference_generated_at(reference_source),
        "referenceRates": reference_source["referenceRates"],
        "inflation": reference_source["inflation"],
    }

    _validate_schema(catalog, load_json(CATALOG_SCHEMA), "catalog-v1.json")
    _validate_schema(reference, load_json(REFERENCE_SCHEMA), "reference-data-v1.json")
    write_json(DIST / "catalog-v1.json", catalog)
    write_json(DIST / "reference-data-v1.json", reference)

    catalog_bytes = (DIST / "catalog-v1.json").read_bytes()
    reference_bytes = (DIST / "reference-data-v1.json").read_bytes()
    write_json(DIST / "metadata.json", {
        "schemaVersion": "1.0",
        "generatedAt": max(catalog["generatedAt"], reference["generatedAt"]),
        "catalog": {"path": "catalog-v1.json", "sha256": hashlib.sha256(catalog_bytes).hexdigest(), "seriesCount": len(catalog["series"])},
        "referenceData": {"path": "reference-data-v1.json", "sha256": hashlib.sha256(reference_bytes).hexdigest(), "referenceRateCount": len(reference["referenceRates"]), "inflationCount": len(reference["inflation"])},
    })


def _validate_schema(document: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        details = "\n".join(f"- {'/'.join(map(str, error.path))}: {error.message}" for error in errors)
        raise ValueError(f"{label} does not satisfy its schema:\n{details}")


def _validate_source_semantics(catalog: dict[str, Any], reference: dict[str, Any]) -> None:
    identities: set[tuple[str, str]] = set()
    for item in catalog.get("series", []):
        identity = (item["seriesCode"], item["termsVersion"])
        if identity in identities:
            raise ValueError(f"Duplicate immutable terms identity: {identity[0]} / {identity[1]}")
        identities.add(identity)
        if item["saleFrom"] > item["saleTo"]:
            raise ValueError(f"{item['seriesCode']}: saleFrom must not be after saleTo")
        if item["maturityMonths"] % item["interestPeriodMonths"] != 0:
            raise ValueError(f"{item['seriesCode']}: maturity must be divisible by interest period")
        if item["type"] == "Ots" and item["fixedMaturityInterestPerBond"] is None:
            raise ValueError(f"{item['seriesCode']}: OTS requires fixedMaturityInterestPerBond")

    for collection_name in ("referenceRates", "inflation"):
        for observation in reference.get(collection_name, []):
            if len(observation["appliesToInterestPeriodStartMonth"]) != 10:
                raise ValueError(f"{collection_name}: invalid appliesToInterestPeriodStartMonth")


def _catalog_generated_at(series: list[dict[str, Any]]) -> str:
    latest = max(item["verifiedAt"] for item in series)
    return f"{latest}T00:00:00Z"


def _reference_generated_at(reference: dict[str, Any]) -> str:
    candidates: list[str] = []
    for item in reference.get("referenceRates", []) + reference.get("inflation", []):
        candidates.append(item.get("publishedAt") or f"{item['referenceDate']}T00:00:00Z")
    return max(candidates) if candidates else "1970-01-01T00:00:00Z"
