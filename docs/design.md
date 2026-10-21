# Document Management API — Design

## Scope
A Django REST Framework API for managing shared image documents.
All authenticated users can list, inspect, and download all documents.
Ownership is recorded for attribution, not access restriction.

## Roles
- admin: manage users, assign roles, and perform all document operations.
- editor: list, inspect, download, upload, and update documents; cannot delete.
- viewer: list, inspect, and download documents only.
- Anonymous requests cannot access users or documents.

Application roles are separate from Django staff and superuser flags.
New users are created by an admin; public registration is unavailable.
`make superuser` creates the initial Django superuser with application role admin
in one step. Ordinary user creation retains the viewer role default; existing
accounts are not promoted automatically.

## Authentication
Use Django password authentication with Simple JWT.
Provide token obtain, refresh, and logout endpoints.
Logout blacklists the submitted refresh token.
Existing access tokens remain valid until expiration.
Production deployment requires HTTPS.
Swagger and the HTTP OpenAPI schema require an active staff user's valid Django
admin session. Unauthenticated documentation requests redirect to admin login;
business endpoints continue to require JWT authentication and application roles.

## Components
- accounts: custom user model, roles, authentication, and user management.
- documents: metadata, validation, permissions, and document operations.
- PostgreSQL: users, authentication tokens, and document metadata.
- MinIO: private image storage with unique object keys.
- Redis: document metadata caching.
- Swagger UI: interactive API documentation.

## Documents
Store title, original filename, object key, content type, file size,
creator, creation time, and update time.
Accept JPEG and PNG images up to 10 MiB.
Validate image contents rather than trusting filename or declared MIME type.
Use filtering and pagination on the document list.

## Downloads
Check authentication and role before issuing a presigned download URL.
URLs expire after 60 seconds and act as bearer credentials until expiration.
Generate URLs on demand; do not cache them.
The MinIO bucket must not allow public access.

## Storage Consistency
Database transactions do not include MinIO operations.
On replacement, upload under a new key before updating the database.
Delete the previous object only after a successful database commit.
Attempt cleanup if a new upload cannot be recorded in the database.
Document remaining failure windows and cleanup limitations in the README.
Expose document metadata in Django admin for read-only browsing/search/filtering.
All document mutations use the API services that maintain storage/cache/audit state.

## Caching
Cache individual document metadata for 60 seconds.
Invalidate affected entries after successful update or deletion.
Check permissions before accessing cached metadata.
Redis failures must not prevent normal database-backed reads.

## Delivery
Provide Docker Compose services for backend, PostgreSQL, Redis, and MinIO.
Include migrations, .env.example, Swagger, tests, and setup instructions.
Never commit real credentials.

## Validation
Test the role/action matrix, anonymous access, login/logout,
image validation, filtering, pagination, and storage failure handling.
Verify a real upload and download against MinIO.
Verify startup and migrations from a clean Docker Compose environment.

## Optional Features

### Audit Logging
Record document creation, updates, deletion, and download URL issuance.
Each record includes actor, document identifier, action, timestamp,
and outcome. Never log passwords, tokens, or presigned URLs.
Issuing a download URL does not prove that the file was downloaded.

### Background Document Storage
Use Celery with Redis as the broker.
Stage uploaded files in a shared Docker volume accessible to the worker.
Create a document with PENDING status and return HTTP 202.
The worker uploads the staged file to MinIO and marks it READY.
Use bounded retries for transient failures and mark exhausted jobs FAILED.
Make task retries idempotent and clean up staged files after processing.
Only READY documents can be downloaded.

### Deployment
Run Django through Gunicorn.
Use Nginx as the reverse proxy.
Gunicorn is an application server, not a reverse proxy.

### WebSocket Notifications
Use Django Channels with Redis as the channel layer.
Notify authenticated users when a document becomes READY
or an update completes successfully.
Send notifications only after the corresponding database commit.
Do not include credentials or presigned download URLs in messages.

## Implementation Priority
1. Complete and test all mandatory requirements.
2. Add audit logging and deployment configuration.
3. Add background storage and WebSocket notifications if time permits.
