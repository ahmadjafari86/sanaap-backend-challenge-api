# Release checklist — v0.1.0

## Transactional Outbox Phase 1 — Unmerged Feature Checkpoint

This addition is separate from the historical v0.1.0 release record below.
Work stays on `codex/transactional-outbox-phase1`, based on `develop` at `008f1f7`.
The user explicitly prohibits merges into `develop` or `main`; no release/tag is included.

- [x] T16–T18: additive schema/enqueue, relay/leases/status/replay, upload receipts
  and periodic stale/legacy recovery implemented with focused RED→GREEN evidence.
- [x] T19 cleanup: durable deletion/replacement/staging obligations, current-owner
  checks and uncertain-commit compensation protection; 334 PostgreSQL tests pass.
- [x] Fresh migration and upgrade from documents 0004 preserve document/audit
  snapshots and create the expected queue indexes.
- [x] Complete seven-service disposable integration, including post-send process
  exit, real broker task loss/duplication, concurrent relays, legacy tasks and
  cleanup recovery; preserve pre-existing volumes.
- [x] Complete independent whole-branch review and resolve blocking findings.
- [x] Record final evidence and leave the feature branch ready for user review.

Final product checkpoint: `684ddd7`, verified on 2026-10-24. Local PostgreSQL
regression passed **334/334** tests in 20.851 seconds. Rebuilt images passed the
same **334** tests in disposable project `sanaap-t09-2bb0d38923`, with Django
checks, no migration drift/unapplied migrations, warning-free OpenAPI and Nginx
syntax. This runs the `make test`/`make check` equivalents inside the isolated
Compose project, rather than starting or altering a working development stack.

The final seven-service core profile passed relay stop/restart, real Redis task
loss, a hard exit immediately after broker send, lease recovery/duplicate
delivery, concurrent relay processes, old document-ID-only queued tasks, MinIO
cleanup outage/recovery and explicit DEAD replay of an already-missing object.
All **37** main audit events and **15** outbox-probe audit events matched exact
actors/outcomes; the five logical probe uploads had exactly **5** receipts.
Terminal/deleted staging was empty. The disposable stack was removed and every
pre-existing volume was preserved; the separate temporary PostgreSQL test
container was also removed.

Fresh migration and an upgrade from documents 0004 preserve document/audit
snapshots and queue indexes. A further PostgreSQL upgrade probe retained legacy
READY/PENDING rows and staged bytes, then completed the legacy positional task
with one storage attempt, receipt and successful store audit.

One independent review of `008f1f7..6c53811` found no Critical/Minor issues and
two Important issues. Four new PostgreSQL regressions first failed, then passed:
lost acceptance COMMIT acknowledgement/unavailable ownership lookup now preserve
staging; missing-stage and exhausted legacy failures now create their own exact
object cleanup after an earlier compensation was skipped while PENDING. The
fix passed the full suite and rebuilt runtime gate. No unresolved review finding
remains; user review is still pending and no merge, push, tag or release occurred.

Execution decisions and accepted boundaries:

| Decision | Reason and practical cost |
| --- | --- |
| Use the requested feature branch in the existing checkout | Preserves the initial planning changes; this checkout contains feature work, while main/develop references remain at `008f1f7`. |
| Extract task briefs for T16-style headings | The installed helper expects Task 16; identifiers and acceptance criteria were retained, with direct test/commit evidence instead of helper bookkeeping. |
| Execute Phase 1 continuously with per-task commits and final independent review | Implements the requested batch; the user reviews the combined branch rather than approving each intermediate task. |
| Defer durable cache/WebSocket publication to Phase 2 | Current callbacks/version checks remain; an outage may lose a live hint or invalidation, and clients refetch authoritative metadata. |
| Defer pruning/retention/orphan reports to Phase 2 | Preserves receipt/dedupe history; operators must monitor queue and disk growth. |
| Retain documented external-write crash windows | PostgreSQL cannot atomically commit filesystem/MinIO writes; unrecorded orphan bytes can require manual investigation. |
| Keep client receipt/exactly-once physical I/O outside scope | Durable worker receipts do not acknowledge clients; physical I/O can repeat and live hints can be missed. |
| Verify runtime separately from independent source review | The reviewer did not run the seven-service gate; the final rebuilt gate passed before completion was recorded. |
| Keep the branch local and unmerged | Follows the user's explicit branch constraint; publishing the feature remotely requires a separate request. |

Phase 2 (T20–T23) remains deferred. Cache invalidation and WebSocket hints retain
their existing best-effort callbacks; pre-transaction orphan writes remain a
documented limit. Do not mark this feature merged or released.

The user selected and approved **v0.1.0**, prepared on `release/0.1.0` after
T13 merge `fbd4983`. [PR #8](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/pull/8)
merged into `main` at `17133d50264dbdfe174c56765a4a5bf9f7dce378`.
Annotated [tag v0.1.0](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/tree/v0.1.0)
points to that exact merge. [PR #9](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/pull/9)
merged `main` back into `develop` at `4d5cd37276e4975bb0f0dc0912ac892ba904e514`.
Package and lockfile both declare `0.1.0`; runtime behavior and dependencies were
unchanged during release preparation.

This completion record is maintained on `develop` after publication. The immutable
tag retains the preparatory checklist as it stood before the external Git actions.

## Contents

- Mandatory scope: JWT roles, shared image CRUD, validation, private downloads,
  search/filter/pagination, resilient metadata cache, schema and setup documents.
- Optional scope: audit rows, Nginx/Gunicorn ASGI, Celery storage and authenticated
  Channels notifications, each reviewed in its own feature PR.
- Packaging correction: omit `.superpowers/` scratch from Git and Docker context.
  Runtime image checks also exclude `.env`, Git metadata and staged upload bytes.
- Release notes: [CHANGELOG.md](../CHANGELOG.md). API schema version `v1` remains
  separate from the selected package/release number.

## Verification gates

- [x] T13 user approval and PR #7 merge into `develop`; 263 PostgreSQL tests passed
  without skips immediately before that merge (29.005 seconds).
- [x] Agree release version and align package/lockfile metadata if necessary:
  selected v0.1.0; both files already match.
- [x] Inspect the release archive/diff and actual image for local scratch/secrets.
- [x] Run clean six-service integration, complete PostgreSQL regressions, startup,
  migration drift/application, warning-free OpenAPI and active Nginx syntax.
- [x] Verify actual worker/socket delivery, Redis/MinIO failure recovery, signed-URL
  expiration, upload boundaries, durable audits and disposable-volume preservation.
- [x] Validate the production Nginx example with an ephemeral local test certificate
  and no published port; this does not establish real certificate issuance/renewal.
- [x] Verify development service health and preservation of all existing volumes.
- [x] Complete one independent release review; resolve blocking findings and record
  accepted limitations without expanding the feature scope.
- [x] Commit/push the candidate and attach draft
  [PR #8](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/pull/8),
  `release/0.1.0` → `main`.

The final disposable T14 run passed all 263 PostgreSQL regressions and every live
gate above, including exactly 37 audit events and empty terminal staging. It
removed its own stack and preserved all pre-existing volumes. Actual rebuilt-image
inspection confirmed scratch, Git metadata, runtime environment files and staged
bytes are absent. The production-example check verified secure settings,
read-only certificate/static mounts, example 80/443 configuration and non-root
TLS syntax; the test container published no ports and its dummy key was removed.
Independent release review found no Critical, Important or Minor issues.
The development stack also runs the corrected images: all six services are
healthy, all five original volumes are preserved, active-image scratch/env/Git
exclusion passes, and migration/schema/Nginx checks pass again.
Immediately before the approved main merge, all 263 PostgreSQL tests passed again
without skips in 30.158 seconds. Hashes of 84 running code/test/build files matched
the reviewed branch. The main release merge has exactly the reviewed candidate's
tree, and the backmerge initially gave `main` and `develop` identical files.

## Completed publication

The user approved the concrete T14 release checkpoint before these operations:

- [x] Mark PR #8 ready and merge it into `main` with expected head `2faa22d`.
- [x] Create and push annotated `v0.1.0` on reviewed main merge `17133d5` without
  overwriting an existing tag; remote tag object `d0d23d7` peels to that merge.
- [x] Merge `main` back into `develop` through PR #9, preserving Git Flow history.
- [x] Verify release ancestry in both remote branches, exact tag target and a clean
  checkout. Subsequent closeout changes on `develop` are documentation only.

Creating a published GitHub Release or deploying publicly is outside this
checkpoint unless explicitly requested. Existing application data is preserved.

## Accepted boundaries

Storage crash/orphan and broker publication windows, manual PENDING requeue,
best-effort socket hints and established JWT/download URL lifetimes remain as
documented in README. Operators must configure HTTPS Origins/WSS, private Redis,
credentials, certificates and capacity before public deployment. No load test,
forced mid-upload process kill, transactional outbox or automated reconciliation
is claimed.

Two previously accepted T11 coverage minors remain deferred: guaranteeing a changed
backend IP during recreation and explicitly asserting the download no-store header
through the live proxy. Current checks exercise recreation recovery and Django
header behavior; the stronger live assertions are not claimed.
