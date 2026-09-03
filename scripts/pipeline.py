from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from scripts.sources import PRODUCT_RULES, terms_content_hash

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DIST = ROOT / "dist"
PUBLICATION = ROOT / "publication" / "v1"
SCHEMAS = ROOT / "schemas"


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
    write_json(DIST / "metadata.json", manifest)
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
    for product in products:
        if product["maturityMonths"] % product["interestPeriodMonths"]:
            raise ValueError(f"{product['id']}: maturity must be divisible by interest period")


def _validate_series(series: list[dict[str, Any]], products: list[dict[str, Any]]) -> None:
    product_ids = {item["id"] for item in products}
    identities: set[tuple[str, int]] = set()
    for item in series:
        identity = (item["seriesCode"], item["termsRevision"])
        if identity in identities:
            raise ValueError(f"Duplicate terms revision: {identity}")
        identities.add(identity)
        if item["productDefinition"] not in product_ids:
            raise ValueError(f"{item['seriesCode']}: unknown product definition")
        if item["saleFrom"] > item["saleTo"]:
            raise ValueError(f"{item['seriesCode']}: invalid sale window")
        if item["contentHash"] != terms_content_hash(item):
            raise ValueError(f"{item['seriesCode']}: invalid contentHash")


def _validate_gus(gus: dict[str, Any]) -> None:
    periods = [item["period"] for item in gus.get("observations", [])]
    if periods != sorted(periods) or len(periods) != len(set(periods)):
        raise ValueError("GUS observations must be unique and canonically ordered")
    by_year: dict[str, list[str]] = defaultdict(list)
    for period in periods:
        by_year[period[:4]].append(period)
    years = sorted(by_year)
    for year in years[:-1]:
        if by_year[year] != [f"{year}-{month:02d}" for month in range(1, 13)]:
            raise ValueError(f"GUS CPI coverage is incomplete for {year}")


def _validate_nbp(nbp: dict[str, Any]) -> None:
    observations = nbp.get("observations", [])
    dates = [item["effectiveFrom"] for item in observations]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("NBP observations must be unique and canonically ordered")
    if observations and dates[0] > "2022-06-01":
        raise ValueError("NBP history does not cover the first ROR/DOR offering")


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
    return {
        "catalog": catalog,
        "gusCpi": {
            "fromPeriod": gus[0]["period"] if gus else None,
            "throughPeriod": gus[-1]["period"] if gus else None,
            "observationCount": len(gus),
        },
        "nbpReferenceRates": {
            "fromEffectiveDate": nbp[0]["effectiveFrom"] if nbp else None,
            "throughEffectiveDate": nbp[-1]["effectiveFrom"] if nbp else None,
            "observationCount": len(nbp),
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
