# Changelog

## 0.1.0

- Shared JPEG/PNG documents with title search, creator filtering and pagination.
- Admin, editor and viewer application roles with access/refresh JWT authentication,
  refresh-token logout and admin-only user management.
- Content-validated images up to 10 MiB, private MinIO objects and on-demand
  60-second download URLs.
- Version-checked metadata caching with database fallback during Redis outages.
- Durable document-operation audit rows without credentials or download URLs.
- Asynchronous initial uploads: HTTP 202/PENDING, bounded Celery attempts,
  READY/FAILED states, private staging and explicit pending-job recovery.
- Authenticated WebSocket READY/update hints after database commit, with Origin
  admission, active-role checks and expiry-based connection closure.
- Non-root Nginx and Gunicorn ASGI deployment, public static assets, controlled
  proxy headers, upload limits and operator-applied HTTPS examples.
- OpenAPI/Swagger, setup and operational documentation, 263 regression tests and
  disposable real-service integration with private-volume cleanup checks.

### Compatibility and operational notes

- All authenticated roles share document visibility. Ownership is attribution;
  application roles are separate from Django staff/superuser flags.
- Creation returns 202 before storage completion; clients wait for READY before
  download or replacement. Non-READY download/update requests return 409.
- Logout revokes the submitted refresh token. Access tokens remain valid until
  expiry; already-issued object URLs remain bearer credentials for their lifetime.
- Storage and database operations are not a distributed transaction. Crash and
  cleanup/publication failures may require operator recovery or orphan cleanup.
  Retries count committed failures; no outbox or exactly-once guarantee is made.
- WebSocket hints have no durable replay/order guarantee; clients reconcile with
  REST after reconnect and handle documents changed or deleted since the hint.
- Production TLS/certificates, capacity validation, connection-rate quotas and
  automated retention/reconciliation remain deployment responsibilities.

See [README](README.md) for the exact API/protocol contract and
[release checklist](docs/release-checklist.md) for delivery evidence and review gates.
