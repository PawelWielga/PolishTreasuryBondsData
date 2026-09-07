from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from scripts.sources import (
    SUPPORTED_PRODUCT_TYPES,
    canonical_decimal,
    terms_content_hash,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DIST = ROOT / "dist"  # frozen legacy v1 only
PUBLICATION = ROOT / "publication" / "v1"
SCHEMAS = ROOT / "schemas"
GUS_HISTORY_START = "2014-01"
NBP_HISTORY_START = "2022-05-06"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_product_definitions() -> list[dict[str, Any]]:
    definitions = [
        load_json(path)
        for path in sorted((DATA / "products").glob("*/rules-v*.json"))
    ]
    return sorted(
        definitions,
        key=lambda item: (item["productType"], item["rulesRevision"]),
    )


def load_series() -> list[dict[str, Any]]:
    series = [
        load_json(path)
        for path in sorted((DATA / "series").glob("*/*/terms-v*.json"))
    ]
    return sorted(
        series,
        key=lambda item: (item["saleFrom"], item["seriesCode"], item["termsRevision"]),
    )


def load_reference_source(name: str) -> dict[str, Any]:
    return load_json(DATA / "reference" / f"{name}.json")


def build_publication() -> str:
    """Validate canonical facts and build the single supported public snapshot tree."""
    products = load_product_definitions()
    series = load_series()
    gus = load_reference_source("gus-cpi")
    nbp = load_reference_source("nbp-reference-rates")
    status_source = load_json(DATA / "source-status.json")

    _validate_product_definitions(products)
    _validate_series(series, products)
    _validate_gus(gus)
    _validate_nbp(nbp)
    _validate_evidence(series, gus, nbp)
    _validate_append_only_history(products, series, gus, nbp)

    generated_at = _generated_at(series, gus, nbp)
    documents = {
        "catalog.json": {
            "schemaVersion": "2.0",
            "generatedAt": generated_at,
            "series": series,
        },
        "product-definitions.json": {
            "schemaVersion": "2.0",
            "generatedAt": generated_at,
            "productDefinitions": products,
        },
        "gus-cpi.json": {
            "schemaVersion": "2.0",
            "generatedAt": gus["verifiedAt"],
            "observations": gus["observations"],
        },
        "nbp-reference-rates.json": {
            "schemaVersion": "2.0",
            "generatedAt": nbp["verifiedAt"],
            "observations": nbp["observations"],
        },
    }
    schemas = {
        "catalog.json": "catalog-v2.schema.json",
        "product-definitions.json": "product-definitions-v2.schema.json",
        "gus-cpi.json": "gus-cpi-v2.schema.json",
        "nbp-reference-rates.json": "nbp-reference-rates-v2.schema.json",
    }
    for filename, document in documents.items():
        _validate_schema(document, load_json(SCHEMAS / schemas[filename]), filename)

    document_bytes = {
        name: canonical_json_bytes(document) for name, document in documents.items()
    }
    combined = b"".join(
        name.encode("utf-8") + content
        for name, content in sorted(document_bytes.items())
    )
    dataset_revision = (
        f"{generated_at[:10].replace('-', '')}T000000Z-{sha256(combined)[:12]}"
    )
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
        "coverage": _coverage(
            series,
            gus["observations"],
            nbp["observations"],
        ),
    }
    _validate_schema(
        manifest,
        load_json(SCHEMAS / "snapshot-manifest-v1.schema.json"),
        "manifest.json",
    )
    _write_immutable_snapshot(
        snapshot,
        {**document_bytes, "manifest.json": canonical_json_bytes(manifest)},
    )

    latest = {
        "schemaVersion": "1.0",
        "datasetRevision": dataset_revision,
        "manifest": f"snapshots/{dataset_revision}/manifest.json",
    }
    status = _build_status(dataset_revision, status_source)
    _validate_schema(
        status,
        load_json(SCHEMAS / "source-status-v1.schema.json"),
        "status.json",
    )
    write_json(PUBLICATION / "latest.json", latest)
    write_json(PUBLICATION / "status.json", status)
    return dataset_revision


def build_dist() -> str:
    """Internal compatibility name for the v2 publication builder.

    The function no longer writes v2 files to dist/. Frozen v1 artifacts are the
    only remaining contents of that directory.
    """
    return build_publication()


def _write_immutable_snapshot(snapshot: Path, files: dict[str, bytes]) -> None:
    if snapshot.exists():
        if snapshot.is_symlink() or not snapshot.is_dir():
            raise ValueError(f"Immutable snapshot {snapshot.name} is not a real directory")
        for name, expected in files.items():
            path = snapshot / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
                raise ValueError(f"Immutable snapshot {snapshot.name} would be rewritten: {name}")
        unexpected = {path.name for path in snapshot.iterdir()} - set(files)
        if unexpected:
            raise ValueError(
                f"Immutable snapshot {snapshot.name} contains unexpected entries: {sorted(unexpected)}"
            )
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
        details = "\n".join(
            f"- {'/'.join(map(str, error.path))}: {error.message}" for error in errors
        )
        raise ValueError(f"{label} does not satisfy its schema:\n{details}")


def _validate_product_definitions(products: list[dict[str, Any]]) -> None:
    if not products:
        raise ValueError("Product definitions are empty")

    identities = [item["id"] for item in products]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate product definition")

    by_type: dict[str, list[int]] = defaultdict(list)
    for product in products:
        product_type = product["productType"]
        if product_type not in SUPPORTED_PRODUCT_TYPES:
            raise ValueError(f"Unsupported product type: {product_type}")
        revision = int(product["rulesRevision"])
        expected_id = f"{product_type}-rules-{revision}"
        if product["id"] != expected_id:
            raise ValueError(
                f"{product['id']}: id does not match productType/rulesRevision ({expected_id})"
            )
        by_type[product_type].append(revision)
        if product["maturityMonths"] % product["interestPeriodMonths"]:
            raise ValueError(f"{product['id']}: maturity must be divisible by interest period")

    if set(by_type) != set(SUPPORTED_PRODUCT_TYPES):
        missing = sorted(set(SUPPORTED_PRODUCT_TYPES) - set(by_type))
        extra = sorted(set(by_type) - set(SUPPORTED_PRODUCT_TYPES))
        raise ValueError(
            f"Product definitions must cover exactly supported families; missing={missing}, extra={extra}"
        )

    for product_type, revisions in by_type.items():
        ordered = sorted(revisions)
        expected = list(range(1, ordered[-1] + 1))
        if ordered != expected:
            raise ValueError(
                f"{product_type}: rule revisions must be contiguous from 1; got {ordered}"
            )


def _validate_https_uri(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an HTTPS URI")
    try:
        parsed = urlparse(value)
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError(f"{label} is malformed: {value!r}") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port <= 0
    ):
        raise ValueError(f"{label} must be a credential-free HTTPS URI")


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
                f"{item['seriesCode']}: series code prefix does not match "
                f"productType {item['productType']}"
            )
        if item["saleFrom"] > item["saleTo"]:
            raise ValueError(f"{item['seriesCode']}: invalid sale window")
        sale_year, sale_month, _ = (int(part) for part in item["saleFrom"].split("-"))
        maturity_index = sale_year * 12 + (sale_month - 1) + product["maturityMonths"]
        maturity_year, maturity_month_zero_based = divmod(maturity_index, 12)
        expected_suffix = f"{maturity_month_zero_based + 1:02d}{maturity_year % 100:02d}"
        if item["seriesCode"][3:] != expected_suffix:
            raise ValueError(
                f"{item['seriesCode']}: maturity suffix disagrees with saleFrom/product maturity; "
                f"expected {expected_suffix}"
            )
        primary = item["provenance"]["primary"]
        _validate_https_uri(primary["url"], f"{item['seriesCode']} primary provenance")
        cross_check = item["provenance"].get("crossCheck")
        if cross_check is not None:
            _validate_https_uri(
                cross_check["url"], f"{item['seriesCode']} cross-check provenance"
            )
        if primary["sheet"] != item["productType"]:
            raise ValueError(
                f"{item['seriesCode']}: MF provenance sheet {primary['sheet']} "
                "does not match productType"
            )
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
                f"GUS {item['period']} revision {item['revision']}: "
                "source metadata does not match period"
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

    verified_period = gus["verifiedAt"][:7]
    verified_year = int(verified_period[:4])
    latest_year = int(years[-1])
    if latest_year > verified_year:
        raise ValueError(
            f"GUS CPI contains future coverage beyond verification year {verified_year}: {years[-1]}"
        )
    if periods[-1] > verified_period:
        raise ValueError(
            f"GUS CPI contains future period {periods[-1]} beyond verification month {verified_period}"
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
        raise ValueError(f"NBP history must start at {NBP_HISTORY_START}, got {first_date}")
    verified_date = nbp["verifiedAt"][:10]
    if current[-1]["effectiveFrom"] > verified_date:
        raise ValueError(
            f"NBP contains future effective date {current[-1]['effectiveFrom']} "
            f"beyond verification date {verified_date}"
        )


def _verify_content_addressed_file(path: Path, digest: str, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label}: referenced evidence is missing: {path}")
    actual = sha256(path.read_bytes())
    if actual != digest:
        raise ValueError(f"{label}: evidence SHA-256 mismatch; expected {digest}, got {actual}")


def _validate_evidence(
    series: list[dict[str, Any]],
    gus: dict[str, Any],
    nbp: dict[str, Any],
) -> None:
    mf_digests = {item["provenance"]["primary"]["sha256"] for item in series}
    for digest in sorted(mf_digests):
        _verify_content_addressed_file(
            DATA / "sources" / "mf" / f"{digest}.xls",
            digest,
            "MF",
        )

    gus_source = gus.get("source", {})
    bundle_digest = gus_source.get("evidenceBundleSha256")
    if bundle_digest is not None:
        bundle_path = DATA / "sources" / "gus" / f"{bundle_digest}.manifest.json"
        _verify_content_addressed_file(bundle_path, bundle_digest, "GUS evidence bundle")
        bundle = load_json(bundle_path)
        if bundle.get("publisher") != "GUS" or bundle.get("schemaVersion") != "1.0":
            raise ValueError("GUS evidence bundle has invalid metadata")
        responses = bundle.get("responses")
        if not isinstance(responses, list) or not responses:
            raise ValueError("GUS evidence bundle has no responses")
        for entry in responses:
            url = entry.get("url")
            digest = entry.get("sha256")
            _validate_https_uri(url, "GUS evidence URL")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"GUS evidence bundle contains invalid SHA-256: {digest!r}")
            _verify_content_addressed_file(
                DATA / "sources" / "gus" / f"{digest}.json",
                digest,
                "GUS raw response",
            )

    nbp_source = nbp.get("source", {})
    archive_digest = nbp_source.get("archiveSha256")
    current_digest = nbp_source.get("currentSha256")
    if (archive_digest is None) != (current_digest is None):
        raise ValueError("NBP evidence must provide archiveSha256 and currentSha256 together")
    if archive_digest is not None and current_digest is not None:
        _verify_content_addressed_file(
            DATA / "sources" / "nbp" / f"{archive_digest}.xml",
            archive_digest,
            "NBP archive",
        )
        _verify_content_addressed_file(
            DATA / "sources" / "nbp" / f"{current_digest}.xml",
            current_digest,
            "NBP current rates",
        )


def _selected_snapshot() -> Path | None:
    latest_path = PUBLICATION / "latest.json"
    if not latest_path.is_file():
        return None
    latest = load_json(latest_path)
    revision = latest.get("datasetRevision")
    manifest = latest.get("manifest")
    if not isinstance(revision, str) or not revision:
        raise ValueError("latest.json has no valid datasetRevision")
    expected_manifest = f"snapshots/{revision}/manifest.json"
    if manifest != expected_manifest:
        raise ValueError(
            f"latest.json manifest must be {expected_manifest!r}, got {manifest!r}"
        )
    snapshot = PUBLICATION / "snapshots" / revision
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError(f"Selected historical snapshot is missing: {revision}")
    return snapshot


def _validate_append_only_history(
    products: list[dict[str, Any]],
    series: list[dict[str, Any]],
    gus: dict[str, Any],
    nbp: dict[str, Any],
) -> None:
    """Require current canonical facts to extend the last reviewed public dataset.

    Because every accepted dataset is cumulative and corrections append revisions,
    comparing with the currently selected snapshot is sufficient. Older immutable
    snapshots are retained for audit/reproduction and are protected separately by
    the publication namespace guard.
    """
    snapshot = _selected_snapshot()
    if snapshot is None:
        return

    files = {
        "products": snapshot / "product-definitions.json",
        "series": snapshot / "catalog.json",
        "gus": snapshot / "gus-cpi.json",
        "nbp": snapshot / "nbp-reference-rates.json",
    }
    missing_files = [path.name for path in files.values() if not path.is_file()]
    if missing_files:
        raise ValueError(
            f"Selected historical snapshot {snapshot.name} is incomplete: "
            f"missing {sorted(missing_files)}"
        )

    current_products = {item["id"]: item for item in products}
    for previous in load_json(files["products"])["productDefinitions"]:
        current = current_products.get(previous["id"])
        if current is None:
            raise ValueError(f"Product definition {previous['id']} was deleted")
        if current != previous:
            raise ValueError(f"Product definition {previous['id']} was mutated")

    current_series = {(item["seriesCode"], item["termsRevision"]): item for item in series}
    for previous in load_json(files["series"])["series"]:
        identity = (previous["seriesCode"], previous["termsRevision"])
        current = current_series.get(identity)
        if current is None:
            raise ValueError(f"Series revision {identity} was deleted")
        if current["contentHash"] != previous["contentHash"]:
            raise ValueError(f"Series revision {identity} was mutated in place")

    current_gus = {
        (item["period"], item["revision"]): item for item in gus.get("observations", [])
    }
    for previous in load_json(files["gus"])["observations"]:
        identity = (previous["period"], previous["revision"])
        current = current_gus.get(identity)
        if current is None:
            raise ValueError(f"GUS observation {identity} was deleted")
        if current["indexPreviousYear100"] != previous["indexPreviousYear100"]:
            raise ValueError(f"GUS observation {identity} was mutated in place")

    current_nbp = {
        (item["effectiveFrom"], item["revision"]): item
        for item in nbp.get("observations", [])
    }
    for previous in load_json(files["nbp"])["observations"]:
        identity = (previous["effectiveFrom"], previous["revision"])
        current = current_nbp.get(identity)
        if current is None:
            raise ValueError(f"NBP observation {identity} was deleted")
        if current["annualRatePercent"] != previous["annualRatePercent"]:
            raise ValueError(f"NBP observation {identity} was mutated in place")


def _generated_at(series: list[dict[str, Any]], gus: dict[str, Any], nbp: dict[str, Any]) -> str:
    candidates = [
        item["provenance"]["verifiedAt"] + "T00:00:00Z" for item in series
    ]
    candidates.extend([gus["verifiedAt"], nbp["verifiedAt"]])
    return max(candidates)


def _mf_provenance(series: list[dict[str, Any]]) -> dict[str, Any]:
    source_series = max(
        series,
        key=lambda item: (
            item["provenance"]["verifiedAt"],
            item["saleFrom"],
            item["seriesCode"],
            item["termsRevision"],
        ),
    )
    primary = source_series["provenance"]["primary"]
    return {
        "publisher": primary["publisher"],
        "url": primary["url"],
        "sha256": primary["sha256"],
        "verifiedAt": source_series["provenance"]["verifiedAt"],
    }


def _coverage(
    series: list[dict[str, Any]],
    gus: list[dict[str, Any]],
    nbp: list[dict[str, Any]],
) -> dict[str, Any]:
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
    result: dict[str, Any] = {
        "schemaVersion": "1.0",
        "datasetRevision": dataset_revision,
        "sources": {},
    }
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
    """Read-only helper retained solely to audit the frozen legacy v1 seed."""
    path = DIST / "catalog-v1.json"
    return load_json(path)["series"] if path.exists() else []
