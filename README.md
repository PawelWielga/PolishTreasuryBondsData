# PolishTreasuryBondsData

Public, versioned and auditable facts for Polish retail Treasury Bonds. The repository acquires and normalizes official data; portfolio valuation and the selection of a CPI/NBP observation for an interest period belong to consuming applications such as Inspector Budget.

## Supported endpoint

GitHub Pages is the supported delivery channel:

```text
https://pawelwielga.github.io/PolishTreasuryBondsData/v1/latest.json
```

The consumer flow is:

1. read `v1/latest.json`;
2. keep the local cache when `datasetRevision` has not changed;
3. otherwise download its immutable manifest and snapshot files;
4. validate schema versions and every SHA-256 hash;
5. atomically replace the local last-known-good cache only after the complete snapshot validates.

Opening an existing portfolio must never require GitHub Pages to be available. The old `raw.githubusercontent.com/.../dist/catalog-v1.json` and `reference-data-v1.json` files are frozen compatibility artifacts, not the supported update endpoint.

## Snapshot layout

```text
/v1/latest.json
/v1/status.json
/v1/snapshots/<datasetRevision>/manifest.json
/v1/snapshots/<datasetRevision>/catalog.json
/v1/snapshots/<datasetRevision>/product-definitions.json
/v1/snapshots/<datasetRevision>/nbp-reference-rates.json
/v1/snapshots/<datasetRevision>/gus-cpi.json
```

Only `latest.json` and `status.json` are mutable. A snapshot directory is content-addressed, retained forever and must remain byte-identical. `datasetRevision` identifies a complete publication and is independent of a series `termsRevision`.

## Catalog v2

The catalog supports OTS, ROR, DOR, TOS, COI, EDO, ROS and ROD. It separates:

- reusable family rules in `product-definitions.json`;
- concrete official offerings in `catalog.json`;
- raw CPI and NBP observations in separate reference files;
- immutable provenance in the snapshot manifest;
- current source health in `status.json`.

Money uses integer PLN minor units (`10000` means PLN 100.00). Rates and margins use exact decimal percentage strings (`"5.35"` means 5.35%), never JSON binary floating point. Each series uses a stable integer `termsRevision` and a deterministic SHA-256 `contentHash` over calculation-relevant facts. A financial correction creates another revision; a provenance-only change does not.

### Migration from v1

| v1 | v2 |
|---|---|
| `type` | `productType` |
| duplicated family rules | `productDefinition` reference |
| `nominalValue` number | `faceValueMinorUnits` integer |
| implicit sale price | `issuePriceMinorUnits` |
| no exchange price | `exchangePriceMinorUnits` |
| decimal rate number | exact `firstPeriodAnnualRatePercent` string |
| `rateRule.margin` | exact `marginPercent` string |
| free-form `termsVersion` | integer `termsRevision` + `contentHash` |

Consumers that only implement v1 must reject schema `2.0` or ignore the v2 endpoint as a whole. They must not interpret ROS/ROD as another family.

## Official sources

### Ministry of Finance

The primary series source is the official `Dane dotyczące obligacji detalicznych.xls` attachment discovered on:

```text
https://www.gov.pl/web/finanse/obligacje-detaliczne1
```

The importer retains the workbook URL, SHA-256, sheet and row. Current offer facts are independently compared with the relevant `obligacjeskarbowe.pl` detail page. A disagreement in series code, sale window, price or first-period rate blocks the update. Checked-in, content-addressed workbooks make historical backfills reproducible offline.

The public catalog includes every series from the workbook that can still be outstanding on the dataset date. Once imported, matured series remain in later snapshots. Coverage and gaps are machine-readable in every manifest.

### GUS CPI

Monthly year-over-year CPI comes from the official public SDP API. Years through 2025 use indicator `1832`; 2026+ uses variable `305`, COICOP 2018 section `1698`, with complete pagination. The dataset preserves both the official previous-year-100 index and the derived exact percentage difference, including negative CPI.

CPI rows are source observations only. There is deliberately no `appliesToInterestPeriodStartMonth`, inflation floor or latest-value fallback. A missing period remains missing and incomplete closed years fail validation.

### NBP reference rate

The official NBP archive page provides the reference-rate change timeline. The parser reads the embedded `stopy_procentowe_archiwum` and current `interest_rates` XML sections and publishes exact effective dates and percentage strings. It does not choose a rate for any ROR/DOR period.

## Freshness and failures

The immutable manifest records the sources and coverage used for that snapshot. `status.json` separately reports MF, GUS and NBP as `FRESH`, `STALE`, `PARTIAL` or `UNAVAILABLE`, including the last attempt and last successful verification.

- MF and GUS become stale after 744 hours because their meaningful cadence is monthly.
- NBP becomes stale after 168 hours because a rate decision can take effect between monthly catalog updates.
- a failed attempt never advances `lastSuccessAt` or changes the selected financial snapshot;
- `STALE` can still point to a fully usable last-known-good snapshot;
- `PARTIAL` means the advertised source coverage is incomplete and must not be presented as fully current.

The publisher records operational state. Consumers may additionally compute staleness from timestamps and `staleAfterHours` while offline.

## Repository layout

```text
data/products/<family>/rules-vN.json
data/series/<family>/<series>/terms-vN.json
data/reference/*.json
data/sources/mf/<sha256>.xls
schemas/*.json
dist/                         # convenient generated aggregates, v1 frozen
publication/v1/               # Pages pointers and immutable snapshots
scripts/
tests/
```

One series correction changes one small source file. Public clients still download compact generated aggregates, never one request per series. Canonical sorting and CI make stale aggregates fail validation.

## Update and review flow

```text
schedule/manual run -> official sources -> cross-checks -> schemas/tests
-> deterministic candidate snapshot -> bot/update-data pull request
-> manual review and merge -> Pages deploy from main
```

The scheduled updater never pushes financial data directly to `main` and never deploys a candidate before review. Re-running updates the same bot PR.

## Local validation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update.py --offline --check
```

Live canary/update:

```bash
python scripts/update.py
```

Any source/network/parser disagreement stops before `latest.json` can advance, preserving the last-known-good snapshot.

## Licensing

Repository code is MIT licensed. Official source data remains subject to the legal framework of the publishing institution. This project is not an official MF, NBP or GUS service and does not provide investment advice.
