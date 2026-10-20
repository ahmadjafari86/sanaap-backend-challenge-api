# Document Management API Implementation Plan

**Goal:** Deliver the mandatory assessment requirements before optional features.
**Spec:** docs/design.md
**Execution:** Guided implementation in the current conversation.

## 1. Project Setup
- [ ] Add .gitignore, dependency files, .env.example, and .dockerignore.
- [ ] Create Django project config and apps accounts and documents.
- [ ] Configure PostgreSQL, Redis, and private MinIO storage.
- [ ] Add Dockerfile and Docker Compose with persistent volumes.
- [ ] Verify: docker compose config
- [ ] Verify: docker compose run --rm backend python manage.py check
- [ ] Commit the working setup.

## 2. Users, Roles, and JWT
- [ ] Define the custom user model before the first migration.
- [ ] Add admin, editor, and viewer roles; default to viewer.
- [ ] Test login, refresh, refresh-token logout, and anonymous rejection.
- [ ] Test that only admins can create users and change roles.
- [ ] Implement Simple JWT and admin-only user management endpoints.
- [ ] Keep application roles separate from staff and superuser flags.
- [ ] Verify tests and commit.

## 3. Document Operations
- [ ] Add document metadata model and migrations.
- [ ] Test the complete role/action permission matrix.
- [ ] Test rejection of invalid images and files over 10 MiB.
- [ ] Implement shared document CRUD for JPEG and PNG images.
- [ ] Use unique private object keys and presigned URLs valid for 60 seconds.
- [ ] Test database and storage failures during upload and replacement.
- [ ] Preserve the previous file when replacement fails.
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
- [ ] Gunicorn application server and Nginx reverse proxy.
- [ ] Celery storage tasks with shared staging, bounded retries,
      idempotency, and PENDING/READY/FAILED states.
- [ ] Authenticated Channels notifications after successful commits.
- [ ] Test optional features before including them in the release.
