from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected exactly one match, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_pipeline() -> None:
    path = "scripts/pipeline.py"
    replace_once(path, "from datetime import datetime\n", "from datetime import datetime, timedelta\n")

    replace_once(
        path,
        '''def load_reference_source(name: str) -> dict[str, Any]:\n    return load_json(DATA / "reference" / f"{name}.json")\n\n\ndef build_publication() -> str:\n''',
        '''def load_reference_source(name: str) -> dict[str, Any]:\n    return load_json(DATA / "reference" / f"{name}.json")\n\n\ndef load_early_redemption_rules() -> dict[str, Any]:\n    return load_json(DATA / "early-redemption-rules.json")\n\n\ndef build_publication() -> str:\n''',
    )

    replace_once(
        path,
        '''    gus = load_reference_source("gus-cpi")\n    nbp = load_reference_source("nbp-reference-rates")\n    status_source = load_json(DATA / "source-status.json")\n\n    _validate_product_definitions(products)\n    _validate_series(series, products)\n    _validate_gus(gus)\n    _validate_nbp(nbp)\n    _validate_evidence(series, gus, nbp)\n    _validate_append_only_history(products, series, gus, nbp)\n\n    generated_at = _generated_at(series, gus, nbp)\n''',
        '''    gus = load_reference_source("gus-cpi")\n    nbp = load_reference_source("nbp-reference-rates")\n    early_redemption = load_early_redemption_rules()\n    status_source = load_json(DATA / "source-status.json")\n\n    _validate_product_definitions(products)\n    _validate_series(series, products)\n    _validate_early_redemption_rules(early_redemption, series)\n    _validate_gus(gus)\n    _validate_nbp(nbp)\n    _validate_evidence(series, gus, nbp)\n    _validate_append_only_history(products, series, gus, nbp, early_redemption)\n\n    generated_at = _generated_at(series, gus, nbp, early_redemption)\n''',
    )

    replace_once(
        path,
        '''        "product-definitions.json": {\n            "schemaVersion": "2.0",\n            "generatedAt": generated_at,\n            "productDefinitions": products,\n        },\n        "gus-cpi.json": {\n''',
        '''        "product-definitions.json": {\n            "schemaVersion": "2.0",\n            "generatedAt": generated_at,\n            "productDefinitions": products,\n        },\n        "early-redemption-rules.json": {\n            "schemaVersion": "2.0",\n            "generatedAt": early_redemption["verifiedAt"] + "T00:00:00Z",\n            "rules": early_redemption["rules"],\n        },\n        "gus-cpi.json": {\n''',
    )

    replace_once(
        path,
        '''        "catalog.json": "catalog-v2.schema.json",\n        "product-definitions.json": "product-definitions-v2.schema.json",\n        "gus-cpi.json": "gus-cpi-v2.schema.json",\n''',
        '''        "catalog.json": "catalog-v2.schema.json",\n        "product-definitions.json": "product-definitions-v2.schema.json",\n        "early-redemption-rules.json": "early-redemption-rules-v2.schema.json",\n        "gus-cpi.json": "gus-cpi-v2.schema.json",\n''',
    )

    replace_once(
        path,
        '''def _document_count(document: dict[str, Any]) -> int:\n    for key in ("series", "productDefinitions", "observations"):\n''',
        '''def _document_count(document: dict[str, Any]) -> int:\n    for key in ("series", "productDefinitions", "rules", "observations"):\n''',
    )

    validator = '''\n\ndef _parse_contract_date(value: Any, label: str):\n    if not isinstance(value, str):\n        raise ValueError(f"{label} must be an ISO date")\n    try:\n        return datetime.strptime(value, "%Y-%m-%d").date()\n    except ValueError as exc:\n        raise ValueError(f"{label} must be an ISO date, got {value!r}") from exc\n\n\ndef _validate_early_redemption_rules(\n    document: dict[str, Any], series: list[dict[str, Any]]\n) -> None:\n    rules = document.get("rules")\n    if not isinstance(rules, list) or not rules:\n        raise ValueError("Early-redemption rules are empty")\n\n    verified_at = _parse_contract_date(\n        document.get("verifiedAt"), "Early-redemption verifiedAt"\n    )\n    identities = [(item.get("productType"), item.get("rulesRevision")) for item in rules]\n    if identities != sorted(identities) or len(identities) != len(set(identities)):\n        raise ValueError(\n            "Early-redemption rules must be unique by (productType, rulesRevision) "\n            "and canonically ordered"\n        )\n\n    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)\n    for rule in rules:\n        family = rule.get("productType")\n        if family not in SUPPORTED_PRODUCT_TYPES:\n            raise ValueError(f"Unsupported early-redemption product type: {family}")\n        by_family[family].append(rule)\n\n    if set(by_family) != set(SUPPORTED_PRODUCT_TYPES):\n        missing = sorted(set(SUPPORTED_PRODUCT_TYPES) - set(by_family))\n        extra = sorted(set(by_family) - set(SUPPORTED_PRODUCT_TYPES))\n        raise ValueError(\n            "Early-redemption rules must cover exactly supported families; "\n            f"missing={missing}, extra={extra}"\n        )\n\n    current_series_by_code: dict[str, dict[str, Any]] = {}\n    for item in series:\n        code = item["seriesCode"]\n        previous = current_series_by_code.get(code)\n        if previous is None or item["termsRevision"] > previous["termsRevision"]:\n            current_series_by_code[code] = item\n\n    provenance_dates = []\n    for family in sorted(by_family):\n        family_rules = by_family[family]\n        revisions = [int(rule["rulesRevision"]) for rule in family_rules]\n        expected_revisions = list(range(1, revisions[-1] + 1))\n        if revisions != expected_revisions:\n            raise ValueError(\n                f"{family}: early-redemption revisions must be contiguous from 1; got {revisions}"\n            )\n\n        family_series = [item for item in series if item["productType"] == family]\n        first_sale = min(item["saleFrom"] for item in family_series)\n        last_sale = max(item["saleTo"] for item in family_series)\n        if family_rules[0]["purchaseFrom"] != first_sale:\n            raise ValueError(\n                f"{family}: first early-redemption rule must start at catalog coverage "\n                f"{first_sale}, got {family_rules[0]['purchaseFrom']}"\n            )\n\n        previous_end = None\n        for index, rule in enumerate(family_rules):\n            label = f"{family} early-redemption revision {rule['rulesRevision']}"\n            start = _parse_contract_date(rule.get("purchaseFrom"), f"{label} purchaseFrom")\n            through_raw = rule.get("purchaseThrough")\n            end = (\n                None\n                if through_raw is None\n                else _parse_contract_date(through_raw, f"{label} purchaseThrough")\n            )\n            if end is not None and end < start:\n                raise ValueError(f"{label}: purchaseThrough precedes purchaseFrom")\n            if previous_end is not None and start != previous_end + timedelta(days=1):\n                raise ValueError(f"{family}: early-redemption applicability windows must be contiguous")\n            if index > 0 and previous_end is None:\n                raise ValueError(f"{family}: open-ended rule cannot precede another revision")\n            if index < len(family_rules) - 1 and end is None:\n                raise ValueError(f"{family}: only the latest early-redemption rule may be open-ended")\n            previous_end = end\n\n            provenance = rule.get("provenance")\n            if not isinstance(provenance, dict):\n                raise ValueError(f"{label}: provenance is required")\n            _validate_https_uri(provenance.get("url"), f"{label} provenance")\n            source_code = provenance.get("seriesCode")\n            source_series = current_series_by_code.get(source_code)\n            if source_series is None:\n                raise ValueError(f"{label}: provenance series {source_code!r} is not in the catalog")\n            if source_series["productType"] != family:\n                raise ValueError(f"{label}: provenance series belongs to another product family")\n            source_from = _parse_contract_date(source_series["saleFrom"], f"{source_code} saleFrom")\n            source_to = _parse_contract_date(source_series["saleTo"], f"{source_code} saleTo")\n            if source_from < start or (end is not None and source_to > end):\n                raise ValueError(f"{label}: provenance series is outside the rule applicability window")\n            provenance_date = _parse_contract_date(\n                provenance.get("verifiedAt"), f"{label} provenance verifiedAt"\n            )\n            if provenance_date > verified_at:\n                raise ValueError(f"{label}: provenance verification is newer than document verifiedAt")\n            provenance_dates.append(provenance_date)\n\n            if rule.get("minimumHoldingCalendarDays", -1) < 0:\n                raise ValueError(f"{label}: minimum holding days cannot be negative")\n            latest = rule.get("latestInstruction", {})\n            if latest.get("value", 0) <= 0:\n                raise ValueError(f"{label}: latest-instruction offset must be positive")\n            payment = rule.get("paymentTiming", {})\n            if payment.get("value", 0) <= 0:\n                raise ValueError(f"{label}: payment timing must be positive")\n            accrual = rule.get("interestAccrualTiming", {})\n            if accrual.get("kind") == "ThroughBusinessDayAfterInstruction" and accrual.get("value", 0) <= 0:\n                raise ValueError(f"{label}: interest-accrual timing must be positive")\n            if rule.get("chargeApplication") == "NoCharge" and rule.get("chargeMinorUnits") != 0:\n                raise ValueError(f"{label}: NoCharge requires a zero charge")\n\n        if previous_end is not None and previous_end < _parse_contract_date(last_sale, f"{family} lastSaleTo"):\n            raise ValueError(f"{family}: early-redemption rules do not reach current catalog coverage")\n\n    if max(provenance_dates) != verified_at:\n        raise ValueError(\n            "Early-redemption verifiedAt must equal the newest rule provenance verification date"\n        )\n\n    for item in series:\n        sale_from = _parse_contract_date(item["saleFrom"], f"{item['seriesCode']} saleFrom")\n        sale_to = _parse_contract_date(item["saleTo"], f"{item['seriesCode']} saleTo")\n        matches = []\n        for rule in by_family[item["productType"]]:\n            start = _parse_contract_date(rule["purchaseFrom"], "purchaseFrom")\n            end = (\n                None\n                if rule["purchaseThrough"] is None\n                else _parse_contract_date(rule["purchaseThrough"], "purchaseThrough")\n            )\n            if start <= sale_from and (end is None or sale_to <= end):\n                matches.append(rule)\n        if len(matches) != 1:\n            raise ValueError(\n                f"{item['seriesCode']}: expected exactly one early-redemption rule for the "\n                f"full sale window, got {len(matches)}"\n            )\n'''
    replace_once(path, "\n\ndef _validate_revisioned_observations(\n", validator + "\n\ndef _validate_revisioned_observations(\n")

    replace_once(
        path,
        '''def _validate_append_only_history(\n    products: list[dict[str, Any]],\n    series: list[dict[str, Any]],\n    gus: dict[str, Any],\n    nbp: dict[str, Any],\n) -> None:\n''',
        '''def _validate_append_only_history(\n    products: list[dict[str, Any]],\n    series: list[dict[str, Any]],\n    gus: dict[str, Any],\n    nbp: dict[str, Any],\n    early_redemption: dict[str, Any],\n) -> None:\n''',
    )

    append_only = '''\n    previous_early_redemption = snapshot / "early-redemption-rules.json"\n    if previous_early_redemption.is_file():\n        current_rules = {\n            (item["productType"], item["rulesRevision"]): item\n            for item in early_redemption.get("rules", [])\n        }\n        for previous in load_json(previous_early_redemption)["rules"]:\n            identity = (previous["productType"], previous["rulesRevision"])\n            current = current_rules.get(identity)\n            if current is None:\n                raise ValueError(f"Early-redemption rule {identity} was deleted")\n            if current != previous:\n                raise ValueError(f"Early-redemption rule {identity} was mutated in place")\n'''
    replace_once(
        path,
        '''    for previous in load_json(files["nbp"])["observations"]:\n        identity = (previous["effectiveFrom"], previous["revision"])\n        current = current_nbp.get(identity)\n        if current is None:\n            raise ValueError(f"NBP observation {identity} was deleted")\n        if current != previous:\n            raise ValueError(f"NBP observation {identity} was mutated in place")\n\n\ndef _generated_at(series: list[dict[str, Any]], gus: dict[str, Any], nbp: dict[str, Any]) -> str:\n''',
        '''    for previous in load_json(files["nbp"])["observations"]:\n        identity = (previous["effectiveFrom"], previous["revision"])\n        current = current_nbp.get(identity)\n        if current is None:\n            raise ValueError(f"NBP observation {identity} was deleted")\n        if current != previous:\n            raise ValueError(f"NBP observation {identity} was mutated in place")\n''' + append_only + '''\n\ndef _generated_at(\n    series: list[dict[str, Any]],\n    gus: dict[str, Any],\n    nbp: dict[str, Any],\n    early_redemption: dict[str, Any],\n) -> str:\n''',
    )

    replace_once(
        path,
        '''    candidates.extend([gus["verifiedAt"], nbp["verifiedAt"]])\n    return max(candidates)\n''',
        '''    candidates.extend(\n        [\n            gus["verifiedAt"],\n            nbp["verifiedAt"],\n            early_redemption["verifiedAt"] + "T00:00:00Z",\n        ]\n    )\n    return max(candidates)\n''',
    )


def patch_immutable_checker() -> None:
    path = "scripts/check_immutable_snapshots.py"
    replace_once(
        path,
        '''EXPECTED_DATA_FILES = frozenset(\n    {\n        "catalog.json",\n        "product-definitions.json",\n        "gus-cpi.json",\n        "nbp-reference-rates.json",\n    }\n)\nEXPECTED_SNAPSHOT_FILES = EXPECTED_DATA_FILES | {"manifest.json"}\n''',
        '''LEGACY_DATA_FILES = frozenset(\n    {\n        "catalog.json",\n        "product-definitions.json",\n        "gus-cpi.json",\n        "nbp-reference-rates.json",\n    }\n)\nCURRENT_DATA_FILES = LEGACY_DATA_FILES | {"early-redemption-rules.json"}\nLEGACY_SNAPSHOT_FILES = LEGACY_DATA_FILES | {"manifest.json"}\nCURRENT_SNAPSHOT_FILES = CURRENT_DATA_FILES | {"manifest.json"}\n''',
    )
    replace_once(
        path,
        '''def _document_count(document: dict) -> int:\n    for key in ("series", "productDefinitions", "observations"):\n''',
        '''def _document_count(document: dict) -> int:\n    for key in ("series", "productDefinitions", "rules", "observations"):\n''',
    )
    replace_once(
        path,
        '''    entries = {entry.name: entry for entry in snapshot.iterdir()}\n    missing = sorted(EXPECTED_SNAPSHOT_FILES - set(entries))\n    unexpected = sorted(set(entries) - EXPECTED_SNAPSHOT_FILES)\n    if missing:\n        violations.append(\n            f"{snapshot.relative_to(ROOT)}: missing canonical files: {', '.join(missing)}"\n        )\n    if unexpected:\n        violations.append(\n            f"{snapshot.relative_to(ROOT)}: unexpected entries: {', '.join(unexpected)}"\n        )\n\n    for name in EXPECTED_SNAPSHOT_FILES & set(entries):\n''',
        '''    entries = {entry.name: entry for entry in snapshot.iterdir()}\n    entry_names = set(entries)\n    expected_snapshot_files = (\n        CURRENT_SNAPSHOT_FILES\n        if "early-redemption-rules.json" in entry_names\n        else LEGACY_SNAPSHOT_FILES\n    )\n    missing = sorted(expected_snapshot_files - entry_names)\n    unexpected = sorted(entry_names - expected_snapshot_files)\n    if missing:\n        violations.append(\n            f"{snapshot.relative_to(ROOT)}: missing canonical files: {', '.join(missing)}"\n        )\n    if unexpected:\n        violations.append(\n            f"{snapshot.relative_to(ROOT)}: unexpected entries: {', '.join(unexpected)}"\n        )\n\n    for name in expected_snapshot_files & entry_names:\n''',
    )
    replace_once(
        path,
        '''    if set(files) != EXPECTED_DATA_FILES:\n        violations.append(\n            f"{manifest_path.relative_to(ROOT)}: manifest files must be exactly "\n            f"{sorted(EXPECTED_DATA_FILES)}"\n        )\n        return violations\n\n    for name in sorted(EXPECTED_DATA_FILES):\n''',
        '''    expected_data_files = (\n        CURRENT_DATA_FILES\n        if "early-redemption-rules.json" in files\n        else LEGACY_DATA_FILES\n    )\n    if set(files) != expected_data_files:\n        violations.append(\n            f"{manifest_path.relative_to(ROOT)}: manifest files must be exactly "\n            f"{sorted(expected_data_files)}"\n        )\n        return violations\n\n    for name in sorted(expected_data_files):\n''',
    )
    replace_once(path, "relative not in EXPECTED_SNAPSHOT_FILES", "relative not in CURRENT_SNAPSHOT_FILES")
    replace_once(
        path,
        '''        missing = sorted(EXPECTED_SNAPSHOT_FILES - selected_paths)\n        unexpected = sorted(selected_paths - EXPECTED_SNAPSHOT_FILES)\n''',
        '''        missing = sorted(CURRENT_SNAPSHOT_FILES - selected_paths)\n        unexpected = sorted(selected_paths - CURRENT_SNAPSHOT_FILES)\n''',
    )


def patch_smoke() -> None:
    replace_once(
        "scripts/smoke_pages.py",
        '''def _document_count(document: dict[str, Any]) -> int | None:\n    for key in ("series", "productDefinitions", "observations"):\n''',
        '''def _document_count(document: dict[str, Any]) -> int | None:\n    for key in ("series", "productDefinitions", "rules", "observations"):\n''',
    )


def patch_manifest_schema() -> None:
    replace_once(
        "schemas/snapshot-manifest-v1.schema.json",
        '''        "product-definitions.json": { "allOf": [{ "$ref": "#/$defs/fileEntry" }, { "properties": { "path": { "const": "product-definitions.json" } } }] },\n        "gus-cpi.json":''',
        '''        "product-definitions.json": { "allOf": [{ "$ref": "#/$defs/fileEntry" }, { "properties": { "path": { "const": "product-definitions.json" } } }] },\n        "early-redemption-rules.json": { "allOf": [{ "$ref": "#/$defs/fileEntry" }, { "properties": { "path": { "const": "early-redemption-rules.json" } } }] },\n        "gus-cpi.json":''',
    )


def patch_tests() -> None:
    replace_once(
        "tests/test_architecture_invariants.py",
        '''    "catalog-v2.json",\n    "product-definitions-v2.json",\n    "gus-cpi-v2.json",\n''',
        '''    "catalog-v2.json",\n    "product-definitions-v2.json",\n    "early-redemption-rules-v2.json",\n    "gus-cpi-v2.json",\n''',
    )
    replace_once(
        "tests/test_manifest_schema_semantics.py",
        '''    def test_unexpected_top_level_manifest_property_is_rejected(self) -> None:\n''',
        '''    def test_legacy_manifest_without_early_redemption_file_remains_schema_valid(self) -> None:\n        document = current_manifest()\n        document["files"].pop("early-redemption-rules.json", None)\n        self.assertEqual([], self.validate(document))\n\n    def test_unexpected_top_level_manifest_property_is_rejected(self) -> None:\n''',
    )


def patch_docs() -> None:
    replace_once(
        "README.md",
        '''/v1/snapshots/<datasetRevision>/product-definitions.json\n/v1/snapshots/<datasetRevision>/gus-cpi.json\n''',
        '''/v1/snapshots/<datasetRevision>/product-definitions.json\n/v1/snapshots/<datasetRevision>/early-redemption-rules.json\n/v1/snapshots/<datasetRevision>/gus-cpi.json\n''',
    )
    replace_once(
        "README.md",
        '''data/\n  products/        canonical product rules\n  series/          canonical series terms and corrections\n  reference/       canonical GUS and NBP facts\n''',
        '''data/\n  products/        canonical product rules\n  series/          canonical series terms and corrections\n  early-redemption-rules.json  versioned early-redemption rules\n  reference/       canonical GUS and NBP facts\n''',
    )
    replace_once(
        "README.md",
        '''- the official Treasury Bond offer pages for an independent current-offer cross-check;\n''',
        '''- official Treasury Bond offer pages and issuance letters for independent cross-checks and early-redemption terms;\n''',
    )
    replace_once(
        "README.md",
        '''- changed series terms create the next `termsRevision`;\n- corrected GUS observations create the next `revision` for that period;\n''',
        '''- changed series terms create the next `termsRevision`;\n- changed early-redemption terms create the next family `rulesRevision`;\n- corrected GUS observations create the next `revision` for that period;\n''',
    )

    replace_once(
        "docs/ARCHITECTURE.md",
        '''| Product rules | `data/products/<TYPE>/rules-vN.json` |\n| Bond series and corrections | `data/series/<TYPE>/<SERIES>/terms-vN.json` |\n''',
        '''| Product rules | `data/products/<TYPE>/rules-vN.json` |\n| Early-redemption rules | `data/early-redemption-rules.json` |\n| Bond series and corrections | `data/series/<TYPE>/<SERIES>/terms-vN.json` |\n''',
    )
    replace_once(
        "docs/ARCHITECTURE.md",
        '''- `product-definitions.json`;\n- `gus-cpi.json`;\n''',
        '''- `product-definitions.json`;\n- `early-redemption-rules.json`;\n- `gus-cpi.json`;\n''',
    )
    replace_once(
        "docs/ARCHITECTURE.md",
        '''15. Pages deploys only reviewed `main`; consumers can keep and use an already downloaded immutable snapshot without Pages being online.\n''',
        '''15. Pages deploys only reviewed `main`; consumers can keep and use an already downloaded immutable snapshot without Pages being online.\n16. Early-redemption `(productType, rulesRevision)` facts are append-only and every catalog sale window resolves to exactly one rule.\n''',
    )


def main() -> None:
    patch_pipeline()
    patch_immutable_checker()
    patch_smoke()
    patch_manifest_schema()
    patch_tests()
    patch_docs()
    print("Applied early-redemption contract integration patch.")


if __name__ == "__main__":
    main()
