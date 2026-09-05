# Operations and publication controls

This repository publishes financial source data, so `main` is treated as the reviewed production branch.

## Production readiness

**Status: PRODUCTION READY as of 2026-09-03.**

The production-readiness decision covers the public read-only data publication service consumed through GitHub Pages and the repository pipeline that acquires, validates, reviews and publishes that data. The decision is based on the following controls being active and verified on `main`:

- all supported bond families are represented through versioned schemas and deterministic aggregates;
- MF offer data is cross-checked against an independent official offer page and calculation-relevant parse failures stop publication;
- GUS CPI and NBP reference-rate observations preserve historical corrections as append-only revisions;
- immutable snapshots are content-addressed, protected by manifest SHA-256 hashes and rechecked against their first reviewed Git bytes on every Pages deployment;
- legacy v1 compatibility artifacts are frozen byte-for-byte by regression tests;
- the public Pages consumer path is exercised after every deployment by an end-to-end smoke test;
- source freshness is published independently from immutable financial snapshots and ages fail-closed;
- `main` is protected and requires the `validate` check before merge;
- update automation proposes pull requests instead of publishing financial changes directly;
- Python dependencies are exact/hash-locked, external Actions are pinned to immutable commit SHAs and Dependabot supplies reviewable maintenance PRs;
- network-sensitive workflows have bounded retries/timeouts and deterministic validation remains offline after dependency installation.

Production readiness does not mean that this repository becomes an official MF, NBP or GUS service, nor does it move portfolio valuation logic into this repository. Consumers remain responsible for validating the manifest/schema contract, retaining a last-known-good local cache and applying bond-interest rules correctly.

A future change that breaks a supported schema, removes fail-closed validation, bypasses protected `main`, weakens immutable snapshot guarantees or disables public post-deployment verification should be treated as a production-readiness regression and must not be merged without an explicit replacement control.

## Main branch protection

`main` is protected with the following policy:

- all normal changes must arrive through a pull request;
- the GitHub Actions `validate` check is required before merge;
- the pull-request branch must be up to date with `main` before merge;
- one approving review is required before merge;
- conversation-resolution is not a required branch-protection check;
- linear history is required;
- force pushes are disabled;
- deletion of `main` is disabled;
- branch protection is enforced for repository administrators as well.

There is no standing administrator bypass. If protection itself must ever be changed for repository recovery, that is an explicit administrative action outside the normal publication path and the protection should be restored before financial-data work resumes.

## Automated data updater

The scheduled updater has `contents: write` and `pull-requests: write`, but it does not push financial changes to `main`. It writes only to `bot/update-data` through `peter-evans/create-pull-request` and proposes a pull request.

Because branch protection targets `main`, the bot can continue creating and updating its proposal branch while the eventual merge remains subject to the same required `validate` check as every other pull request.

### Required repository setting

GitHub's repository-level Actions policy must allow workflows to create pull requests. In **Settings → Actions → General → Workflow permissions**, enable **Allow GitHub Actions to create and approve pull requests**. The workflow-level `pull-requests: write` permission is necessary but is not sufficient when that repository switch is disabled.

If the repository setting is disabled, source acquisition and deterministic tests can still succeed and `peter-evans/create-pull-request` can push the generated `bot/update-data` branch, but the final REST request that opens the review pull request is rejected by GitHub. The workflow intentionally remains failed in that state because a candidate branch without its required review surface is not a successful production update cycle. The existing candidate can be recovered by opening a pull request from `bot/update-data` to `main`; financial data must still never be copied directly to `main`.

The 2026-09-05 scheduled run demonstrated this exact failure mode: MF/GUS/NBP acquisition and the regression suite succeeded, then GitHub rejected only PR creation because the repository-level switch was disabled. Treat that setting as part of the production control plane, not as an optional convenience.

### Local MF workbook override

`--mf-workbook` is a reproducibility/debugging input, not a provenance bypass. It requires the exact canonical `https://www.gov.pl/attachment/...` URL and the updater downloads that URL during the run. The local workbook is accepted only when its SHA-256 matches the bytes fetched from that official URL. Redirects may remain only within the original HTTPS origin; a redirect to another host or port fails closed.

Normalized MF series and generated manifests are independently constrained to the canonical MF, GUS and NBP source endpoints, while offer cross-check URLs must remain on `www.obligacjeskarbowe.pl`. A manually edited provenance object therefore cannot make an untrusted domain look official during an offline rebuild.

## Pages publication

GitHub Pages deploys from reviewed `main` only. The Pages workflow validates the checked-in financial publication before upload. Its independent six-hour schedule may recalculate mutable `status.json` from durable source-success timestamps, but it does not fetch new financial facts or advance `latest.json`.

Before every Pages upload, including scheduled freshness-only runs, `scripts/check_immutable_snapshots.py` verifies every retained snapshot directory against the bytes from its first appearance on the current first-parent Git history and requires that snapshot to have been selected by `latest.json` at that first appearance. This is deliberately stronger than checking only the latest push diff: if an immutable snapshot rewrite ever reached `main` through an exceptional protection bypass, a later unrelated commit or scheduled deployment still cannot make those rewritten bytes publishable.

The archive verifier also requires each retained snapshot to keep exactly the five canonical top-level files and rejects symlinked/non-regular snapshot entries. Pull-request and push validation additionally compare the candidate against its reviewed base so a new snapshot may be introduced only as the single complete revision selected by `latest.json`.

This keeps the two responsibilities separate:

1. financial data changes require a validated pull request into protected `main`;
2. operational freshness can age independently without selecting an unreviewed dataset revision.

## Post-deployment smoke test

Every successful Pages deployment is followed by a network smoke test against the public Pages URL. `scripts/smoke_pages.py` follows the same retrieval chain expected from Inspector Budget:

1. fetch `v1/latest.json`;
2. resolve its relative manifest path;
3. require the manifest `datasetRevision` to match `latest.json`;
4. fetch every file declared in the manifest;
5. verify SHA-256 against the bytes returned by Pages;
6. parse every file as JSON and verify its declared `schemaVersion`;
7. verify manifest counts when the document has a known collection field;
8. fetch `v1/status.json`, require it to match the rendered deployment artifact and point at the selected dataset revision.

The smoke tester retries public reads to tolerate short Pages propagation delays, but persistent missing files, broken paths, invalid JSON, revision disagreement, status disagreement or hash mismatch fail the workflow visibly and do not modify repository data.

When at least two real snapshot revisions are retained locally, the same deployment smoke test also fetches and fully verifies the newest prior immutable snapshot. Until the first actual data revision change occurs, the repository has only one genuine snapshot and the workflow reports that fact rather than fabricating historical data for the test.

## Dependency and workflow supply chain

Runtime Python dependencies are split into two files:

- `requirements.in` is the small human-reviewed list of direct dependencies;
- `requirements.txt` is the CI lock for CPython 3.12.12 on GitHub-hosted Ubuntu x86_64 and contains every direct and transitive package at an exact version with a SHA-256 hash for the wheel used by CI.

CI, Pages publication and the scheduled updater install with `--require-hashes --only-binary=:all:`. That makes package resolution fail closed if a pinned artifact changes, disappears, or no longer matches the reviewed hash. The normal offline validation remains network-independent after the install step.

All external GitHub Actions in `.github/workflows` are pinned to immutable 40-character commit SHAs. The adjacent version comments record the reviewed upstream release that each SHA represents. `tests/test_supply_chain.py` prevents floating action tags or an unlocked direct dependency from being reintroduced accidentally.

Dependabot checks both the `github-actions` and `pip` ecosystems every Monday and proposes dependency changes as ordinary reviewable pull requests. Minor and patch updates are grouped; major updates remain separate so breaking changes are explicit. Dependency PRs are subject to the same protected-`main` validation gate as other changes.

Network-sensitive jobs have explicit workflow timeouts: Pages deployment is capped at 15 minutes and the official-source updater at 20 minutes. Validation itself is capped at 10 minutes. These limits bound failures without changing any public dataset contract.
