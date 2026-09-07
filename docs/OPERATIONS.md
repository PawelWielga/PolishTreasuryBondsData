# Operations

## Branch and review policy

`main` is the production source of truth and must remain protected. Changes should flow through pull requests with the required `validate` check. Do not disable branch protection, force-push `main` or bypass required checks to publish data faster.

The repository trust boundary is the reviewed PR base plus the candidate tree. It does not attempt to self-protect against an administrator who can rewrite data, validation code, workflows and Git history together.

## Automated updater

`.github/workflows/update-data.yml` acquires official data and opens or updates `bot/update-data`.

The updater:

1. checks out reviewed `main`;
2. installs hash-locked dependencies;
3. fetches and cross-checks MF, GUS and NBP;
4. captures content-addressed official evidence required by newly published provenance;
5. updates canonical facts using append-only revisions and builds the candidate immutable snapshot;
6. stages the exact candidate and proves that an offline rebuild reproduces it;
7. builds a factual pull-request summary;
8. opens or updates the data pull request.

The updater intentionally uses the repository `GITHUB_TOKEN` rather than a long-lived credential. GitHub therefore holds the `pull_request` workflow for a bot-created data PR until a maintainer approves it. Review the generated data-only diff, select **Approve workflows to run**, and merge only after the real required `validate` check passes. A separately dispatched workflow run is not a substitute for the protected-branch check.

The updater never pushes production data directly to `main`.

### Failure behavior

A live update starts only from a clean managed tree. On an acquisition/build failure it restores the checked-in `data/` and `publication/` state and removes partial generated output.

Source disagreements, missing historical coverage, unsafe redirects, malformed source payloads, missing current offerings or future-dated observations fail closed.

`--skip-cross-check` is not available to the production CLI. Tests that intentionally exercise an MF fixture can call `sync_mf(..., cross_check=False)` directly.

A future `--as-of` date is rejected.

## Evidence maintenance

### MF

MF evidence is stored as:

```text
data/sources/mf/<sha256>.xls
```

Every referenced digest must match the exact bytes. Existing financial terms are reconstructed from the workbook during tests.

### GUS

New live refreshes retain exact GUS response bytes as deterministically compressed artifacts:

```text
data/sources/gus/<sha256>.json.gz
```

The filename SHA-256 is calculated from the original uncompressed official response bytes. A content-addressed `*.manifest.json` records each official request URL, the raw-content SHA-256 and `contentEncoding: gzip`; validation decompresses the artifact and checks the hash against the original bytes.

### NBP

New live refreshes write exact XML bytes as:

```text
data/sources/nbp/<sha256>.xml
```

Canonical NBP provenance records both archive and current-rate SHA-256 values.

Do not invent evidence for older normalized facts. If raw bytes were not retained historically, leave the historical provenance as-is until a real official re-verification captures evidence.

## Validate data

`.github/workflows/validate.yml` is the authoritative PR gate. It performs:

- the complete unit/regression suite;
- immutable-snapshot comparison against the reviewed `main` merge base;
- an offline rebuild/check of canonical facts and publication output.

This workflow protects invariants rather than the textual implementation of other workflows.

## Pages deployment

`.github/workflows/pages.yml` publishes only approved `main` (plus scheduled/manual freshness runs). Push deployments are path-filtered to changes that can affect the public site or its publication tooling.

It:

1. verifies the checked-in immutable snapshot and manifest hashes;
2. renders current `v1/status.json` from durable source-success timestamps;
3. stages the immutable publication and public JSON Schemas;
4. deploys GitHub Pages;
5. runs the public smoke test against the deployed contract.

Pages does not fetch new financial data. A deployment therefore cannot silently introduce an upstream data change that bypassed review.

## Public contract smoke test

The smoke test checks at least:

- `v1/latest.json`;
- the selected manifest;
- every manifest-bound snapshot file and its SHA-256;
- `v1/status.json`;
- public JSON Schemas byte-for-byte;
- a retained prior immutable snapshot when one exists.

A failed smoke test is an operational deployment failure, not permission to rewrite an immutable snapshot.

## Local commands

Install dependencies:

```bash
python -m pip install --require-hashes -r requirements.txt
```

Run the complete test suite:

```bash
python -m unittest discover -s tests -v
```

Validate the current immutable archive:

```bash
python scripts/check_immutable_snapshots.py
```

Verify deterministic offline state:

```bash
python scripts/update.py --offline --check
```

Run a live update candidate locally:

```bash
python scripts/update.py
```

For a reviewed-base comparison equivalent to PR validation:

```bash
python scripts/check_immutable_snapshots.py --base <reviewed-base-sha>
```

## Frozen legacy v1

`dist/catalog-v1.json`, `dist/reference-data-v1.json` and `dist/metadata.json` are repository compatibility artifacts and must remain byte-identical. The current builder never rewrites them, and they are not part of the current GitHub Pages consumer contract.

There are no current v2 generated files under `dist/`.

## Recovery principles

If an official source fails or changes unexpectedly:

- do not guess missing financial values;
- do not weaken validation merely to make the updater green;
- keep the last reviewed immutable snapshot available;
- mark source freshness conservatively through status;
- adapt the source-specific importer only after confirming the new official format/endpoint;
- preserve previously published history and create new revisions for genuine corrections.
