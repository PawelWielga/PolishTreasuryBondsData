# PolishTreasuryBondsData

Public, versioned data source for Polish retail Treasury Bonds (detaliczne obligacje Skarbu Państwa).

The repository separates **financial facts** from applications that consume them. It is intended for Inspector Budget and can also be consumed by other software.

## Public endpoints

Stable v1 datasets are published as static JSON files:

- `dist/catalog-v1.json` - immutable versions of Treasury Bond series terms,
- `dist/reference-data-v1.json` - verified NBP reference-rate and GUS CPI observations,
- `dist/metadata.json` - generated hashes and counts after the scheduled updater runs.

Raw URL base:

```text
https://raw.githubusercontent.com/PawelWielga/PolishTreasuryBondsData/main/dist/
```

Consumers should cache the last successfully validated response locally and must not require GitHub availability to open an existing portfolio.

## Supported product families

The v1 contract currently models:

```text
OTS
ROR
DOR
TOS
COI
EDO
```

The application consuming the data owns calculation logic. This repository owns acquisition, normalization, provenance and publication of facts.

## Sources

Primary sources are official Polish institutions only:

- Ministry of Finance / Obligacje Skarbowe: https://www.obligacjeskarbowe.pl/
- NBP: https://nbp.pl/
- GUS: https://stat.gov.pl/

Every series and reference observation contains its source URL. Missing data is left missing; the pipeline never substitutes zero, the latest known value or a forecast.

## Update model

`update-data.yml` runs daily and:

1. reads the current official Ministry of Finance offer,
2. discovers the current OTS/ROR/DOR/TOS/COI/EDO series,
3. fetches each official detail page,
4. parses sale dates, rates, margins and product rules,
5. compares the result with immutable versions already stored in the repository,
6. adds a new `termsVersion` only when the financial terms changed,
7. validates JSON Schema and semantic invariants,
8. runs parser regression tests,
9. commits only validated changes.

If an official page changes shape and cannot be parsed, the workflow fails and the last valid `dist` remains untouched.

NBP/GUS observations are kept in the same public contract. They are deliberately curated from official publications until a stable machine-readable provider is verified for the exact observations required by retail-bond calculations.

## Versioning

A bond purchase must be pinned to `(seriesCode, termsVersion)`.

Existing versions are immutable. If an official correction changes the financial parameters of the same series, a new version is added instead of overwriting the version used by historical purchases.

Breaking JSON changes require a new endpoint, e.g. `catalog-v2.json`. Existing v1 files remain compatible.

## Repository layout

```text
.github/workflows/
  validate.yml
  update-data.yml

data/
  series/catalog-source.json
  reference/reference-source.json

dist/
  catalog-v1.json
  reference-data-v1.json
  metadata.json

schemas/
  catalog-v1.schema.json
  reference-data-v1.schema.json

scripts/
  pipeline.py
  update.py

tests/
  test_pipeline.py
```

## Local validation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update.py --offline
```

To also query current official Ministry of Finance pages:

```bash
python scripts/update.py
```

## Licensing and provenance

Repository code is MIT licensed. Official source data remains subject to the terms and legal framework applicable to the publishing institution. This repository does not claim ownership of Ministry of Finance, NBP or GUS source publications.

This project is not an official Ministry of Finance, NBP or GUS service and does not provide investment advice.
