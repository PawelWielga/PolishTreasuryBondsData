# Operations and publication controls

This repository publishes financial source data, so `main` is treated as the reviewed production branch.

## Main branch protection

`main` is protected with the following policy:

- all normal changes must arrive through a pull request;
- the GitHub Actions `validate` check is required before merge;
- the pull-request branch must be up to date with `main` before merge;
- zero approving reviews are required, which keeps the workflow practical for a single maintainer;
- all review conversations must be resolved;
- linear history is required;
- force pushes are disabled;
- deletion of `main` is disabled;
- branch protection is enforced for repository administrators as well.

There is no standing administrator bypass. If protection itself must ever be changed for repository recovery, that is an explicit administrative action outside the normal publication path and the protection should be restored before financial-data work resumes.

## Automated data updater

The scheduled updater has `contents: write` and `pull-requests: write`, but it does not push financial changes to `main`. It writes only to `bot/update-data` through `peter-evans/create-pull-request` and proposes a pull request.

Because branch protection targets `main`, the bot can continue creating and updating its proposal branch while the eventual merge remains subject to the same required `validate` check as every other pull request.

## Pages publication

GitHub Pages deploys from reviewed `main` only. The Pages workflow validates the checked-in financial publication before upload. Its independent six-hour schedule may recalculate mutable `status.json` from durable source-success timestamps, but it does not fetch new financial facts or advance `latest.json`.

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
7. verify manifest counts when the document has a known collection field.

The smoke tester retries public reads to tolerate short Pages propagation delays, but persistent missing files, broken paths, invalid JSON, revision disagreement or hash mismatch fail the workflow visibly and do not modify repository data.

When at least two real snapshot revisions are retained locally, the same deployment smoke test also fetches and fully verifies the newest prior immutable snapshot. Until the first actual data revision change occurs, the repository has only one genuine snapshot and the workflow reports that fact rather than fabricating historical data for the test.
