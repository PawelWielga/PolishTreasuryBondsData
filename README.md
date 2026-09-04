# PolishTreasuryBondsData

> **Status: PRODUCTION READY** — the public GitHub Pages data contract, update pipeline, validation gates, source-health monitoring and post-deployment smoke tests are approved for production consumer use as of 2026-09-03.

Public, versioned and auditable facts for Polish retail Treasury Bonds. The repository acquires and normalizes official data; portfolio valuation and the selection of a CPI/NBP observation for an interest period belong to consuming applications such as Inspector Budget.

## Production status

The supported production entry point is:

```text
https://pawelwielga.github.io/PolishTreasuryBondsData/v1/latest.json
```

Operational source health is published at:

```text
https://pawelwielga.github.io/PolishTreasuryBondsData/v1/status.json
```

Production readiness means that:

- `main` is the protected production branch and requires the `validate` GitHub Actions check before merge;
- financial-data changes reach `main` only through reviewed pull requests;
- official-source parser disagreement or incomplete required data fails closed;
- published snapshots are immutable, versioned and SHA-256 verified;
- historical GUS/NBP corrections are append-only revisions rather than destructive rewrites;
- GitHub Pages deploys only reviewed repository state and runs an end-to-end public smoke test after deployment;
- source freshness is derived independently so an upstream outage cannot silently leave stale data marked fresh;
- Python dependencies and GitHub Actions are supply-chain pinned and updated through reviewable Dependabot pull requests;
- the frozen legacy v1 artifacts remain byte-compatible for existing consumers.

Production consumers must still implement the documented last-known-good cache behavior, verify manifest hashes/schema versions and treat `status.json` as operational health. This repository is not an official MF, NBP or GUS service and does not provide investment advice.

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

Opening an existing portfolio must never require GitHub Pages to be available. The old `raw.githubusercontent.com/.../dist/catalog-v1.json`, `reference-data-v1.json` and `metadata.json` files are frozen compatibility artifacts, not the supported update endpoint.

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

Money uses integer PLN minor units (`10000` means PLN 100.00). `faceValueMinorUnits` is the official fixed PLN 100 nominal value of one retail Treasury Bond and is deliberately independent from its sale or exchange price. Rates and margins use exact decimal percentage strings (`"5.35"` means 5.35%), never JSON binary floating point. Each series uses a stable integer `termsRevision` and a deterministic SHA-256 `contentHash` over calculation-relevant facts. A financial correction creates another revision; a provenance-only change does not.

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

### Frozen v1 compatibility contract

The legacy files `dist/catalog-v1.json`, `dist/reference-data-v1.json` and `dist/metadata.json` are frozen byte-for-byte. The v2 builder does not generate or modify them. In particular, `dist/metadata.json` keeps its original v1 shape with `catalog` and `referenceData` entries and must never be replaced by a v2 snapshot manifest while advertising `schemaVersion: "1.0"`.

CI pins the exact Git blob hashes of all three v1 artifacts and also verifies that a complete v2 build leaves them unchanged. Any intentional future change to the v1 compatibility contract requires an explicit compatibility decision and corresponding golden-hash update; normal data refreshes must use the versioned Pages snapshot contract instead.

## Official sources

### Ministry of Finance

The primary series source is the official `Dane dotyczące obligacji detalicznych.xls` attachment discovered on:

```text
https://www.gov.pl/web/finanse/obligacje-detaliczne1
```

The importer retains the workbook URL, SHA-256, sheet and row. Current offer facts are independently compared with the relevant `obligacjeskarbowe.pl` detail page. A disagreement in series code, sale window, price or first-period rate blocks the update; for OTS, the fixed maturity interest amount is independently cross-checked as well. Checked-in, content-addressed workbooks make historical backfills reproducible offline.

The public catalog includes every series from the workbook that can still be outstanding on the dataset date. Once imported, matured series remain in later snapshots. A live refresh also requires the workbook to contain a current-month offering for every supported family; an unchanged or partially stale workbook cannot refresh source health indefinitely. Coverage and gaps are machine-readable in every manifest.

### GUS CPI

Monthly year-over-year CPI comes from the official public SDP API. Years through 2025 use indicator `1832`; 2026+ uses variable `305`, COICOP 2018 section `1698`, with complete pagination. The dataset preserves both the official previous-year-100 index and the derived exact percentage difference, including negative CPI.

CPI rows are source observations only. There is deliberately no `appliesToInterestPeriodStartMonth`, inflation floor or latest-value fallback. The updater requires every previously published period to remain present in the official response and rejects gaps or missing closed years. Year completeness is based on the latest observed CPI year rather than the calendar changing on January 1, so the previous December may remain legitimately unpublished until GUS releases it; once a newer-year observation appears, the preceding year must be complete. A live refresh allows at most a two-calendar-month publication lag; older data fails closed instead of refreshing `lastSuccessAt`.

### NBP reference rate

Reference-rate data comes directly from the official machine-readable NBP XML files:

```text
https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml
https://static.nbp.pl/dane/stopy/stopy_procentowe.xml
```

The archive provides the complete supported change timeline and the current file independently confirms the latest effective reference rate. The updater requires both files to identify the same latest effective date and percentage value. It also verifies that the archive still contains every previously published effective date within the supported range. Any disagreement, source lag in either direction or regression in archive coverage fails closed and preserves the last-known-good snapshot instead of guessing a missing decision.

The published NBP history intentionally starts at `2022-05-06`, the first reference-rate observation needed for supported ROR/DOR offerings. Older entries present in the NBP archive are not backfilled into this contract. The dataset publishes exact effective dates and percentage strings and does not choose a rate for any ROR/DOR interest period.

### Reference observation revisions

Official historical corrections from GUS or NBP are append-only. If multiple rows have the same GUS `period` or NBP `effectiveFrom`, they are separate revisions of the same observation identity. The row with the highest integer `revision` is the current official observation for that identity; lower revisions remain published for provenance and reproducibility.

Consumers must first select the highest revision for each observation identity and only then apply their own bond interest-period selection logic. They must not treat multiple revisions as multiple months or rate changes. Manifest `coverage.*.observationCount` counts distinct observation identities, not revision rows.

A correction creates a new dataset snapshot. Earlier snapshot directories remain byte-identical and therefore continue to reproduce the data that was available before the correction.

## Freshness and failures

The immutable manifest records the sources and coverage used for the selected financial snapshot. Public `status.json` is deliberately separate operational state. Its `status` value is derived independently for MF, GUS and NBP from durable `lastSuccessAt` plus `staleAfterHours` at Pages deployment time.

- MF and GUS become `STALE` 744 hours after their last successful verification because their meaningful cadence is monthly.
- NBP becomes `STALE` 168 hours after its last successful verification because a rate decision can take effect between monthly catalog updates.
- a source with no successful verification is `UNAVAILABLE`;
- a durable `PARTIAL` coverage state remains `PARTIAL` while fresh and becomes `STALE` when its freshness window expires;
- a failed refresh never advances `lastSuccessAt`, never selects another `datasetRevision` and never rewrites an immutable snapshot;
- `STALE` can therefore still point to a fully usable last-known-good financial snapshot.

Pages is redeployed on a six-hour schedule even when `main` has not changed. Immediately before upload, `scripts/status.py` recalculates only `publication/v1/status.json`; `latest.json` and snapshot files are left untouched. This means prolonged upstream failures cannot leave public health indefinitely `FRESH` merely because the failed updater never created a data PR.

`lastAttemptAt`, `lastAttemptStatus` and `message` describe the last durable attempt recorded in reviewed repository state. They are not guaranteed to include every transient CI failure. For freshness decisions, `lastSuccessAt`, `staleAfterHours` and the derived public `status` are authoritative. Offline consumers can perform the same age calculation locally.

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
scheduled source updater -> official sources -> cross-checks -> schemas/tests
-> deterministic candidate snapshot -> bot/update-data pull request
-> manual review and merge -> immutable data available on main

push/manual/6-hour Pages run -> validate reviewed main
-> derive status.json from durable lastSuccessAt timestamps
-> deploy same selected financial snapshot + current operational health
```

The source updater never pushes financial data directly to `main` and never deploys an unreviewed candidate. Re-running updates the same bot PR. The independent Pages health refresh does not fetch or accept new financial facts and cannot advance `latest.json`.

## Local validation

`requirements.txt` is the reproducible CI lock for CPython 3.12.12 on GitHub-hosted Ubuntu x86_64. To reproduce the production validation environment on Linux/WSL, use Python 3.12.12 and the same fail-closed install command as CI:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update.py --offline --check
```

For portable local development on Windows or macOS, install the reviewed direct dependencies from `requirements.in`; the protected GitHub Actions run remains the authoritative reproducibility check:

```text
python -m venv .venv
# activate .venv using your shell/platform
python -m pip install -r requirements.in
python -m unittest discover -s tests -v
python scripts/update.py --offline --check
```

Deterministic health rendering for inspection:

```bash
python scripts/status.py --as-of 2026-09-03T12:00:00Z
```

Live canary/update:

```bash
python scripts/update.py
```

Any source/network/parser disagreement stops before `latest.json` can advance, preserving the last-known-good snapshot.

## Licensing

Repository code is MIT licensed. Official source data remains subject to the legal framework of the publishing institution. This project is not an official MF, NBP or GUS service and does not provide investment advice.
