# Architecture

## Purpose

PolishTreasuryBondsData is a small, auditable source of facts needed to calculate Polish retail Treasury Bond holdings offline. It is intentionally not a general data platform or monitoring system.

The design optimizes for correctness, historical integrity, deterministic publication, official provenance and maintainability by one maintainer.

## Trust model

The repository protects against realistic operational failures:

- upstream format changes;
- parser defects;
- partial or stale source responses;
- accidental loss or mutation of already published facts;
- stale generated files;
- unsafe HTTP redirects;
- supply-chain drift;
- an incorrect pull request;
- an incorrect Pages deployment.

It does not attempt to defend against a repository administrator who can modify data, validators, workflows and Git history at the same time. Such a requirement would need an external trust anchor and is outside this repository's threat model.

## Sources of truth

| Information | Canonical source |
| --- | --- |
| Product rules | `data/products/<TYPE>/rules-vN.json` |
| Bond series and corrections | `data/series/<TYPE>/<SERIES>/terms-vN.json` |
| GUS CPI facts | `data/reference/gus-cpi.json` |
| NBP reference-rate facts | `data/reference/nbp-reference-rates.json` |
| Raw MF evidence | `data/sources/mf/<sha256>.xls` |
| Raw GUS evidence for new refreshes | `data/sources/gus/<sha256>.json.gz` plus a content-addressed manifest |
| Raw NBP evidence for new refreshes | `data/sources/nbp/<sha256>.xml` |
| Durable source verification state | `data/source-status.json` |
| Public immutable data | `publication/v1/snapshots/<datasetRevision>/` |
| Current public pointer | `publication/v1/latest.json` |
| Public source freshness | `publication/v1/status.json` |
| Document contracts | `schemas/` |
| Frozen legacy v1 | `dist/*-v1.json` and `dist/metadata.json` |

There is no active v2 representation in `dist/`. Current v2 documents exist only inside immutable publication snapshots.

## Data flow

```text
official source
    ↓
raw evidence
    ↓
source-specific parser and semantic validation
    ↓
canonical facts in data/
    ↓
deterministic publication builder
    ↓
JSON Schema + semantic validation
    ↓
immutable snapshot + manifest SHA-256
    ↓
latest.json
    ↓
reviewed main
    ↓
GitHub Pages
```

### Evidence

MF rows are reproducible from the referenced content-addressed XLS, including sheet and row coordinates.

New GUS refreshes retain the exact response bodies used by the importer, stored as gzip-compressed artifacts to avoid enormous text diffs. The filename and manifest SHA-256 are calculated from the original uncompressed response bytes, so decompression reproduces the exact official payload. A content-addressed manifest records the official request URL, raw-content SHA-256 and `contentEncoding: gzip`.

New NBP refreshes retain exact archive and current-rate XML bytes and record both hashes in canonical provenance.

Existing historical GUS/NBP facts are not assigned fabricated evidence retroactively. Their current provenance is preserved until an official source is successfully re-verified and raw evidence is actually captured.

## Schema versus importer policy

JSON Schema describes stable document shape and semantics. It may require a URI but does not freeze today's MF, GUS or NBP endpoint into a schema version.

Current endpoint allowlists, redirect rules and transport restrictions live in `scripts/sources.py`. This keeps historical documents valid if an institution later moves an official endpoint while preserving a strict network boundary for the updater.

## Publication model

`publication/v1/latest.json` points to one immutable snapshot. A snapshot contains:

- `catalog.json`;
- `product-definitions.json`;
- `gus-cpi.json`;
- `nbp-reference-rates.json`;
- `manifest.json`.

The manifest binds every logical file by SHA-256, schema version and record count and includes dataset coverage and provenance.

A correction never edits a published record in place. It appends a new terms/reference revision and therefore produces a new dataset revision. Older snapshots remain addressable.

`status.json` is deliberately mutable. It reports source freshness for the currently available dataset and is not part of immutable financial history.

## Core invariants

1. Frozen legacy v1 files are byte-identical and are never regenerated.
2. Product rules have one canonical representation under `data/products/`.
3. A series `(seriesCode, termsRevision)` is append-only.
4. A GUS `(period, revision)` and NBP `(effectiveFrom, revision)` is append-only.
5. A historical correction creates the next contiguous revision instead of overwriting history.
6. Canonical facts required by the currently reviewed snapshot cannot disappear or mutate in place.
7. A snapshot present in the reviewed PR base cannot be modified, deleted or extended.
8. A candidate may add at most one new complete snapshot, and `latest.json` must select it.
9. `latest.json` cannot roll back to an older existing snapshot.
10. Every manifest SHA-256 must match the exact published bytes.
11. A build from the same checked-in canonical facts is deterministic and idempotent.
12. Every MF series revision remains reproducible from content-addressed official XLS evidence.
13. New GUS/NBP source verification captures raw official evidence before the corresponding provenance is published.
14. Network endpoint policy is enforced by the importer, not encoded as a permanent public schema rule.
15. Pages deploys only reviewed `main`; consumers can keep and use an already downloaded immutable snapshot without Pages being online.

## GitHub Actions boundaries

### Update data

The updater is an acquisition job, not a production publisher. It fetches and cross-checks official sources, captures evidence, updates canonical facts, builds a deterministic candidate and opens a pull request.

### Validate data

`Validate data` is the review gate. It runs the test suite, compares immutable snapshot namespaces with the reviewed base and proves that an offline rebuild reproduces the committed candidate.

### Pages

Pages deploys only approved `main`. It verifies the checked-in publication, derives mutable freshness status, deploys the static contract and runs a public smoke test.

Running the same complete validation stack in every workflow is intentionally avoided because those executions would use the same repository code and would not form independent security boundaries.
