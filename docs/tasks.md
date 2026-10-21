# Remaining Implementation Tasks

**Source of requirements:** [design.md](design.md).
**Progress checklist:** [implementation-plan.md](implementation-plan.md).
**Execution:** Implement one task, verify it, and review it with the user before starting the next task. A passing test run does not replace shared review.

## Git Flow

- T01–T09 continue on `feature/documents`, based on `develop` after the accounts PR was merged.
- Keep task changes reviewable and commit them after shared review.
- T09 prepares the documents pull request into `develop`.
- T09–T13 use verified checkpoint commits to open draft PRs; shared review is still required before merging or starting the next task.
- T10–T13 each use a feature branch from updated `develop` after prerequisites have been merged. Their pull requests also target `develop`.
- T14 uses a release branch for the agreed version, merges into `main` and back into `develop`, and tags the release.
- Do not merge or publish a release before its review checkpoint.

## Task Queue

| ID | Deliverable | Type | Status |
| --- | --- | --- | --- |
| T01 | Document upload API | Mandatory | Completed; reviewed by user |
| T02 | Shared document list and detail API | Mandatory | Completed; reviewed by user |
| T03 | Metadata updates and safe image replacement | Mandatory | Completed; reviewed by user |
| T04 | Document deletion after successful database commit | Mandatory | Completed; reviewed by user |
| T05 | Private download URLs valid for 60 seconds | Mandatory | Completed; reviewed by user |
| T06 | Title search, creator filtering, and pagination | Mandatory | Completed; reviewed by user |
| T07 | Resilient document metadata caching | Mandatory | Completed; reviewed by user |
| T08 | OpenAPI, Swagger, and user documentation | Mandatory | Completed; reviewed by user |
| T09 | Mandatory integration checks and documents PR | Mandatory | Completed; reviewed by user; merged into develop |
| T10 | Audit logging | Optional, included in scope | Completed; reviewed by user; merged into develop |
| T11 | Nginx deployment with Gunicorn | Optional, included in scope | Completed; reviewed by user; merged into develop |
| T12 | Celery background document storage | Optional, included in scope | Verified; awaiting shared user review |
| T13 | Authenticated WebSocket notifications | Optional, included in scope | Pending |
| T14 | Final regression checks and Git Flow release | Delivery | Pending |

## Review Checkpoint — T12

- Branch `feature/background-storage` starts from `develop` merge `23edcff`. The user approved the existing Celery/PENDING/READY/FAILED design and one-task-at-a-time execution; merge and T13 remain gated on the next shared review.
- HTTP creation now validates/stages privately and returns 202/PENDING. The worker receives only the document id and atomically records READY or FAILED with a storage audit. Metadata exposes read-only status; downloads and updates require READY (409 otherwise). Admin deletion works in every state; existing rows migrate as READY, while replacements remain synchronous.
- Added Celery with JSON-only Redis DB 1, late acknowledgement/reject-on-worker-loss, four persisted storage attempts, private shared staging, Redis AOF storage, state-aware cache invalidation and explicit `requeue_pending_documents` recovery. Acceptance audits use create/success/202; terminal storage audits use store/success/200 or store/failure/503 with the original creator as actor.
- Focused test-first cycles cover acceptance, commit timing, broker failure, retries/exhaustion, staging cleanup, duplicate delivery, audit rollback, cache transitions, 409 gates and two actual PostgreSQL worker/deletion races. Updated schema, API regressions, isolation guards, real-service integration and README for the changed public contract.
- One independent whole-feature review found one Important rollback-compensation race and no Critical/Minor findings. A deterministic regression reproduced a READY row whose object an older worker deleted. Compensation now reacquires the row lock and rechecks ownership; authoritative READY objects survive, and database uncertainty defers cleanup. The regression and final full PostgreSQL/integration verification pass. No second reviewer or unresolved T12 finding remains.
- Earlier clean-stack runs passed 231 PostgreSQL tests, including both concurrency races, but exposed two deployment issues: a missing integration-only worker environment value, then the upstream bucket initializer silently ignoring our storage subclass. Both are fixed. Startup now calls `initialize_document_storage` against the actual configured backend; fresh-bucket initialization has a dedicated regression. Every failed disposable stack was removed, preserving existing volumes.
- Final clean-stack integration passed **235 PostgreSQL tests**, fresh startup/migrations, warning-free OpenAPI validation, PENDING/202 and all-role 409 gates, queued worker stop/restart recovery to READY, broker failure and exhausted storage retries to FAILED, Redis cache fallback/recovery, exact 10 MiB byte transfer and 400/413 boundaries, signed-URL expiry, replacement/deletion cleanup, and backend outage/recreation recovery. All **37 durable audit events** match authenticated actors and outcomes; terminal/deleted documents leave no staged files. Disposable resources were removed and every pre-existing volume preserved.
- Updated the main stack with migration 0004 and repeated all **235 PostgreSQL tests without skips** in 21.038 seconds. Migration drift, warning-free schema validation and `nginx -t` checks pass; all six services are healthy. Native tests also pass (six expected PostgreSQL-only skips). Implementation and verification are ready for a dedicated draft PR into `develop`; shared user review remains pending before merge/T13.
- Documented boundaries remain: commit-to-publication/broker-loss recovery is manual; there is no transactional outbox, exactly-once transport, automatic orphan reconciliation or disk quota. Forced process death can replay an uncommitted attempt; database-unavailable retries may leave PENDING. The live restart check exercises queued work across a stop/restart, without claiming a forced mid-upload kill test. These limits are distinct from the fixed cross-worker deletion defect.

## Completed Review — T11

- The user approved T11. PR #5 was marked ready and merged into `develop` (`23edcff`) after all 204 PostgreSQL tests passed again. GitHub reported a clean merge with no checks or review objections. T12 starts on `feature/background-storage` from that merge.

- Started `feature/nginx-deployment` from updated `develop` (`4962201`) after the user's T10 approval and PR #4 merge. The user subsequently approved the shared review and merge before T12.
- Added a digest-pinned, non-root Nginx container as the loopback port 8000 entrypoint. Gunicorn remains internal. Backend startup collects public static assets into a project-owned volume mounted read-only in Nginx; private images remain in MinIO.
- Preserved original Host/port, JWT/error headers and upstream responses; overwrite forwarded scheme/client headers, disable API caching/retries, and resolve the backend through Docker DNS. Trusted-proxy behavior is opt-in outside Compose. An 11 MiB total-body limit allows the existing 10 MiB image limit plus multipart overhead.
- Added seven regressions: two disposable-stack isolation guards and five subprocess tests of actual Django settings/middleware, including trusted HTTPS, redirects, and secure session/CSRF cookies. The initial focused run reproduced five missing-behavior failures before implementation; all 13 focused tests now pass.
- Full disposable integration passed **204 PostgreSQL tests** without skips, fresh startup/migrations and warning-free OpenAPI validation. Live checks verified static bytes/MIME, Swagger, JWT/host errors, pagination ports, spoofed forwarding headers, exact 10 MiB upload/download bytes, API 400 at 10 MiB + 1 and proxy 413 for oversized requests, Redis/MinIO outages/recovery, signed-URL expiry, backend gateway failure and recreation recovery without restarting Nginx, and all **23 durable audit events**. Cleanup preserved every pre-existing volume.
- The first backend-outage probe expected only 502 and received 504. Both are valid upstream gateway failures, so the expectation now accepts either while still requiring static availability and successful backend recovery. The complete integration rerun then passed.
- Updated the main stack and repeated all **204 tests** without skips. Migration drift, warning-free schema validation and active Nginx syntax checks passed. The browser rendered Swagger's API title, Authorize control and Authentication/Documents/Users groups through the actual proxy.
- Added operator-applied production Compose/TLS examples and HTTPS deployment instructions. A disposable self-signed certificate validated TLS syntax and certificate readability as the non-root Nginx user; Compose merge checks confirmed secure settings, public 80/443 ports, an internal backend and read-only mounts. No public deployment or certificate issuance/renewal was performed.
- One independent whole-feature review found no Critical or Important issues and independently passed all 13 focused tests. Two **Minor test-coverage improvements** are deferred: force/assert a changed backend IP during recreation (Docker may reuse it), and explicitly assert download `Cache-Control: private, no-store` through the live proxy. Current DNS configuration supports address changes and existing Django tests cover the no-store response; the deployed tests do not prove those two stronger assertions.
- Production certificate lifecycle, external TLS terminator trust, WebSocket upgrades, asynchronous storage and final release remain separate responsibilities/tasks. Proxy health checks Nginx itself; backend health is separate. Normal Nginx error logs remain diagnostic, while the access-log format excludes query strings/headers/bodies.
- Verified implementation checkpoint `dbd67c6` and documentation checkpoint `872808a` were reviewed and merged through [PR #5](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/pull/5).

## Completed Review — T10

- The user approved T10. [PR #4](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/pull/4) was marked ready and merged into `develop` with merge commit `4962201`, after repeating all 197 tests. GitHub reported a clean merge with no pending/failing checks or review objections.
- T11 starts on `feature/nginx-deployment` from that updated `develop` commit.

- Started `feature/audit-logging` from `develop` after the user's T09 approval and PR #3 merge (`580197c`).
- Added `DocumentAuditEvent` and migration 0003. Events store actor/document id snapshots, allowlisted action and outcome, UTC time, and HTTP status. IDs survive document/user deletion; no body, header, filename, object key, credential, URL, or exception text is retained.
- Successful create/update/delete events share the service transaction. An event-write failure rolls back the change and compensates a new upload. URL issuance records success before returning the signed URL; audit-write failure returns safe 503.
- Added a shared API exception mixin for denied, anonymous/invalid-token, validation/parse, missing-document, storage/database, and unexpected failures. Failure auditing isolates SQL errors with a savepoint, preserves the original response, avoids retrying authentication, and safely bounds route ids.
- Existing upload cleanup now reuses the redacted helper: a new regression exposed chained private exception details when both audit insertion and cleanup failed, then passed after removing traceback logging from that handled path.
- Added 24 regressions. The initial 23 failed before the model existed, then passed after implementation. Tests cover real inserts followed by failure, actual NOT NULL violations, rollback and compensation, actor attribution, deleted identifiers, no duplicate normal-operation events, read exclusions, forged fields, and secret leakage.
- Final clean-stack integration passed all **197 PostgreSQL tests**, migration checks, warning-free schema validation, actual file/role/privacy/expiry checks, Redis/MinIO outages, and an exact match of all **19 durable audit events** after the tested documents were deleted. Disposable resources were removed and all pre-existing volumes preserved.
- Rebuilt the main backend, applied migration 0003 on the existing database, and repeated all 197 tests without skips. Migration drift and warning-free OpenAPI checks passed again.
- One independent whole-feature review found no Critical, Important, or Minor findings and independently passed all 24 focused tests. README provides shell inspection instructions and the audit scope/limits.
- Failure-event persistence remains best effort during a database outage/process crash. URL issuance does not prove receipt or transfer. Internal calls may have a null actor; framework failures outside the DRF handler boundary, privileged audit edits, retention automation, historical backfill, and an audit endpoint remain outside this feature.
- Verified checkpoint `68f634d` was reviewed and merged through PR #4. The remaining optional features and release retain their own review gates.

## Completed Review — T09

- The user approved T09. [PR #3](https://github.com/ahmadjafari86/sanaap-backend-challenge-api/pull/3) was marked ready and merged into `develop` using a merge commit, `580197c`. All 173 tests passed again before merging; GitHub reported a clean merge with no pending/failing checks or review objections.
- T10 starts on `feature/audit-logging` from that updated `develop` commit.

- Added `make integration`, a standard-library Python controller, and a Compose override for a disposable `sanaap-t09-*` project with fresh credentials, random loopback API/MinIO ports, and separate volumes. It rebuilds current images and runs regression tests in a fresh one-off backend container.
- Added six isolation tests, written before the guard implementation. The controller rejects shared/external resources, bind mounts, unsafe port publication, and fixed container names, checks isolation again before cleanup, and verifies all pre-existing volumes survive.
- The host-to-Docker connection reset was caused by Mullvad blocking LAN access. After explicit user permission, only Local network sharing was enabled; the VPN stayed connected. Host Swagger and MinIO health requests returned HTTP 200.
- Real HTTP checks passed for JWT role enforcement, shared metadata, upload/download byte equality, unsigned-object denial, filtering/pagination, and successful replacement/deletion with object cleanup.
- Verified actual signed-URL expiration returns HTTP 403 while the object still exists. Refresh-token logout rejects refresh reuse while the existing access token retains its documented validity.
- Stopped and recovered only the disposable Redis and MinIO services. Reads and updates survived Redis failure without stale metadata. MinIO upload/replacement failures returned safe 503 responses and preserved database state. Post-commit deletion during MinIO outage retained the documented private orphan, which the integration controller subsequently removed.
- Whole-branch independent review found two Important issues: truncated JPEG pixel data passed validation, and initial uploads omitted attempted-key cleanup after a lost acknowledgement. Four new regressions reproduced both issues before implementation. The fixes fully decode images before storage, rewind the upload stream, and attempt UUID-key cleanup while preserving the original storage error. Also removed one trailing-whitespace issue.
- Final clean-stack verification: all **173 tests passed on PostgreSQL**; startup, applied/missing migrations, and warning-free OpenAPI validation passed. Real integration then completed successfully and removed only its own resources, preserving all pre-existing volumes.
- Rebuilt the main backend with the final code and repeated all 173 PostgreSQL tests without skips, migration drift detection, and warning-free schema validation. Host Swagger and MinIO health endpoints both returned HTTP 200.
- README documents the integration command and prerequisites, full image decoding, attempted-key compensation, and remaining timeout/crash/cleanup limitations. The mandatory implementation checklist was updated at the T09 checkpoint; later optional features and the final release have separate review gates.
- The verified T09 checkpoint is commit `c5a8657`. Mandatory work is merged into `develop`; release preparation for `main` and subsequent optional tasks remain in their own checkpoints.

## Completed Review — T08

- The user approved the shared review of T08 and authorized proceeding to T09 after its commit.
- Added public `/api/schema/` and `/api/docs/` routes that remain accessible with invalid Authorization headers. Business endpoints retain JWT and application-role permissions.
- Documented all existing auth, user, and document operations, including binary multipart uploads/replacements, JSON/form title updates, password-free output, server-owned metadata, filters/pagination bounds, error statuses, 60-second downloads, and empty logout/deletion responses.
- Enabled split request/response components and added documentation-only JWT subclasses preserving library behavior. Schema generation does not query user/document data.
- Added 14 schema/Swagger contract tests. All failed with 404 before implementation, then passed locally with database access forbidden.
- Expanded README with initial application-admin bootstrap, Swagger/login/refresh/logout, endpoint/role tables, examples, JPEG/PNG and 10 MiB limits, verification commands, and current storage/cache/URL boundaries.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 163 tests passed on PostgreSQL, with no skipped tests. Schema generation with `--validate --fail-on-warn` passed, migration dry-run reported no changes, and `git diff --check` passed.
- Independent read-only code review found no Critical, Important, or Minor issues and independently passed all 14 schema tests and warning-free CLI validation.
- Live Docker requests to the docs/schema routes returned HTTP 200 internally. The host's published `127.0.0.1:8000` connection resets, including without proxy use. The same-code temporary native preview on port 18000 successfully rendered Swagger in the browser, including CDN assets, Authorize, and auth/document/user groups; its tab and server were then closed.
- Swagger uses the installed integration's CDN assets, so the browser needs internet access. The host-to-Docker connection reset remains a concrete T09 networking preflight issue; no VPN/firewall settings were changed and no cause is claimed yet.
- Real Redis/MinIO/clean-start integration and the documents PR remain T09; audit/deployment/background features remain their existing tasks.
- Verification was repeated successfully before committing T08: all 163 PostgreSQL tests passed.
- Commit: `fbe6b02` — `docs(api): add validated OpenAPI Swagger and usage guide`.

## Completed Review — T07

- The user approved the shared review of T07 and authorized proceeding to T08 after its commit.
- Added a 60-second Redis cache for individual GET/HEAD document metadata. Lists and signed download URLs do not use it, and response fields remain unchanged.
- Authentication, application-role checks, object permissions, and a small database existence/version probe precede cache access. Hits skip the full metadata fetch and serialization; PostgreSQL is still required.
- Added an internal UUID `metadata_version` and migration 0002. Successful metadata/file updates rotate the version inside the transaction. Keys include document id and version, so delayed fills and failed invalidations cannot make an old version current.
- Updates and deletions schedule best-effort invalidation after commit. Rollback preserves the previous version and cache entry. Cache read/write/delete exceptions are redacted and cannot interrupt database-backed reads, committed writes, or file cleanup.
- Added 22 cache tests covering expiry, shared access/JWT ordering, missing rows, commit/rollback timing, equal-timestamp rollback/retry, Redis errors, replacement metadata, isolated entries, and delayed-fill races with update/deletion. Existing storage tests retain state/timing assertions rather than exact callback counts.
- Test-first evidence: the cache tests failed for missing cache behavior before implementation; the local suite then passed with only two PostgreSQL-specific concurrency tests skipped.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 149 tests passed on PostgreSQL, with no skipped tests. Migration dry-run reported no changes, `showmigrations documents` confirmed migration 0002 applied, and `git diff --check` passed.
- Independent read-only code review found no Critical, Important, or Minor issues. The reviewer independently ran the cache tests, checked migration drift, and verified migration of an existing document row preserves its metadata and supplies a UUID version.
- README explains the cache/version contract, database dependency, outage fallback, old-key expiry, and version maintenance for direct database edits outside the service.
- Real deployed Redis outage/MinIO/clean-start integration remains T09; full schema/docs remain T08 and audit remains T10.
- Verification was repeated successfully before committing T07: all 149 PostgreSQL tests passed.
- Commit: `d50da41` — `feat(documents): add resilient versioned metadata cache`.

## Completed Review — T06

- The user approved the shared review of T06 and authorized proceeding to T07 after its commit.
- Added title substring search and exact creator-id filtering to the shared list; filters combine with AND and run before pagination.
- Added page-number pagination with a default of 10 and a maximum of 100 items. The list now returns `count`, `next`, `previous`, and `results`; earlier read tests were updated to consume `results`.
- Preserved descending creation time/id ordering. Navigation links retain search, creator, and page size; an empty first page has zero count and no navigation links.
- Invalid search/creator/page/page-size inputs return field-specific 400 responses; a valid page beyond the result set returns 404. Explicit blank creator values are rejected rather than silently ignored.
- All roles can filter another user's documents. Authentication and role checks precede query validation; filtering leaves the other document endpoints unchanged.
- Added 20 real-database API tests, including combined filters, case-insensitive/literal/Persian search, title-only matching, invalid input, page-size bounds, stable tie ordering, and next/previous navigation.
- Test-first evidence: the new tests failed for the missing pagination/filter behavior before implementation. All 32 query/read tests then passed locally.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 127 tests passed on PostgreSQL, with no skipped tests. Migration dry-run reported no changes; `git diff --check` passed.
- Independent read-only code review found no Critical, Important, or Minor issues and independently passed all 32 query/read tests.
- README documents query parameters, errors, the changed list envelope, navigation, and the possibility of page shifts when data changes between requests.
- Metadata caching remains T07, full OpenAPI/Swagger remains T08, and real stack integration remains T09.
- Verification was repeated successfully before committing T06: all 127 PostgreSQL tests passed.
- Commit: `ad78e36` — `feat(documents): add validated filtering and pagination`.

## Completed Review — T05

- The user approved the shared review of T05 and authorized proceeding to T06 after its commit.
- Added `GET /api/v1/documents/<id>/download/` for all three authenticated roles, including another user's documents. Authentication, role validation, and the database lookup precede URL signing.
- Returns only `download_url` and `expires_in: 60`; the configured `django-minio-backend` signs for the external MinIO endpoint and the private bucket.
- Set the signing expiry to exactly 60 seconds, explicitly disabled backend URL caching, and added HTTP cache-prevention headers to the download endpoint.
- Missing documents return 404. Known signing/document-lookup database failures return safe 503 responses without logging exception details or signed URLs. OPTIONS does not sign; HEAD and write methods are unsupported.
- Added 14 tests using real local MinIO SDK signing, dummy credentials, and an HTTP client that rejects any network call. Covered roles/JWT, expiry/external endpoint, repeated signing without caching, ignored client TTL overrides, and safe error responses.
- Test-first evidence: the new tests failed against the missing route before implementation, then all 14 passed locally.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 107 tests passed on PostgreSQL, with no skipped tests. Migration dry-run reported no changes; `git diff --check` passed.
- Independent read-only code review found no Critical, Important, or Minor issues and independently passed all 14 focused download tests.
- README explains bearer URL lifetime, access tokens after logout, application-role changes, object deletion/replacement, and the distinction between URL issuance and a completed transfer.
- Real MinIO transfer and bucket-policy verification remain T09; metadata caching, schema, and audit remain T07, T08, and T10.
- Verification was repeated successfully before committing T05: all 107 PostgreSQL tests passed.
- Commit: `b821801` — `feat(documents): add private 60-second download URLs`.

## Completed Review — T04

- The user approved the shared review of T04 and authorized proceeding to T05 after its commit.
- Added application-admin-only DELETE with an empty HTTP 204 response. Anonymous/invalid/inactive JWT requests are rejected, and editor/viewer/unknown roles cannot delete.
- The deletion service locks and rereads the current row, deletes it inside an atomic block, and schedules current-object cleanup after commit.
- Missing/repeated deletion returns 404. Database failure returns safe 503 and preserves the row/file, including failure after actual SQL deletion. Outer rollbacks discard storage cleanup.
- A missing storage object does not block metadata deletion. Post-commit storage failure is logged without exposing exception details; the committed deletion remains successful and its orphan is documented in README.
- Added 17 tests, including PostgreSQL deletion during an in-flight replacement and a stale-view interleaving that verifies cleanup of the current storage key.
- Test-first evidence: authorized DELETE failed with 405 before implementation; the new tests then passed locally with only the PostgreSQL concurrency case skipped.
- Final verification: `docker compose exec -T backend python manage.py test --noinput` — all 93 tests passed on PostgreSQL, with no skipped tests.
- Migration dry-run reported no changes; `git diff --check` passed. Independent read-only code review found no Critical, Important, or Minor issues.
- Real MinIO checks, cache invalidation, full schema/docs, and audit records remain assigned to T09, T07, T08, and T10. Automatic orphan reconciliation remains a documented limitation.
- Verification was repeated successfully before committing T04: all 93 PostgreSQL tests passed.
- Commit: `7d811b3` — `feat(documents): add transactional admin-only deletion`.

## Completed Review — T03

- The user approved the shared review of T03 and authorized proceeding to T04 after its commit.
- Added JSON/multipart PATCH and PUT updates. Admins/editors can update shared documents; original creator and server-derived metadata remain protected.
- PATCH requires a title and/or file; PUT requires a title and optionally replaces the file. Empty changes, invalid images, and images over 10 MiB are rejected.
- Replacement uploads use fresh UUID keys. The service locks and rereads the current row before saving, then deletes the previous object only after commit.
- Upload/database failures and a document disappearing during upload preserve the previous reference where it still exists and attempt new-object cleanup.
- Independent review found one Important issue with lost upload acknowledgements. A regression reproduced the orphan, then the fix made it pass by cleaning the attempted UUID key while preserving the original error. No Critical or Minor issues were reported.
- Added 23 update tests, including a PostgreSQL-only concurrent replacement test. The initial update tests failed with 405 before implementation.
- Final verification: `docker compose exec -T backend python manage.py test --noinput` — all 76 tests passed on PostgreSQL, with no skipped tests.
- Migration dry-run reported no changes; `git diff --check` passed.
- README documents crash/cleanup, lost-acknowledgement, backend renaming, and outer-transaction rollback windows. Automatic orphan reconciliation is not implemented.
- Verification was repeated successfully before committing T03: all 76 PostgreSQL tests passed.
- Commit: `9edd930` — `feat(documents): add safe metadata and image updates`.

## Completed Review — T02

- The user approved the shared review of T02 and authorized proceeding to T03 after its commit.
- Added shared metadata reads at `GET /api/v1/documents/` and `GET /api/v1/documents/<id>/` for all three roles, regardless of creator.
- The list orders by descending creation time, then descending id. Missing documents return 404; invalid/anonymous/inactive authentication and unknown roles are rejected.
- GET, HEAD, and OPTIONS are covered. T01 multipart upload behavior remains intact.
- Added 12 read API tests. Before implementation, missing list/detail behavior failed with 405/404; all 26 upload/read tests then passed locally.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 53 tests passed against PostgreSQL.
- Verification: `docker compose exec -T backend python manage.py makemigrations --check --dry-run` — no changes detected. `git diff --check` also passed.
- Independent read-only code review found no Critical, Important, or Minor issues and independently passed the 26 upload/read tests.
- Filtering/pagination (T06), caching (T07), full schema/docs (T08), and real MinIO checks (T09) remain assigned to their existing tasks. T02 introduces no storage I/O on reads.
- Verification was repeated successfully before committing T02: all 53 PostgreSQL tests passed.
- Commit: `5c23230` — `feat(documents): add shared list and detail API`.

## Completed Review — T01

- The user approved T01 and authorized task-scoped commits before proceeding to T02.
- Added the upload route/view, a read-only metadata serializer, and a safe HTTP 503 response for known storage/database outages.
- Extended the existing upload API tests to 14 tests covering roles, JWT, JPEG/PNG content, the 10 MiB boundary, server-owned metadata, and failure handling.
- Before implementation, the existing three upload tests failed because the route returned 404.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 41 tests passed against PostgreSQL.
- Verification: `docker compose exec -T backend python manage.py makemigrations --check --dry-run` — no changes detected.
- Storage is mocked in these tests; real MinIO upload/download and private-bucket checks remain part of T09.
- Verification was repeated successfully before committing T01: all 41 PostgreSQL tests passed.
- Commit: `c6d9b76` — `feat(documents): add authenticated upload API`.

## Acceptance Criteria

### Task 1: T01 — Document upload API

- `POST /api/v1/documents/` accepts multipart `title` and `file`.
- Admins and editors receive HTTP 201 with document metadata; viewers receive 403 and anonymous or invalid-token requests receive 401.
- The creator is always the authenticated user. Client-supplied metadata cannot override server-derived values.
- Validate real JPEG/PNG content and the 10 MiB boundary before calling storage.
- Use the existing creation service and configured `django-minio-backend` storage.
- Return a safe error for known database or storage outages; database failure still attempts upload cleanup.
- Tests cover permissions, JWT authentication, validation, metadata, and failure responses. Run the whole suite against PostgreSQL in Docker.
- Present the implementation and test evidence for shared review. Do not start T02 until reviewed.

### Task 2: T02 — Shared document list and detail API

- All three authenticated roles can list and inspect all documents, including documents created by other users.
- Anonymous requests are rejected. Missing documents return 404.
- Return metadata with a deterministic list order; do not generate or expose download URLs here.
- Exercise the read role/action matrix through the actual API.
- Interfaces: `GET /api/v1/documents/` returns the shared metadata list; `GET /api/v1/documents/<id>/` returns one document using `DocumentSerializer` from T01.
- Order: newest `created_at` first, with descending `id` as the tie-breaker. Pagination/filtering remain in T06.
- Reuse JWT authentication and `DocumentRolePermission`. Support GET, HEAD, and OPTIONS while preserving T01 upload behavior.
- Reject unknown application roles, invalid JWTs, and inactive JWT users; never restrict reads to the creator.
- Files: modify `apps/documents/views.py` and `apps/documents/urls.py`; add `apps/documents/test_read_api.py`.
- Test-first: run the new read tests before implementation; expect missing list/detail behavior to fail with 405/404.
- Verification: run the full PostgreSQL suite and migration check inside Docker; expect a green suite and no missing migrations.
- Complete shared user review before committing T02 and moving to T03.

### Task 3: T03 — Metadata updates and safe image replacement

- Admins and editors can update shared documents; viewers and anonymous requests are rejected.
- Support metadata-only updates and validated file replacement while preserving original creator attribution.
- Upload replacements under new keys before updating the database; delete the old object only after commit.
- Handle concurrent replacement safely. On upload or database failure, keep the previous reference and file and attempt cleanup of the new upload.
- Test role access, invalid input, rollback, cleanup failure, and transaction timing.
- Interfaces: `PATCH /api/v1/documents/<id>/` accepts a title and/or image; `PUT` requires a title and optionally replaces the image. JSON metadata and multipart image updates are supported.
- Reject empty updates and preserve server-owned creator, creation time, object key, and image metadata against client overrides.
- Upload a replacement before locking the row, then fetch its current state with `select_for_update()` inside `transaction.atomic()`; this avoids stale updates and serializes concurrent replacements.
- Schedule best-effort old-object deletion with `transaction.on_commit()`. A cleanup failure must not turn a committed update into an API failure.
- On database failure or disappearance of the document after upload, attempt new-object cleanup and preserve the previous database reference; return safe 503 or 404 responses as appropriate.
- Document unavoidable crash/cleanup and outer-transaction rollback windows. Verify concurrency with a PostgreSQL transaction test.
- Test-first: add HTTP/state-based tests before implementation; expect PATCH/PUT to fail with 405. Run all tests and migration checks in Docker after implementation.
- Complete shared review before committing T03 and starting deletion task T04.

### Task 4: T04 — Document deletion

- Only application admins can delete documents.
- Delete storage objects only after the corresponding database deletion commits.
- Test rollback and storage cleanup failure without claiming that database and MinIO operations share a transaction.
- Record cleanup limitations for the delivery documentation.
- Interface: `DELETE /api/v1/documents/<id>/` returns empty HTTP 204 to an application admin, including for another user's document. Missing/already-deleted documents return 404.
- Lock and reread the current row before deletion so a concurrent replacement's current file is targeted, not a stale key from the view.
- Use the existing best-effort storage cleanup callback. A post-commit storage outage is logged and leaves an orphan, while the successful database deletion still returns 204.
- Database errors return safe HTTP 503 and must preserve the row and file; outer rollbacks discard the cleanup callback.
- Verify unauthorized/JWT access, missing/repeated deletion, failures before and after the row deletion, transaction timing, and replacement/deletion interleavings.
- Test-first: add deletion HTTP/state tests; expect authorized DELETE to fail with 405 before implementation. Run the full PostgreSQL suite, including transaction/concurrency tests, and migration checks.
- Stop for shared review before committing T04 and starting download task T05.

### Task 5: T05 — Private downloads

- All three authenticated roles can request a download URL for any document; check access before generating it.
- URLs expire after exactly 60 seconds, use the client-reachable MinIO endpoint, and are never cached.
- Keep the bucket private. Test unauthorized access, missing documents, and storage errors.
- Document that issued URLs remain usable until expiry, including after logout or role changes.
- Interface: `GET /api/v1/documents/<id>/download/` returns `download_url` and `expires_in: 60` after JWT/role validation and a database lookup.
- Use the configured `django-minio-backend` through `default_storage.url()`, with a 60-second signing TTL and explicit `MINIO_URL_CACHING_ENABLED = False`.
- Preserve the client-reachable external endpoint and private bucket configuration. Return HTTP cache-prevention headers, including `private` and `no-store`.
- Only GET and OPTIONS are supported; HEAD does not issue a URL. Missing documents return 404, and known signing/database failures return safe 503 responses without logging credentials or generated URLs.
- Test real local MinIO SDK signing with dummy credentials and a network-forbidden HTTP client, including expiry, external host, repeated signing, query-parameter TTL overrides, JWT/roles, and failure redaction.
- Document bearer URL lifetime, access tokens after logout, role changes, and object deletion/replacement. URL issuance does not verify that a subsequent file transfer succeeds.
- Run the full PostgreSQL suite and migration check, then stop for shared review before T06. Real MinIO transfer and bucket-policy verification remain T09.

### Task 6: T06 — Querying and pagination

- Add title search, creator filtering, and bounded pagination to the shared list.
- Validate query inputs and maintain deterministic pagination order.
- Test filter combinations, empty results, and page boundaries.
- Interface: `GET /api/v1/documents/?search=<text>&creator=<user-id>&page=1&page_size=10` returns `count`, `next`, `previous`, and `results`.
- Search is a trimmed, case-insensitive literal substring of title only, up to 255 characters. Blank search applies no title restriction; search and creator filters combine with AND.
- Creator is a positive signed-64-bit integer identifier; an unknown valid identifier produces an empty page rather than 404. The filter never changes shared access permissions.
- Pagination defaults to 10 items and allows explicit sizes from 1 through 100. Page numbers must be positive integers. Invalid inputs return field-specific 400 responses; a valid page beyond the result set returns 404. An empty result has a valid first page.
- Apply filtering before pagination and retain descending creation time/id ordering. Page links preserve query parameters. Detail, upload, update, deletion, and download contracts remain unchanged.
- Test-first: exercise the actual list API with real database rows, including role access, filter combinations, literal/Unicode search, invalid inputs, deterministic pages, page-size limits, and navigation links. Update earlier read tests for the approved paginated response.
- Run the full PostgreSQL suite and migration check; stop for shared review before committing T06 or starting caching task T07.

### Task 7: T07 — Metadata caching

- Cache individual document metadata for 60 seconds; check permissions before cache access.
- Invalidate affected entries after successful updates or deletions.
- Redis read, write, or invalidation failures must not prevent normal database-backed reads.
- Never cache presigned URLs. Test invalidation and stale-data/concurrent-update cases.
- Cache only the individual detail GET/HEAD metadata payload, with an explicit 60-second TTL. Lists and download responses do not use this cache.
- Authentication, application-role checks, object permissions, and a database existence/version probe precede cache access. A hit skips the full metadata fetch and serialization, while PostgreSQL remains required for each detail read.
- Add an internal UUID metadata version and include it in cache keys. Rotate it with each successful metadata/image update; do not expose it in API responses. A stale in-flight reader can populate only its old version's key, which later reads cannot use.
- Schedule best-effort old-version invalidation after update/deletion commits. Rollback discards invalidation; failed Redis deletion cannot make old metadata current again. Old entries expire within 60 seconds.
- Cache get/set/delete failures fall back safely without exposing backend exception details or turning committed writes into failures. Missing documents always return 404 even if old cache entries remain.
- Verify cache hits/expiry, roles/JWT before cache access, post-commit timing, rollback, Redis read/write/invalidation errors, replacement metadata, and delayed cache-fill races with update/deletion. Use real in-memory caching for deterministic tests and PostgreSQL for concurrent interleavings; deployed Redis failure checks remain T09.
- Generate and verify the metadata-version migration, run the full PostgreSQL suite, and stop for shared review before committing T07 or starting T08.

### Task 8: T08 — Schema and documentation

- Provide OpenAPI and Swagger routes describing actual request, response, permission, and error behavior.
- Document setup, first application-admin creation, endpoints, role behavior, and file limits.
- Explain independent database/storage failure windows, failed cleanup, and presigned URL behavior in README.
- Validate the generated schema and check for missing migrations.
- Publish anonymous schema and Swagger routes at `/api/schema/` and `/api/docs/`; stale/invalid Authorization headers must not prevent viewing documentation. Keep the business API's JWT/role rules unchanged.
- Describe all existing authentication, user-management, and document operations. Split request/response components so passwords are absent from responses and image uploads use binary multipart fields.
- Document metadata-only JSON/form updates, multipart replacements, query bounds, paginated lists, exact 60-second download responses, empty logout/deletion responses, and known error statuses. Use the installed drf-spectacular integration and validate without schema-generation warnings.
- Expand README with first application-admin setup, token/Swagger usage, endpoint/role tables, file limits, examples, and current failure/consistency boundaries. Keep optional features in their assigned tasks.
- Test the published schema/Swagger boundaries and generated client-facing contracts before implementation, then run the full PostgreSQL suite, schema validation, and migration check. Stop for shared review before committing T08 or starting T09.

### Task 9: T09 — Mandatory integration and documents PR

- Resolve the observed host-to-Docker `127.0.0.1:8000` connection reset before claiming external reachability. Internal docs/schema HTTP 200 and a successful native browser preview isolate this as an integration/networking preflight; inspect the cause without changing VPN/firewall settings without authorization.
- Run the complete suite against PostgreSQL and verify real API upload and download against MinIO.
- Verify startup, migrations, and private bucket initialization with an isolated clean Compose project, preserving existing volumes.
- Check Redis failure behavior and storage failure handling.
- Provide `make integration`: an automatically named disposable Compose project with fresh credentials, random loopback-only API/MinIO ports, new project-owned volumes, and guarded cleanup preserving all pre-existing volumes. Verify actual HTTP/JWT/API behavior, binary transfers, private unsigned-object denial, real 60-second expiry, replacement/deletion cleanup, Redis outage/recovery, and MinIO outage/recovery. Stop only services in that disposable project.
- Verify integration isolation guards before running Docker mutations. Full regression also runs in a fresh one-off backend container; validate schema and migrations on the clean stack.
- Review the completed mandatory checklist and create the documents PR into `develop`.
- Complete the shared review and merge checkpoint before starting optional feature branches.

### Task 10: T10 — Audit logging

- Record document creation, updates, deletion, and download URL issuance with actor, document identifier, action, timestamp, and outcome.
- Cover successful and failed operations without logging passwords, tokens, or presigned URLs.
- Preserve audit information after document deletion. Issuing a URL is not evidence of a completed download.
- Test audit records and complete a dedicated feature PR/review.
- Implement this approved scope in `apps/documents`: `DocumentAuditEvent` stores nullable actor/document id snapshots, an allowlisted action (`create`, `update`, `delete`, `download_url`), UTC `occurred_at`, `success`/`failure`, and HTTP status. No foreign-key cascade, body, headers, URL, filename, object key, or exception text is stored.
- Successful mutation events share the service transaction and roll back with it. An audit insertion failure must roll back the mutation and preserve existing storage compensation. Download issuance records success before returning the URL; recording failure returns the existing safe 503.
- An API mixin records failed attempts, including authentication, permission, validation, missing-document, storage/database, and unexpected errors. Failure logging uses a savepoint and redacted best-effort fallback so it cannot replace the original response. Unauthenticated actors and out-of-range route identifiers are stored as null, never forged request identities.
- Routine list/detail/HEAD/OPTIONS reads and unsupported methods do not create operation events. A deletion that committed but could not clean storage still records the successful metadata deletion; cleanup errors retain their existing operator logs.
- Interfaces: `record_document_event(*, actor_id, action, document_id, outcome, status_code)`; `DocumentAuditMixin.audit_actions` maps supported HTTP methods to actions; update/delete/signing services accept optional `actor_id=None`, with API views passing the authenticated user's id. Creation uses its existing `creator` argument.
- Files: model and migration in `models.py`/`migrations/0003_*`; event writer and exception mixin in `audit.py`; service/view integration in `services.py`/`views.py`; contract/transaction tests in `test_audit_api.py`; real audit assertions in `scripts/integration_check.py`; README inspection instructions and limitations.
- [x] Write and run failing database-backed API/transaction regressions: correct actor/action/id/time/status; denial/invalid/missing/failure attempts; retained identifiers after deletion; no duplicate events or secret leakage; no success after rollback; audit failure compensation; safe failure-audit fallback; unbounded route ids.
- [x] Implement the smallest model/writer/mixin/service changes and watch those tests pass.
- [x] Run the full PostgreSQL suite, migration drift/OpenAPI checks, and disposable real-service integration with durable audit assertions.
- [x] Obtain one independent review of the complete T10 branch; resolve Important/Critical findings in one test-backed pass and record any remaining limitations.
- [x] Commit/push the verified review checkpoint, open a draft PR into `develop`, and hold before merge/T11 for shared user review. Completed through PR #4, subsequently approved and merged by the user.

### Task 11: T11 — Nginx deployment

- Keep Gunicorn as the application server and add Nginx as the reverse proxy.
- Support static assets, multipart upload limits including request overhead, and clear production/HTTPS configuration instructions.
- Verify proxy routing, Swagger assets, errors, and document uploads.
- Complete a dedicated feature PR/review.
- Serve the existing public API port 8000 through a non-root Nginx container listening internally on 8080. Gunicorn keeps its current internal 8000 listener without publishing a host port. MinIO's external signing endpoint stays separately configured.
- Collect static assets at backend startup into a project-owned `static_data` volume; Nginx mounts it read-only. Use a pinned official Nginx image and copied configuration, not host configuration bind mounts.
- Use an 11 MiB request limit to accept 10 MiB files plus multipart overhead. Verify exact-limit success, API rejection at 10 MiB + 1, and Nginx 413 beyond its total-body limit. Nginx responses before the API boundary do not create audit events.
- Preserve original Host including its public port, paths/query parameters, JWT headers, JSON errors and authentication/no-store headers. Overwrite forwarded scheme/client headers, trust the scheme only in the isolated Compose backend, and never cache API responses or automatically retry mutations. Resolve Docker backend addresses dynamically for recovery after recreation.
- Add static/health/routing/host-spoof/large-upload/error/recovery checks to `scripts/integration_check.py` and focused `scripts/proxy_checks.py`, preserving exact audit assertions and disposable resource safeguards. Accept gateway 502/504 for unavailable/timed-out upstreams. Adapt isolation tests to five services, shared project-owned static volume, no backend publishing and read-only proxy mounts.
- Keep local HTTP startup convenient. Document concrete TLS server and Compose override examples using operator-supplied certificates, production host/debug/cookie/redirect settings, and a separately reachable HTTPS MinIO endpoint. No public deployment, certificate issuance or VPN/security setting changes are part of T11.
- Files: new `docker/nginx/Dockerfile`, `nginx.conf`, `default.conf`, `proxy-locations.conf`, `production.conf.example`, and `compose.production.yaml.example`; update `Dockerfile`, backend entrypoint, settings, Compose and integration override, isolation/forwarding tests, integration controller, Makefile, README and checklists.
- [x] Write and run failing proxy HTTP/isolation/forwarding regressions before implementation.
- [x] Implement proxy/static/header/settings changes and pass the focused regressions.
- [x] Run full PostgreSQL, migration/schema checks and the real disposable integration through Nginx, including large uploads, static bytes, backend outage/recreation and audit persistence.
- [x] Obtain one independent whole-T11 review and resolve Important/Critical findings in one test-backed pass. No blocking findings; two Minor coverage suggestions are recorded above.
- [x] Commit/push a verified draft PR into `develop`; hold before merge/T12 for shared user review. PR #5 was approved and merged.

### Task 12: T12 — Background storage

- Review the transition from synchronous HTTP 201 creation to HTTP 202 with PENDING/READY/FAILED states before changing the API contract.
- Use Celery and Redis with a shared staging volume accessible to backend and worker.
- Dispatch work after database commit; use bounded transient retries, idempotent tasks, and staged-file cleanup.
- Only READY documents can be downloaded. Handle worker restart, exhausted retries, and dispatch/storage failures.
- Update schema, deployment, audit integration, and tests; complete a dedicated feature PR/review.
- Apply the approved asynchronous contract to HTTP creation only: return 202/PENDING, expose read-only `status`, and retain synchronous READY-only updates/replacements. Existing rows migrate as READY. Pending/failed metadata remains readable; downloads and updates of non-READY rows return 409. Admin deletion is allowed in every state.
- Stage validated bytes under a server-generated name in a private shared volume. Create the PENDING row and acceptance audit (create/success/202) atomically; publish only after commit. A publish failure marks the committed row FAILED with a store/failure/503 event and cleans staging; the accepted response stays 202 and clients poll detail. Validation/staging/database failures before acceptance retain 400/503 with no document.
- Worker messages contain only the document id. Serialize worker/update/delete with a row lock, use stable private object keys and persistent bounded attempts (four total attempts), and handle redelivery after worker loss idempotently. READY/FAILED/deleted rows cannot be revived or uploaded again. A store/success/200 event shares the READY transition; a store/failure/503 event shares terminal failure. Rotate metadata versions and invalidate after commit on every state transition.
- Use Celery with a separate Redis broker DB, JSON serialization, late acknowledgement, rejection on worker loss, prefetch 1, no result backend and bounded network operations. Persist Redis broker data with AOF and a project-owned volume. Worker shares only staging, uses the backend image and waits for healthy/migrated backend; it does not run startup migrations/collectstatic itself.
- Staging cleanup is after terminal commit/deletion and before creation failure returns. Document crash windows (staging outside DB transactions, commit-to-publish gap, broker data loss, cleanup failure) and an explicit operator command to republish PENDING rows after recovery; do not claim an outbox or exactly-once transport.
- [x] Write/run failing acceptance/status/gating regressions; implement staging and the public 202 contract.
- [x] Write/run worker retry/redelivery/audit/cache/cleanup and real PostgreSQL race regressions; implement bounded idempotent processing and recovery.
- [x] Update Compose/dependencies/schema/README and disposable integration for a real worker, worker restart, broker/storage outages and durable audit assertions.
- [x] Run the full PostgreSQL suite, migration/schema checks and clean real-service integration; obtain one independent whole-feature review and resolve blocking findings. Final 235-test/37-event integration passed; the Important compensation race was fixed with a failing regression first.
- [ ] Commit/push a verified draft PR into `develop`; hold before merge/T13 for shared user review.

### Task 13: T13 — WebSocket notifications

- Use Channels and Redis; review JWT authentication and connection handling before implementation.
- Notify authenticated users when a document becomes READY or an update commits successfully.
- Emit events after commit and exclude credentials and presigned URLs from payloads.
- Test authentication, delivery, rollback behavior, and integration with Celery/Nginx; complete a dedicated feature PR/review.

### Task 14: T14 — Release

- Run regression and real integration checks for mandatory and optional functionality.
- Verify clean startup of the full stack, migrations, docs, and failure handling.
- Review the release diff and agreed version before the Git Flow release merges and tag.
- Mark tasks reviewed only when their required implementation, evidence, and user review are complete.
