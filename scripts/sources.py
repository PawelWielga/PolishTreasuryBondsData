from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import requests
import xlrd
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MF_PAGE_URL = "https://www.gov.pl/web/finanse/obligacje-detaliczne1"
NBP_RATES_URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml"
GUS_BASE_URL = "https://api-sdp.stat.gov.pl/api/1.1.0"
USER_AGENT = "PolishTreasuryBondsData/2.0 (+https://github.com/PawelWielga/PolishTreasuryBondsData)"

GUS_FIRST_VARIABLE_YEAR = 2026
GUS_ANNUAL_INDICATOR_ID = 1832
GUS_CPI_VARIABLE_ID = 305
GUS_COICOP_2018_SECTION_ID = 1698
GUS_COICOP_2018_TOTAL_POSITION = 14916914
GUS_HOUSEHOLD_TOTAL_POSITION = 6902025
GUS_ANNUAL_MEASURE_ID = 5
GUS_JANUARY_PERIOD_ID = 247
GUS_DECEMBER_PERIOD_ID = 258

RETAIL_BOND_FACE_VALUE_MINOR_UNITS = 10_000
MF_REQUIRED_HEADERS = {
    0: "Seria",
    1: "Kod ISIN",
    3: "Początek sprzedaży",
    4: "Koniec sprzedaży",
    5: "Cena emisyjna",
    6: "Cena zamiany",
    9: "Oprocentowanie",
}


class SourceError(RuntimeError):
    """An official source could not be fetched or validated safely."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_decimal(value: Decimal, minimum_places: int = 2) -> str:
    text = format(value, "f")
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    fraction = fraction.ljust(minimum_places, "0")
    return f"{whole}.{fraction}" if fraction else whole


def percent_from_fraction(value: Any) -> str:
    decimal = Decimal(str(value)) * Decimal("100")
    return canonical_decimal(decimal.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def money_minor_units(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def default_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _https_origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlparse(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise SourceError(f"Official source URL is malformed: {url}") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SourceError(f"Official source URL must be credential-free HTTPS: {url}")
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def validate_official_mf_workbook_url(workbook_url: str) -> str:
    parsed = urlparse(workbook_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.gov.pl"
        or not parsed.path.startswith("/attachment/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SourceError(
            "MF workbook provenance must be the exact official "
            "https://www.gov.pl/attachment/... URL"
        )
    return workbook_url


def validate_official_cross_check_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.obligacjeskarbowe.pl"
        or not parsed.path.startswith("/oferta-obligacji/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SourceError(
            "Treasury Bond cross-check provenance must stay on the exact official "
            "https://www.obligacjeskarbowe.pl/oferta-obligacji/... URL"
        )
    return url


def fetch(
    session: requests.Session,
    url: str,
    accept: str,
    timeout: tuple[int, int] = (10, 45),
    allow_not_found: bool = False,
) -> bytes:
    requested_origin = _https_origin(url)
    try:
        response = session.get(url, headers={"Accept": accept}, timeout=timeout)
        response_url = getattr(response, "url", url)
        if isinstance(response_url, str) and _https_origin(response_url) != requested_origin:
            raise SourceError(
                f"Official source redirect left trusted origin: {url} -> {response_url}"
            )
        if allow_not_found and response.status_code == 404:
            return b""
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        raise SourceError(f"Official source request failed for {url}: {exc}") from exc


def discover_mf_workbook(page_html: str, page_url: str = MF_PAGE_URL) -> str:
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[str] = []
    for link in soup.select("a.file-download[href]"):
        label = " ".join(link.stripped_strings).lower()
        aria = (link.get("aria-label") or "").lower()
        if "obligacji detalicznych" in f"{label} {aria}" and "xls" in f"{label} {aria}":
            candidates.append(urljoin(page_url, str(link["href"])))
    if len(candidates) != 1:
        raise SourceError(f"Expected one official MF retail-bond workbook, found {len(candidates)}")
    return candidates[0]


@dataclass(frozen=True)
class ProductRules:
    product_type: str
    maturity_months: int
    interest_period_months: int
    rate_model: str
    capitalization_rule: str
    interest_payment_rule: str
    accrual_rule: str


PRODUCT_RULES: dict[str, ProductRules] = {
    "OTS": ProductRules("OTS", 3, 3, "Fixed", "None", "AtMaturity", "FixedMaturityOnly"),
    "ROR": ProductRules("ROR", 12, 1, "NbpReferencePlusMargin", "None", "AtPeriodEnd", "ActualPeriodProRata"),
    "DOR": ProductRules("DOR", 24, 1, "NbpReferencePlusMargin", "None", "AtPeriodEnd", "ActualPeriodProRata"),
    "TOS": ProductRules("TOS", 36, 12, "Fixed", "EndOfPeriod", "AtMaturity", "ActualPeriodProRata"),
    "COI": ProductRules("COI", 48, 12, "InflationPlusMargin", "None", "AtPeriodEnd", "ActualPeriodProRata"),
    "EDO": ProductRules("EDO", 120, 12, "InflationPlusMargin", "EndOfPeriod", "AtMaturity", "ActualPeriodProRata"),
    "ROS": ProductRules("ROS", 72, 12, "InflationPlusMargin", "EndOfPeriod", "AtMaturity", "ActualPeriodProRata"),
    "ROD": ProductRules("ROD", 144, 12, "InflationPlusMargin", "EndOfPeriod", "AtMaturity", "ActualPeriodProRata"),
}


def _excel_date(book: xlrd.book.Book, raw: Any) -> str:
    year, month, day, *_ = xlrd.xldate_as_tuple(float(raw), book.datemode)
    return date(year, month, day).isoformat()


def _mf_header(sheet: Any, column: int) -> str:
    return " ".join(str(sheet.cell_value(0, column)).split())


def _validate_mf_sheet_layout(sheet: Any, family_code: str, rules: ProductRules) -> int | None:
    required_last_column = max(MF_REQUIRED_HEADERS)
    if sheet.ncols <= required_last_column:
        raise SourceError(
            f"MF workbook sheet {family_code} has only {sheet.ncols} columns; "
            f"at least {required_last_column + 1} are required"
        )
    for column, expected in MF_REQUIRED_HEADERS.items():
        actual = _mf_header(sheet, column)
        if actual != expected:
            raise SourceError(
                f"MF workbook sheet {family_code} column {column + 1} changed: "
                f"expected {expected!r}, got {actual!r}"
            )

    if family_code == "OTS":
        if sheet.ncols <= 10 or _mf_header(sheet, 10) != "Odsetki (zł)":
            raise SourceError("MF workbook sheet OTS is missing the expected Odsetki (zł) column")

    margin_column = sheet.ncols - 1 if _mf_header(sheet, sheet.ncols - 1) == "Marża" else None
    if rules.rate_model != "Fixed" and margin_column is None:
        raise SourceError(f"MF workbook sheet {family_code} is missing required Marża column")
    return margin_column


def parse_mf_workbook(content: bytes, workbook_url: str, verified_at: str) -> list[dict[str, Any]]:
    try:
        book = xlrd.open_workbook(file_contents=content)
    except (xlrd.XLRDError, ValueError) as exc:
        raise SourceError(f"MF attachment is not a readable XLS workbook: {exc}") from exc

    workbook_hash = sha256_bytes(content)
    result: list[dict[str, Any]] = []
    for family_code, rules in PRODUCT_RULES.items():
        if family_code not in book.sheet_names():
            raise SourceError(f"MF workbook is missing required sheet {family_code}")
        sheet = book.sheet_by_name(family_code)
        header_rows = 2 if sheet.cell_value(1, 0) == "" else 1
        margin_column = _validate_mf_sheet_layout(sheet, family_code, rules)
        for row_index in range(header_rows, sheet.nrows):
            series_code = str(sheet.cell_value(row_index, 0)).strip().upper()
            if not re.fullmatch(rf"{family_code}\d{{4}}", series_code):
                continue
            first_rate = sheet.cell_value(row_index, 9)
            if first_rate in (None, ""):
                raise SourceError(f"{family_code} row {row_index + 1}: first-period rate is missing")
            margin = sheet.cell_value(row_index, margin_column) if margin_column is not None else 0
            if rules.rate_model != "Fixed" and margin in (None, ""):
                raise SourceError(f"{series_code}: margin is missing")

            sale_from = _excel_date(book, sheet.cell_value(row_index, 3))
            source_url = (
                "https://www.obligacjeskarbowe.pl/oferta-obligacji/"
                f"{_family_slug(family_code)}/{series_code.lower()}/"
            )
            series: dict[str, Any] = {
                "seriesCode": series_code,
                "productType": family_code,
                "productDefinition": f"{family_code}-rules-1",
                "isin": str(sheet.cell_value(row_index, 1)).strip() or None,
                "saleFrom": sale_from,
                "saleTo": _excel_date(book, sheet.cell_value(row_index, 4)),
                "currency": "PLN",
                "faceValueMinorUnits": RETAIL_BOND_FACE_VALUE_MINOR_UNITS,
                "issuePriceMinorUnits": money_minor_units(sheet.cell_value(row_index, 5)),
                "exchangePriceMinorUnits": money_minor_units(sheet.cell_value(row_index, 6)),
                "firstPeriodAnnualRatePercent": percent_from_fraction(first_rate),
                "marginPercent": percent_from_fraction(margin or 0),
                "fixedMaturityInterestMinorUnits": (
                    money_minor_units(sheet.cell_value(row_index, 10)) if family_code == "OTS" else None
                ),
                "termsRevision": 1,
                "provenance": {
                    "primary": {
                        "publisher": "Ministry of Finance",
                        "url": workbook_url,
                        "sha256": workbook_hash,
                        "sheet": family_code,
                        "row": row_index + 1,
                    },
                    "crossCheck": {"url": source_url} if _is_current_offering(sale_from, verified_at) else None,
                    "verifiedAt": verified_at,
                },
            }
            series["contentHash"] = terms_content_hash(series)
            result.append(series)

    result.sort(key=lambda item: (item["saleFrom"], item["seriesCode"], item["termsRevision"]))
    return result


def _family_slug(code: str) -> str:
    return {
        "OTS": "obligacje-3-miesieczne-ots",
        "ROR": "obligacje-roczne-ror",
        "DOR": "obligacje-2-letnie-dor",
        "TOS": "obligacje-3-letnie-tos",
        "COI": "obligacje-4-letnie-coi",
        "EDO": "obligacje-10-letnie-edo",
        "ROS": "obligacje-6-letnie-ros",
        "ROD": "obligacje-12-letnie-rod",
    }[code]


def _is_current_offering(sale_from: str, verified_at: str) -> bool:
    return sale_from[:7] == verified_at[:7]


def terms_financial_view(series: dict[str, Any]) -> dict[str, Any]:
    excluded = {"termsRevision", "contentHash", "provenance"}
    return {key: value for key, value in series.items() if key not in excluded}


def terms_content_hash(series: dict[str, Any]) -> str:
    payload = json.dumps(terms_financial_view(series), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_series_html(html: str) -> dict[str, str | int | None]:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("main") or soup
    text = " ".join(content.stripped_strings)
    series_code = _required(r"\bSeria:\s*([A-Z]{3}\d{4})\b", text, "series code").upper()
    sale = re.search(r"Sprzedaż:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", text, re.I)
    if not sale:
        raise SourceError(f"{series_code}: sale window not found on cross-check page")
    issue_price = Decimal(
        _required(
            r"Cena sprzedaży jednej obligacji:\s*([0-9]+(?:,[0-9]+)?)\s*zł",
            text,
            "sale price",
        ).replace(",", ".")
    )
    first_rate = Decimal(
        _required(r"Oprocentowanie:\s*([0-9]+(?:,[0-9]+)?)%", text, "first rate").replace(",", ".")
    )
    family = series_code[:3]
    if family not in PRODUCT_RULES:
        raise SourceError(f"Unsupported Treasury Bond family on cross-check page: {family}")
    exchange_price = None
    if family not in {"ROS", "ROD"}:
        exchange_price = Decimal(
            _required(
                r"Cena zamiany jednej obligacji:\s*([0-9]+(?:,[0-9]+)?)\s*zł",
                text,
                "exchange price",
            ).replace(",", ".")
        )
    rules = PRODUCT_RULES[family]
    margin_match = None
    if rules.rate_model == "NbpReferencePlusMargin":
        margin_match = re.search(r"stopa referencyjna NBP\s*\+\s*([0-9]+(?:,[0-9]+)?)%", text, re.I)
    elif rules.rate_model == "InflationPlusMargin":
        margin_match = re.search(r"marża\s*([0-9]+(?:,[0-9]+)?)%\s*\+\s*inflacja", text, re.I)
    margin = canonical_decimal(Decimal(margin_match.group(1).replace(",", "."))) if margin_match else None
    fixed_maturity_interest = None
    if family == "OTS":
        fixed_maturity_interest = money_minor_units(
            Decimal(
                _required(
                    r"\bOdsetki:\s*([0-9]+(?:[,.][0-9]+)?)\s*zł\b",
                    text,
                    "fixed maturity interest",
                ).replace(",", ".")
            )
        )
    return {
        "seriesCode": series_code,
        "saleFrom": datetime.strptime(sale.group(1), "%d.%m.%Y").date().isoformat(),
        "saleTo": datetime.strptime(sale.group(2), "%d.%m.%Y").date().isoformat(),
        "issuePriceMinorUnits": money_minor_units(issue_price),
        "exchangePriceMinorUnits": money_minor_units(exchange_price),
        "firstPeriodAnnualRatePercent": canonical_decimal(first_rate),
        "marginPercent": margin,
        "fixedMaturityInterestMinorUnits": fixed_maturity_interest,
        "maturityMonths": _parse_maturity_months(text[: max(text.find("Seria:"), 0)]),
    }


def _parse_maturity_months(text: str) -> int | None:
    patterns = (
        (r"\b3[- ]miesięcz", 3), (r"\broczn", 12), (r"\b2[- ]letni", 24),
        (r"\b3[- ]letni", 36), (r"\b4[- ]letni", 48), (r"\b6[- ]letni", 72),
        (r"\b10[- ]letni", 120), (r"\b12[- ]letni", 144),
    )
    for pattern, months in patterns:
        if re.search(pattern, text, re.I):
            return months
    return None


def cross_check_series(workbook_series: dict[str, Any], html_facts: dict[str, Any]) -> None:
    comparable = dict(workbook_series)
    rules = PRODUCT_RULES[workbook_series["productType"]]
    comparable["maturityMonths"] = rules.maturity_months
    fields = (
        "seriesCode", "saleFrom", "saleTo", "issuePriceMinorUnits", "exchangePriceMinorUnits",
        "firstPeriodAnnualRatePercent", "marginPercent", "fixedMaturityInterestMinorUnits", "maturityMonths",
    )
    for field in fields:
        if html_facts.get(field) is None:
            continue
        if comparable.get(field) != html_facts.get(field):
            raise SourceError(
                f"{workbook_series['seriesCode']}: official sources disagree on {field}: "
                f"MF XLS={comparable.get(field)!r}, HTML={html_facts.get(field)!r}"
            )


def _required(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.I)
    if not match:
        raise SourceError(f"Could not parse {label} from official page")
    return match.group(1)


def parse_gus_indicator_response(payload: Any, year: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise SourceError(f"GUS {year}: expected an array")
    points = [_gus_observation(item, year) for item in payload]
    return _validate_gus_year(points, year, require_complete=True)


def parse_gus_variable_responses(payloads: Iterable[Any], year: int, require_complete: bool = False) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise SourceError(f"GUS {year}: invalid variable response")
        matches = [
            row for row in payload["data"]
            if row.get("id-pozycja-2") == GUS_COICOP_2018_TOTAL_POSITION
            and row.get("id-pozycja-3") == GUS_HOUSEHOLD_TOTAL_POSITION
            and row.get("id-sposob-prezentacji-miara") == GUS_ANNUAL_MEASURE_ID
        ]
        if len(matches) > 1:
            raise SourceError(f"GUS {year}: multiple total-CPI matches for one period")
        if matches:
            points.append(_gus_observation(matches[0], year))
    return _validate_gus_year(points, year, require_complete=require_complete)


def _gus_observation(row: dict[str, Any], year: int) -> dict[str, Any]:
    if row.get("id-daty") != year:
        raise SourceError(f"GUS {year}: response contains a different year")
    period_id = int(row.get("id-okres", -1))
    if period_id not in range(GUS_JANUARY_PERIOD_ID, GUS_DECEMBER_PERIOD_ID + 1):
        raise SourceError(f"GUS {year}: invalid monthly period {period_id}")
    index = Decimal(str(row["wartosc"]))
    month = period_id - GUS_JANUARY_PERIOD_ID + 1
    return {
        "period": f"{year:04d}-{month:02d}",
        "indexPreviousYear100": canonical_decimal(index),
        "yearOverYearPercent": canonical_decimal(index - Decimal("100")),
        "revision": 1,
        "source": {
            "publisher": "GUS",
            "api": "SDP",
            "year": year,
            "periodId": period_id,
        },
    }


def _validate_gus_year(points: list[dict[str, Any]], year: int, require_complete: bool) -> list[dict[str, Any]]:
    result = sorted({point["period"]: point for point in points}.values(), key=lambda point: point["period"])
    if len(result) != len(points):
        raise SourceError(f"GUS {year}: duplicate monthly observations")
    if require_complete and len(result) != 12:
        raise SourceError(f"GUS {year}: expected 12 observations, got {len(result)}")
    if result:
        expected = [f"{year:04d}-{month:02d}" for month in range(1, len(result) + 1)]
        actual = [item["period"] for item in result]
        if actual != expected:
            raise SourceError(f"GUS {year}: incomplete/non-contiguous periods: {actual}")
    return result


def fetch_gus_history(
    session: requests.Session,
    start_year: int,
    end_year: int,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        if year < GUS_FIRST_VARIABLE_YEAR:
            url = (
                f"{GUS_BASE_URL}/indicators/indicator-data-indicator"
                f"?id-wskaznik={GUS_ANNUAL_INDICATOR_ID}&id-rok={year}&lang=pl"
            )
            payload = json.loads(fetch(session, url, "application/json"))
            observations.extend(parse_gus_indicator_response(payload, year))
            sleep(0.2)
            continue

        payloads = []
        for period_id in range(GUS_JANUARY_PERIOD_ID, GUS_DECEMBER_PERIOD_ID + 1):
            page = 1
            while True:
                url = (
                    f"{GUS_BASE_URL}/variable/variable-data-section?id-zmienna={GUS_CPI_VARIABLE_ID}"
                    f"&id-przekroj={GUS_COICOP_2018_SECTION_ID}&id-rok={year}&id-okres={period_id}"
                    f"&page-size=5000&page={page}&lang=pl"
                )
                content = fetch(session, url, "application/json", allow_not_found=True)
                if not content:
                    break
                payload = json.loads(content)
                payloads.append(payload)
                if page >= int(payload.get("page-count", page)):
                    break
                page += 1
                sleep(0.2)
            sleep(0.2)
        observations.extend(parse_gus_variable_responses(payloads, year, require_complete=False))
    return observations


def _decode_embedded_json_string(html: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', html, re.S)
    if not match:
        raise SourceError(f"NBP page does not contain {key}")
    return json.loads('"' + match.group(1) + '"')


def parse_nbp_rates(html_or_xml: str) -> list[dict[str, Any]]:
    documents: list[str]
    if re.search(r'"stopy_procentowe_archiwum"\s*:', html_or_xml):
        documents = [
            _decode_embedded_json_string(html_or_xml, "stopy_procentowe_archiwum"),
            _decode_embedded_json_string(html_or_xml, "interest_rates"),
        ]
    else:
        documents = [html_or_xml]

    observations: dict[str, dict[str, Any]] = {}
    for document in documents:
        try:
            root = ET.fromstring(document)
        except ET.ParseError as exc:
            raise SourceError(f"NBP rate XML is malformed: {exc}") from exc
        parents = {child: parent for parent in root.iter() for child in parent}
        for element in root.iter():
            if element.attrib.get("id") != "ref":
                continue
            parent = parents.get(element)
            effective = (
                element.attrib.get("obowiazuje_od")
                or element.attrib.get("obowiązuje_od")
                or (parent.attrib.get("obowiazuje_od") if parent is not None else None)
                or (parent.attrib.get("obowiązuje_od") if parent is not None else None)
            )
            raw_rate = element.attrib.get("oprocentowanie")
            if not effective or not raw_rate:
                raise SourceError("NBP reference-rate row lacks effective date or value")
            datetime.strptime(effective, "%Y-%m-%d")
            observation = {
                "effectiveFrom": effective,
                "annualRatePercent": canonical_decimal(Decimal(raw_rate.replace(",", "."))),
                "revision": 1,
                "publishedAt": None,
                "source": NBP_RATES_URL,
            }
            previous = observations.get(effective)
            if previous and previous["annualRatePercent"] != observation["annualRatePercent"]:
                raise SourceError(f"NBP source contains conflicting reference rates for {effective}")
            observations[effective] = observation
    if not observations:
        raise SourceError("NBP source contains no reference-rate observations")
    return [observations[key] for key in sorted(observations)]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
