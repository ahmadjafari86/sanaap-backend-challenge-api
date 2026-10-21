# Release checklist

The user selected **v0.1.0**. The candidate is on `release/0.1.0`, prepared from
`develop` after the approved T13 merge `fbd4983`. Package and lockfile already
declare `0.1.0`, so dependencies and runtime behavior need no version changes.
This document records preparation, not publication.

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
- [ ] Commit/push the candidate and attach a draft `release/0.1.0` → `main` PR.

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

## Shared review and publication

The candidate PR is the T14 review checkpoint. Version selection alone does not
approve publication. After the user reviews the concrete candidate:

- [ ] Mark the reviewed PR ready and merge it into `main` with its expected head.
- [ ] Create the agreed annotated `v0.1.0` tag on the reviewed main merge and
  push it without overwriting any existing tag.
- [ ] Merge `main` back into `develop`, preserving Git Flow release history.
- [ ] Verify remote branch ancestry, exact tag target and a clean checkout.

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
