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
- [ ] Test the complete role/action matrix through document API endpoints, including downloads.
- [x] Test rejection of invalid images and files over 10 MiB, including the exact size boundary.
- [x] Implement document creation through the configured MinIO storage backend.
- [x] Implement and test the upload API, including JWT, role access, validation, metadata, and safe failure responses (T01; reviewed by user).
- [x] Implement and test shared document list/detail reads, deterministic ordering, JWT/role access, and HEAD/OPTIONS (T02; reviewed by user).
- [x] Implement and test metadata updates and validated image replacement via PATCH/PUT, including concurrent replacements and after-commit cleanup (T03; reviewed by user).
- [ ] Implement shared document CRUD for JPEG and PNG images.
- [x] Generate UUID-based object keys for document uploads.
- [ ] Issue private presigned download URLs valid for 60 seconds.
- [x] Test upload, database, and cleanup failures during document creation.
- [x] Test database and storage failures during document replacement, including lost upload acknowledgements and cleanup failure.
- [x] Preserve the previous file when replacement fails.
- [x] Verify the document foundation (27 project tests passed).
- [ ] Verify tests and commit.

## 4. Querying, Caching, and API Documentation
- [ ] Add title search, creator filtering, and pagination.
- [ ] Test filtering and pagination.
- [ ] Cache document metadata for 60 seconds.
- [ ] Test invalidation after update and deletion.
- [ ] Test database fallback when Redis is unavailable.
- [ ] Never cache presigned URLs; check permissions before cache access.
- [ ] Add OpenAPI schema and Swagger UI.
- [ ] Verify tests and commit.

## 5. Mandatory Delivery
- [ ] Run: docker compose run --rm backend python manage.py test
- [ ] Check for missing migrations.
- [ ] Verify startup and migrations using a fresh Compose project.
- [ ] Verify a real upload and download through MinIO.
- [ ] Document setup, admin creation, endpoints, assumptions, and limitations.
- [ ] Document presigned URL behavior after logout or role changes.
- [ ] Merge through develop and prepare the main branch for delivery.

## 6. Optional Features
- [ ] Audit log: actor, action, document identifier, time, and outcome.
- [x] Configure Gunicorn as the application server.
- [ ] Add Nginx as the reverse proxy.
- [ ] Celery storage tasks with shared staging, bounded retries,
      idempotency, and PENDING/READY/FAILED states.
- [ ] Authenticated Channels notifications after successful commits.
- [ ] Test optional features before including them in the release.
