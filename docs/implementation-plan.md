# Document Management API Implementation Plan

**Goal:** Deliver the mandatory assessment requirements before optional features.
**Spec:** docs/design.md
**Execution:** Agent implementation, one task at a time, followed by shared review before starting the next task.
**Task queue:** [tasks.md](tasks.md) defines the remaining mandatory and optional work, acceptance criteria, and review checkpoints.

## Execution and Git Flow

- Continue document work on `feature/documents`, created from the updated `develop` branch.
- Complete and verify one task, then present its changes and evidence for shared review.
- Start the next task only after the current task has been reviewed with the user.
- Keep commits scoped to a reviewed task; merge feature branches into `develop` through pull requests.
- Start subsequent independent feature branches from updated `develop` after their prerequisites are merged.
- Finish through a release branch, merge into `main` and back into `develop`, and tag the agreed release version.
- Complete mandatory work and its delivery checks before optional features; optional features remain in the task queue.

## 1. Project Setup
- [x] Add .gitignore, dependency files, .env.example, and .dockerignore.
- [x] Create Django project config and apps accounts and documents.
- [x] Configure PostgreSQL, Redis, and private MinIO storage.
- [x] Add Dockerfile and Docker Compose with persistent volumes.
- [x] Verify: docker compose config (runs through `make up`).
- [x] Verify Django system checks during backend startup and test runs.
- [x] Commit the working setup.

## 2. Users, Roles, and JWT
- [x] Define the custom user model before the first migration.
- [x] Add admin, editor, and viewer roles; default to viewer.
- [x] Test login, refresh, refresh-token logout, and anonymous rejection.
- [x] Test admin user creation and role changes, and editor/viewer creation denial.
- [x] Test that anonymous users cannot create users or change roles, and editors/viewers cannot change roles.
- [x] Implement Simple JWT and admin-only user management endpoints.
- [x] Keep application roles separate from staff and superuser flags.
- [x] Verify tests and commit.

## 3. Document Operations
- [x] Add document metadata model and migrations.
- [x] Implement and unit-test the role/method permission matrix, including anonymous and unknown-role rejection.
- [x] Test the complete role/action matrix through document API endpoints, including downloads (T05 reviewed by user).
- [x] Test rejection of invalid images and files over 10 MiB, including the exact size boundary.
- [x] Implement document creation through the configured MinIO storage backend.
- [x] Implement and test the upload API, including JWT, role access, validation, metadata, and safe failure responses (T01; reviewed by user).
- [x] Implement and test shared document list/detail reads, deterministic ordering, JWT/role access, and HEAD/OPTIONS (T02; reviewed by user).
- [x] Implement and test metadata updates and validated image replacement via PATCH/PUT, including concurrent replacements and after-commit cleanup (T03; reviewed by user).
- [x] Implement and test admin-only deletion, rollback, after-commit cleanup, and replacement/deletion races (T04; reviewed by user).
- [x] Implement shared document CRUD for JPEG and PNG images (T01–T04; reviewed by user).
- [x] Generate UUID-based object keys for document uploads.
- [x] Issue private presigned download URLs valid for 60 seconds (T05 reviewed by user).
- [x] Test upload, database, and cleanup failures during document creation.
- [x] Test database and storage failures during document replacement, including lost upload acknowledgements and cleanup failure.
- [x] Preserve the previous file when replacement fails.
- [x] Verify the document foundation (27 project tests passed).
- [x] Verify tests and commit (T01–T04 reviewed by user).

## 4. Querying, Caching, and API Documentation
- [x] Add title search, creator filtering, and pagination (T06 reviewed by user).
- [x] Test filtering and pagination, including combinations, validation, size limits, and page navigation (T06 reviewed by user).
- [x] Cache individual document metadata for 60 seconds with database-backed version checks (T07 reviewed by user).
- [x] Test after-commit invalidation, rollback, and concurrent cache-fill races with updates/deletion (T07 reviewed by user).
- [x] Test database fallback for cache read/write/invalidation errors (T07 reviewed by user; deployed Redis outage check remains T09).
- [x] Never cache presigned URLs in the storage backend or HTTP responses (T05 reviewed by user).
- [x] Check authentication, role/object permissions, and document existence/version before metadata cache access (T07 reviewed by user).
- [x] Add OpenAPI schema and Swagger UI with validated request/response contracts (T08 reviewed; T15 restricts HTTP documentation to valid Django admin sessions).
- [x] Verify tests and commit (T06–T08 reviewed by user).

## 5. Mandatory Delivery
- [x] Run the full PostgreSQL suite in a fresh one-off backend container through `make integration`.
- [x] Check for missing migrations and validate OpenAPI without warnings.
- [x] Verify startup, migrations, and private bucket initialization using a fresh Compose project.
- [x] Verify a real upload and download through MinIO, matching exact file bytes.
- [x] Verify unsigned-object denial, actual 60-second URL expiry, and replacement/deletion cleanup.
- [x] Verify real Redis and MinIO outage/recovery behavior in the disposable stack.
- [x] Add isolation guards and verify cleanup preserves all pre-existing volumes.
- [x] Document setup, application-admin creation, auth/roles, endpoints, examples, file limits, assumptions, and limitations (T08 reviewed by user).
- [x] Document presigned URL behavior after logout or role changes (T05 reviewed by user).
- [x] Merge mandatory document work into develop through PR #3 (T09 reviewed by user).
- [x] Prepare main for delivery through the final release checkpoint (v0.1.0 reviewed and merged through PR #8, annotated tag verified, main merged back into develop through PR #9).

## 6. Optional Features
- [x] Audit log: actor, action, document identifier, time, and outcome (T10 reviewed by user and merged into develop through PR #4).
- [x] Configure Gunicorn as the application server.
- [x] Add Nginx as the reverse proxy, serve collected static assets, verify upload/proxy boundaries, and document HTTPS configuration (T11 reviewed by user and merged through PR #5).
- [x] Celery storage tasks with shared staging, bounded retries,
      idempotency, and PENDING/READY/FAILED states (T12 reviewed by user and merged through PR #6).
- [x] Authenticated Channels notifications after successful commits (T13 reviewed by user and merged through PR #7; 263 PostgreSQL tests and real Redis/Nginx delivery verified).
- [x] Test optional features (T10–T13 regression/integration gates passed; the final release checkpoint remains T14).

## 7. Post-release Administration Workflow (T15)
- [x] Add read-only document metadata, search and filters to Django admin.
- [x] Move tests into per-application packages, retaining every original test.
- [x] Require valid active-staff admin sessions for Swagger and the HTTP schema.
- [x] Create Django and application administrators together with `make superuser`.
- [x] Verify continuous `make logs`, optional service selection and MinIO login instructions.
- [x] Pass 279 PostgreSQL tests, independent review and disposable real-service integration.
