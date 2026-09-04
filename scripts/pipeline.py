from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from scripts.sources import PRODUCT_RULES, canonical_decimal, terms_content_hash

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DIST = ROOT / "dist"
PUBLICATION = ROOT / "publication" / "v1"
SCHEMAS = ROOT / "schemas"
GUS_HISTORY_START = "2014-01"
NBP_HISTORY_START = "2022-05-06"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_product_definitions() -> list[dict[str, Any]]:
    definitions = [load_json(path) for path in sorted((DATA / "products").glob("*/rules-v*.json"))]
    return sorted(definitions, key=lambda item: (item["productType"], item["rulesRevision"]))


def load_series() -> list[dict[str, Any]]:
    series = [load_json(path) for path in sorted((DATA / "series").glob("*/*/terms-v*.json"))]
    return sorted(series, key=lambda item: (item["saleFrom"], item["seriesCode"], item["termsRevision"]))


def load_reference_source(name: str) -> dict[str, Any]:
    return load_json(DATA / "reference" / f"{name}.json")


def build_dist() -> str:
    products = load_product_definitions()
    series = load_series()
    gus = load_reference_source("gus-cpi")
    nbp = load_reference_source("nbp-reference-rates")
    status_source = load_json(DATA / "source-status.json")

    _validate_product_definitions(products)
    _validate_series(series, products)
    _validate_gus(gus)
    _validate_nbp(nbp)
    _validate_append_only_history(products, series, gus, nbp)

    generated_at = _generated_at(series, gus, nbp)
    documents = {
        "catalog.json": {"schemaVersion": "2.0", "generatedAt": generated_at, "series": series},
        "product-definitions.json": {
            "schemaVersion": "2.0", "generatedAt": generated_at, "productDefinitions": products
        },
        "gus-cpi.json": {
            "schemaVersion": "2.0", "generatedAt": gus["verifiedAt"], "observations": gus["observations"]
        },
        "nbp-reference-rates.json": {
            "schemaVersion": "2.0", "generatedAt": nbp["verifiedAt"], "observations": nbp["observations"]
        },
    }
    schemas = {
        "catalog.json": "catalog-v2.schema.json",
        "product-definitions.json": "product-definitions-v2.schema.json",
        "gus-cpi.json": "gus-cpi-v2.schema.json",
        "nbp-reference-rates.json": "nbp-reference-rates-v2.schema.json",
    }
    dist_names = {
        "catalog.json": "catalog-v2.json",
        "product-definitions.json": "product-definitions-v2.json",
        "gus-cpi.json": "gus-cpi-v2.json",
        "nbp-reference-rates.json": "nbp-reference-rates-v2.json",
    }
    for filename, document in documents.items():
        _validate_schema(document, load_json(SCHEMAS / schemas[filename]), filename)
        write_json(DIST / dist_names[filename], document)

    document_bytes = {name: canonical_json_bytes(document) for name, document in documents.items()}
    combined = b"".join(name.encode() + content for name, content in sorted(document_bytes.items()))
    dataset_revision = f"{generated_at[:10].replace('-', '')}T000000Z-{sha256(combined)[:12]}"
    snapshot = PUBLICATION / "snapshots" / dataset_revision
    manifest = {
        "schemaVersion": "1.0",
        "datasetRevision": dataset_revision,
        "generatedAt": generated_at,
        "files": {
            name: {
                "path": name,
                "sha256": sha256(content),
                "schemaVersion": documents[name]["schemaVersion"],
                "count": _document_count(documents[name]),
            }
            for name, content in sorted(document_bytes.items())
        },
        "provenance": {
            "mf": _mf_provenance(series),
            "gus": gus["source"],
            "nbp": nbp["source"],
        },
        "coverage": _coverage(series, gus["observations"], nbp["observations"]),
    }
    _validate_schema(manifest, load_json(SCHEMAS / "snapshot-manifest-v1.schema.json"), "manifest.json")
    _write_immutable_snapshot(snapshot, {**document_bytes, "manifest.json": canonical_json_bytes(manifest)})

    latest = {
        "schemaVersion": "1.0",
        "datasetRevision": dataset_revision,
        "manifest": f"snapshots/{dataset_revision}/manifest.json",
    }
    status = _build_status(dataset_revision, status_source)
    _validate_schema(status, load_json(SCHEMAS / "source-status-v1.schema.json"), "status.json")
    write_json(PUBLICATION / "latest.json", latest)
    write_json(PUBLICATION / "status.json", status)
    return dataset_revision


def _write_immutable_snapshot(snapshot: Path, files: dict[str, bytes]) -> None:
    if snapshot.exists():
        for name, expected in files.items():
            path = snapshot / name
            if not path.exists() or path.read_bytes() != expected:
                raise ValueError(f"Immutable snapshot {snapshot.name} would be rewritten: {name}")
        unexpected = {path.name for path in snapshot.iterdir() if path.is_file()} - set(files)
        if unexpected:
            raise ValueError(f"Immutable snapshot {snapshot.name} contains unexpected files: {sorted(unexpected)}")
        return
    snapshot.mkdir(parents=True)
    for name, content in files.items():
        (snapshot / name).write_bytes(content)


def _document_count(document: dict[str, Any]) -> int:
    for key in ("series", "productDefinitions", "observations"):
        if key in document:
            return len(document[key])
    return 0


def _validate_schema(document: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        details = "\n".join(f"- {'/'.join(map(str, error.path))}: {error.message}" for error in errors)
        raise ValueError(f"{label} does not satisfy its schema:\n{details}")


def _validate_product_definitions(products: list[dict[str, Any]]) -> None:
    identities = {item["id"] for item in products}
    if len(identities) != len(products):
        raise ValueError("Duplicate product definition")
    expected = {f"{code}-rules-1" for code in PRODUCT_RULES}
    if identities != expected:
        raise ValueError(f"Expected product definitions {sorted(expected)}, got {sorted(identities)}")

    rule_fields = {
        "maturityMonths": "maturity_months",
        "interestPeriodMonths": "interest_period_months",
        "rateModel": "rate_model",
        "capitalizationRule": "capitalization_rule",
        "interestPaymentRule": "interest_payment_rule",
        "accrualRule": "accrual_rule",
    }
    for product in products:
        product_type = product["productType"]
        rules = PRODUCT_RULES.get(product_type)
        if rules is None:
            raise ValueError(f"{product['id']}: unknown product type {product_type}")
        expected_id = f"{product_type}-rules-{product['rulesRevision']}"
        if product["id"] != expected_id:
            raise ValueError(
                f"{product['id']}: id does not match productType/rulesRevision ({expected_id})"
            )
        for document_field, rules_field in rule_fields.items():
            expected_value = getattr(rules, rules_field)
            if product[document_field] != expected_value:
                raise ValueError(
                    f"{product['id']}: {document_field} disagrees with parser rules: "
                    f"{product[document_field]!r} != {expected_value!r}"
                )
        if product["maturityMonths"] % product["interestPeriodMonths"]:
            raise ValueError(f"{product['id']}: maturity must be divisible by interest period")


def _validate_series(series: list[dict[str, Any]], products: list[dict[str, Any]]) -> None:
    if not series:
        raise ValueError("Series catalog is empty")

    products_by_id = {item["id"]: item for item in products}
    identities: set[tuple[str, int]] = set()
    revisions_by_code: dict[str, list[int]] = defaultdict(list)
    for item in series:
        identity = (item["seriesCode"], item["termsRevision"])
        if identity in identities:
            raise ValueError(f"Duplicate terms revision: {identity}")
        identities.add(identity)
        revisions_by_code[item["seriesCode"]].append(item["termsRevision"])

        product = products_by_id.get(item["productDefinition"])
        if product is None:
            raise ValueError(f"{item['seriesCode']}: unknown product definition")
        if item["productType"] != product["productType"]:
            raise ValueError(
                f"{item['seriesCode']}: productType {item['productType']} does not match "
                f"{item['productDefinition']} ({product['productType']})"
            )
        if item["seriesCode"][:3] != item["productType"]:
            raise ValueError(
                f"{item['seriesCode']}: series code prefix does not match productType {item['productType']}"
            )
        if item["saleFrom"] > item["saleTo"]:
            raise ValueError(f"{item['seriesCode']}: invalid sale window")
        if item["contentHash"] != terms_content_hash(item):
            raise ValueError(f"{item['seriesCode']}: invalid contentHash")

    for series_code, revisions in revisions_by_code.items():
        ordered = sorted(revisions)
        expected_revisions = list(range(1, ordered[-1] + 1))
        if ordered != expected_revisions:
            raise ValueError(
                f"{series_code}: terms revisions must be contiguous from 1; got {ordered}"
            )


def _validate_revisioned_observations(
    observations: list[dict[str, Any]], identity: str, label: str
) -> None:
    identities = [(item[identity], item["revision"]) for item in observations]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise ValueError(
            f"{label} observations must be unique by ({identity}, revision) and canonically ordered"
        )

    revisions_by_identity: dict[str, list[int]] = defaultdict(list)
    for item in observations:
        revisions_by_identity[item[identity]].append(item["revision"])
    for observation_identity, revisions in revisions_by_identity.items():
        ordered = sorted(revisions)
        expected_revisions = list(range(1, ordered[-1] + 1))
        if ordered != expected_revisions:
            raise ValueError(
                f"{label} {observation_identity}: revisions must be contiguous from 1; got {ordered}"
            )


def _current_reference_observations(
    observations: list[dict[str, Any]], identity: str
) -> list[dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for item in observations:
        key = item[identity]
        previous = current.get(key)
        if previous is None or item["revision"] > previous["revision"]:
            current[key] = item
    return [current[key] for key in sorted(current)]


def _validate_gus(gus: dict[str, Any]) -> None:
    observations = gus.get("observations", [])
    _validate_revisioned_observations(observations, "period", "GUS")
    if not observations:
        raise ValueError("GUS CPI observations are empty")

    for item in observations:
        expected_yoy = canonical_decimal(
            Decimal(item["indexPreviousYear100"]) - Decimal("100")
        )
        if item["yearOverYearPercent"] != expected_yoy:
            raise ValueError(
                f"GUS {item['period']} revision {item['revision']}: yearOverYearPercent "
                f"must equal indexPreviousYear100 - 100 ({expected_yoy})"
            )
        year, month = (int(part) for part in item["period"].split("-"))
        source = item.get("source", {})
        expected_period_id = 246 + month
        if source.get("year") != year or source.get("periodId") != expected_period_id:
            raise ValueError(
                f"GUS {item['period']} revision {item['revision']}: source metadata does not match period"
            )

    current = _current_reference_observations(observations, "period")
    periods = [item["period"] for item in current]
    if periods[0] != GUS_HISTORY_START:
        raise ValueError(f"GUS CPI history must start at {GUS_HISTORY_START}, got {periods[0]}")

    by_year: dict[str, list[str]] = defaultdict(list)
    for period in periods:
        by_year[period[:4]].append(period)
    years = sorted(by_year)
    start_year = int(GUS_HISTORY_START[:4])
    expected_years = [str(year) for year in range(start_year, int(years[-1]) + 1)]
    if years != expected_years:
        missing_years = sorted(set(expected_years) - set(years))
        raise ValueError(f"GUS CPI coverage is missing years: {missing_years}")

    verified_year = int(gus["verifiedAt"][:4])
    latest_year = int(years[-1])
    if latest_year > verified_year:
        raise ValueError(
            f"GUS CPI contains future coverage beyond verification year {verified_year}: {years[-1]}"
        )
    for year in years:
        periods_for_year = by_year[year]
        required_count = 12 if int(year) < latest_year else len(periods_for_year)
        expected_periods = [f"{year}-{month:02d}" for month in range(1, required_count + 1)]
        if periods_for_year != expected_periods:
            raise ValueError(f"GUS CPI coverage is incomplete/non-contiguous for {year}")


def _validate_nbp(nbp: dict[str, Any]) -> None:
    observations = nbp.get("observations", [])
    _validate_revisioned_observations(observations, "effectiveFrom", "NBP")
    current = _current_reference_observations(observations, "effectiveFrom")
    if not current:
        raise ValueError("NBP reference-rate observations are empty")
    first_date = current[0]["effectiveFrom"]
    if first_date != NBP_HISTORY_START:
        raise ValueError(
            f"NBP history must start at {NBP_HISTORY_START}, got {first_date}"
        )


def _validate_append_only_history(
    products: list[dict[str, Any]],
    series: list[dict[str, Any]],
    gus: dict[str, Any],
    nbp: dict[str, Any],
) -> None:
    snapshots_root = PUBLICATION / "snapshots"
    if not snapshots_root.exists():
        return

    current_products = {item["id"]: item for item in products}
    current_series = {(item["seriesCode"], item["termsRevision"]): item for item in series}
    current_gus = {(item["period"], item["revision"]): item for item in gus.get("observations", [])}
    current_nbp = {
        (item["effectiveFrom"], item["revision"]): item
        for item in nbp.get("observations", [])
    }

    for snapshot in sorted(path for path in snapshots_root.iterdir() if path.is_dir()):
        files = {
            "products": snapshot / "product-definitions.json",
            "series": snapshot / "catalog.json",
            "gus": snapshot / "gus-cpi.json",
            "nbp": snapshot / "nbp-reference-rates.json",
        }
        missing_files = [path.name for path in files.values() if not path.is_file()]
        if missing_files:
            raise ValueError(
                f"Historical snapshot {snapshot.name} is incomplete: missing {sorted(missing_files)}"
            )

        previous_products = load_json(files["products"])["productDefinitions"]
        for previous in previous_products:
            current = current_products.get(previous["id"])
            if current is None:
                raise ValueError(
                    f"Product definition {previous['id']} from historical snapshot {snapshot.name} was deleted"
                )
            if current != previous:
                raise ValueError(
                    f"Product definition {previous['id']} from historical snapshot {snapshot.name} was mutated"
                )

        previous_series = load_json(files["series"])["series"]
        for previous in previous_series:
            identity = (previous["seriesCode"], previous["termsRevision"])
            current = current_series.get(identity)
            if current is None:
                raise ValueError(
                    f"Series revision {identity} from historical snapshot {snapshot.name} was deleted"
                )
            if current["contentHash"] != previous["contentHash"]:
                raise ValueError(
                    f"Series revision {identity} from historical snapshot {snapshot.name} was mutated in place"
                )

        previous_gus = load_json(files["gus"])["observations"]
        for previous in previous_gus:
            identity = (previous["period"], previous["revision"])
            current = current_gus.get(identity)
            if current is None:
                raise ValueError(
                    f"GUS observation {identity} from historical snapshot {snapshot.name} was deleted"
                )
            if current["indexPreviousYear100"] != previous["indexPreviousYear100"]:
                raise ValueError(
                    f"GUS observation {identity} from historical snapshot {snapshot.name} was mutated in place"
                )

        previous_nbp = load_json(files["nbp"])["observations"]
        for previous in previous_nbp:
            identity = (previous["effectiveFrom"], previous["revision"])
            current = current_nbp.get(identity)
            if current is None:
                raise ValueError(
                    f"NBP observation {identity} from historical snapshot {snapshot.name} was deleted"
                )
            if current["annualRatePercent"] != previous["annualRatePercent"]:
                raise ValueError(
                    f"NBP observation {identity} from historical snapshot {snapshot.name} was mutated in place"
                )


def _generated_at(series: list[dict[str, Any]], gus: dict[str, Any], nbp: dict[str, Any]) -> str:
    candidates = [item["provenance"]["verifiedAt"] + "T00:00:00Z" for item in series]
    candidates.extend([gus["verifiedAt"], nbp["verifiedAt"]])
    return max(candidates)


def _mf_provenance(series: list[dict[str, Any]]) -> dict[str, Any]:
    primary = series[-1]["provenance"]["primary"]
    return {
        "publisher": primary["publisher"],
        "url": primary["url"],
        "sha256": primary["sha256"],
        "verifiedAt": max(item["provenance"]["verifiedAt"] for item in series),
    }


def _coverage(series: list[dict[str, Any]], gus: list[dict[str, Any]], nbp: list[dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in series:
        by_family[item["productType"]].append(item)
    catalog = {
        family: {
            "firstSaleFrom": min(item["saleFrom"] for item in items),
            "lastSaleTo": max(item["saleTo"] for item in items),
            "seriesCount": len({item["seriesCode"] for item in items}),
            "termsRevisionCount": len(items),
            "gaps": _monthly_gaps(items),
        }
        for family, items in sorted(by_family.items())
    }
    current_gus = _current_reference_observations(gus, "period")
    current_nbp = _current_reference_observations(nbp, "effectiveFrom")
    return {
        "catalog": catalog,
        "gusCpi": {
            "fromPeriod": current_gus[0]["period"] if current_gus else None,
            "throughPeriod": current_gus[-1]["period"] if current_gus else None,
            "observationCount": len(current_gus),
        },
        "nbpReferenceRates": {
            "fromEffectiveDate": current_nbp[0]["effectiveFrom"] if current_nbp else None,
            "throughEffectiveDate": current_nbp[-1]["effectiveFrom"] if current_nbp else None,
            "observationCount": len(current_nbp),
        },
    }


def _monthly_gaps(items: list[dict[str, Any]]) -> list[str]:
    months = sorted({item["saleFrom"][:7] for item in items})
    if not months:
        return []
    start, end = (datetime.strptime(value, "%Y-%m") for value in (months[0], months[-1]))
    expected: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        expected.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return sorted(set(expected) - set(months))


def _build_status(dataset_revision: str, source: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"schemaVersion": "1.0", "datasetRevision": dataset_revision, "sources": {}}
    for name in ("mf", "gus", "nbp"):
        item = source["sources"][name]
        result["sources"][name] = {
            "status": item["status"],
            "lastAttemptAt": item["lastAttemptAt"],
            "lastAttemptStatus": item["lastAttemptStatus"],
            "lastSuccessAt": item["lastSuccessAt"],
            "staleAfterHours": item["staleAfterHours"],
            "message": item.get("message"),
        }
    return result


def migration_records_from_catalog_v1() -> list[dict[str, Any]]:
    """Read-only migration helper retained for auditing the original v1 seed."""
    path = DIST / "catalog-v1.json"
    return load_json(path)["series"] if path.exists() else []
