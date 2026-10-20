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
| T05 | Private download URLs valid for 60 seconds | Mandatory | Pending |
| T06 | Title search, creator filtering, and pagination | Mandatory | Pending |
| T07 | Resilient document metadata caching | Mandatory | Pending |
| T08 | OpenAPI, Swagger, and user documentation | Mandatory | Pending |
| T09 | Mandatory integration checks and documents PR | Mandatory | Pending |
| T10 | Audit logging | Optional, included in scope | Pending |
| T11 | Nginx deployment with Gunicorn | Optional, included in scope | Pending |
| T12 | Celery background document storage | Optional, included in scope | Pending |
| T13 | Authenticated WebSocket notifications | Optional, included in scope | Pending |
| T14 | Final regression checks and Git Flow release | Delivery | Pending |

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

### Task 6: T06 — Querying and pagination

- Add title search, creator filtering, and bounded pagination to the shared list.
- Validate query inputs and maintain deterministic pagination order.
- Test filter combinations, empty results, and page boundaries.

### Task 7: T07 — Metadata caching

- Cache individual document metadata for 60 seconds; check permissions before cache access.
- Invalidate affected entries after successful updates or deletions.
- Redis read, write, or invalidation failures must not prevent normal database-backed reads.
- Never cache presigned URLs. Test invalidation and stale-data/concurrent-update cases.

### Task 8: T08 — Schema and documentation

- Provide OpenAPI and Swagger routes describing actual request, response, permission, and error behavior.
- Document setup, first application-admin creation, endpoints, role behavior, and file limits.
- Explain independent database/storage failure windows, failed cleanup, and presigned URL behavior in README.
- Validate the generated schema and check for missing migrations.

### Task 9: T09 — Mandatory integration and documents PR

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
