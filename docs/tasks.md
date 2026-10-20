# Remaining Implementation Tasks

**Source of requirements:** [design.md](design.md).
**Progress checklist:** [implementation-plan.md](implementation-plan.md).
**Execution:** Implement one task, verify it, and review it with the user before starting the next task. A passing test run does not replace shared review.

## Git Flow

- T01–T09 continue on `feature/documents`, based on `develop` after the accounts PR was merged.
- Keep task changes reviewable and commit them after shared review.
- T09 prepares the documents pull request into `develop`.
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
| T09 | Mandatory integration checks and documents PR | Mandatory | Pending |
| T10 | Audit logging | Optional, included in scope | Pending |
| T11 | Nginx deployment with Gunicorn | Optional, included in scope | Pending |
| T12 | Celery background document storage | Optional, included in scope | Pending |
| T13 | Authenticated WebSocket notifications | Optional, included in scope | Pending |
| T14 | Final regression checks and Git Flow release | Delivery | Pending |

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
- Review the completed mandatory checklist and create the documents PR into `develop`.
- Complete the shared review and merge checkpoint before starting optional feature branches.

### Task 10: T10 — Audit logging

- Record document creation, updates, deletion, and download URL issuance with actor, document identifier, action, timestamp, and outcome.
- Cover successful and failed operations without logging passwords, tokens, or presigned URLs.
- Preserve audit information after document deletion. Issuing a URL is not evidence of a completed download.
- Test audit records and complete a dedicated feature PR/review.

### Task 11: T11 — Nginx deployment

- Keep Gunicorn as the application server and add Nginx as the reverse proxy.
- Support static assets, multipart upload limits including request overhead, and clear production/HTTPS configuration instructions.
- Verify proxy routing, Swagger assets, errors, and document uploads.
- Complete a dedicated feature PR/review.

### Task 12: T12 — Background storage

- Review the transition from synchronous HTTP 201 creation to HTTP 202 with PENDING/READY/FAILED states before changing the API contract.
- Use Celery and Redis with a shared staging volume accessible to backend and worker.
- Dispatch work after database commit; use bounded transient retries, idempotent tasks, and staged-file cleanup.
- Only READY documents can be downloaded. Handle worker restart, exhausted retries, and dispatch/storage failures.
- Update schema, deployment, audit integration, and tests; complete a dedicated feature PR/review.

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
