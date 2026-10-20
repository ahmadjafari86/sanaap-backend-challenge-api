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
| T02 | Shared document list and detail API | Mandatory | Pending |
| T03 | Metadata updates and safe image replacement | Mandatory | Pending |
| T04 | Document deletion after successful database commit | Mandatory | Pending |
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

## Completed Review — T01

- The user approved T01 and authorized task-scoped commits before proceeding to T02.
- Added the upload route/view, a read-only metadata serializer, and a safe HTTP 503 response for known storage/database outages.
- Extended the existing upload API tests to 14 tests covering roles, JWT, JPEG/PNG content, the 10 MiB boundary, server-owned metadata, and failure handling.
- Before implementation, the existing three upload tests failed because the route returned 404.
- Verification: `docker compose exec -T backend python manage.py test --noinput` — all 41 tests passed against PostgreSQL.
- Verification: `docker compose exec -T backend python manage.py makemigrations --check --dry-run` — no changes detected.
- Storage is mocked in these tests; real MinIO upload/download and private-bucket checks remain part of T09.
- Verification was repeated successfully before committing T01: all 41 PostgreSQL tests passed.

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

### Task 3: T03 — Metadata updates and safe image replacement

- Admins and editors can update shared documents; viewers and anonymous requests are rejected.
- Support metadata-only updates and validated file replacement while preserving original creator attribution.
- Upload replacements under new keys before updating the database; delete the old object only after commit.
- Handle concurrent replacement safely. On upload or database failure, keep the previous reference and file and attempt cleanup of the new upload.
- Test role access, invalid input, rollback, cleanup failure, and transaction timing.

### Task 4: T04 — Document deletion

- Only application admins can delete documents.
- Delete storage objects only after the corresponding database deletion commits.
- Test rollback and storage cleanup failure without claiming that database and MinIO operations share a transaction.
- Record cleanup limitations for the delivery documentation.

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
