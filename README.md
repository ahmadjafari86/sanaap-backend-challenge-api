# Sanaap Document Management API

A Django REST Framework API for shared JPEG/PNG documents, using PostgreSQL,
private MinIO storage, Redis metadata caching, and JWT application roles.

## Local setup

Requirements:
- Docker Engine with Docker Compose v2 supporting `--wait`
- GNU Make
- Internet access for the initial build

Start the project:

```bash
make up
```

The command creates `.env` if missing, generates a Django secret key,
builds the images, starts the services, applies database migrations,
and initializes the MinIO buckets.

Services:
- Backend: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/api/docs/
- OpenAPI schema: http://127.0.0.1:8000/api/schema/
- MinIO object endpoint: http://127.0.0.1:19000
- MinIO console: http://127.0.0.1:19001

MinIO console credentials are configured in `.env` through
`MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`.

Useful commands:

```bash
make ps
make check
make logs
make shell
make down
```

`make down` preserves database and object-storage volumes.

MinIO is built from the official source tag
`RELEASE.2025-09-07T16-13-09Z`.

VPN software that blocks local networks may prevent access to Docker
services. Check local-network access settings if connections fail.

The example configuration is for local development. Production requires HTTPS,
`DJANGO_DEBUG=False`, suitable allowed hosts, and deployment-specific credentials.
`MINIO_PUBLIC_ENDPOINT_URL` must be reachable by the browser or download client.

## First application admin

Create the initial Django administrator interactively:

```bash
docker compose exec backend python manage.py createsuperuser --username admin
```

Set its application role using `make shell`, then run:

```python
from apps.accounts.models import User

application_admin = User.objects.get(username="admin")
application_admin.role = User.Role.ADMIN
application_admin.save(update_fields=["role"])
```

Application API administration requires `role="admin"`. Django's staff/superuser
flags control the Django administration site, and newly created users default to
the viewer application role. After this bootstrap, an application admin can
create users and assign roles through the API. Public registration is unavailable.

## Authentication and Swagger

Open `/api/docs/` and expand **Authentication → Obtain an access and refresh
token**. Use **Try it out** with the admin's username and password. The response
contains `access` and `refresh`. Click **Authorize** and enter the access token;
Swagger adds the `Bearer` prefix. It does not persist authorization across reloads.

For other clients, login accepts JSON at `POST /api/v1/auth/token/`:

```json
{"username": "admin", "password": "<your-password>"}
```

Send the access token as `Authorization: Bearer <access-token>`. Access tokens
last 5 minutes; refresh tokens last 1 day. Submit `{"refresh": "<refresh-token>"}`
to `POST /api/v1/auth/token/refresh/` to obtain a new access token. Refresh tokens
are not rotated. The same payload sent to `POST /api/v1/auth/logout/` blacklists
that refresh token and returns HTTP 200 with `{}`. Existing access tokens remain
valid until expiry; invalid/expired/blacklisted refresh tokens return 401.

The schema and Swagger page are public, including when an invalid Authorization
header is present. Schema generation does not read live document/user data.
Swagger's default JavaScript/CSS assets load from a CDN and require browser
internet access. The API and schema are served locally; use
`/api/schema/?format=json` for a JSON schema download.

## Endpoints and roles

All paths below start with `/api/v1/`. Document ownership records attribution;
all three roles share access to all documents.

| Method | Path | Allowed roles | Success |
| --- | --- | --- | --- |
| POST | `auth/token/` | Public; valid username/password | 200, access + refresh |
| POST | `auth/token/refresh/` | Public; valid refresh token | 200, access |
| POST | `auth/logout/` | Public; valid refresh token | 200, `{}` |
| GET | `users/` | admin | 200, user array |
| POST | `users/` | admin | 201, user metadata |
| PATCH | `users/<id>/` | admin | 200, user metadata |
| GET | `documents/` | admin, editor, viewer | 200, paginated metadata |
| POST | `documents/` | admin, editor | 201, document metadata |
| GET | `documents/<id>/` | admin, editor, viewer | 200, document metadata |
| PUT/PATCH | `documents/<id>/` | admin, editor | 200, document metadata |
| DELETE | `documents/<id>/` | admin | 204, no body |
| GET | `documents/<id>/download/` | admin, editor, viewer | 200, download URL + expiry |

User creation accepts `username`, `password`, optional `email`, and optional
`role` (`admin`, `editor`, or `viewer`). PATCH can change those details or the
role; password is optional and is never returned. Django password validation
and hashing apply. User detail supports PATCH/OPTIONS; it has no GET route.

Document list/detail also support HEAD and OPTIONS. The download endpoint supports
GET/OPTIONS only. Metadata responses contain `id`, `title`, `original_filename`,
`content_type`, `file_size`, `creator`, `created_at`, and `updated_at`; internal
storage keys and version identifiers are omitted. Times are UTC ISO 8601 values.

## Uploading and updating images

Images must contain real JPEG or PNG data and be at most **10 MiB (10,485,760
bytes)**. The exact limit is accepted. Images must fully decode; truncated pixel
data is rejected. Filenames and declared MIME types do not bypass image-content
validation. Titles are trimmed, nonblank, and at most 255
characters. The server assigns creator, timestamps, unique storage key, and file
metadata; client-supplied values cannot override them.

Set `API_ACCESS_TOKEN` to an access token from login, then upload an existing file:

```bash
curl --request POST 'http://127.0.0.1:8000/api/v1/documents/' \
  --header "Authorization: Bearer $API_ACCESS_TOKEN" \
  --form 'title=Sample image' \
  --form 'file=@/absolute/path/photo.png'
```

PATCH accepts a title and/or file; an empty update returns 400. PUT requires a
title and optionally replaces the image. Send JSON or URL-encoded form data for
title-only changes, and multipart form data for an image replacement. Original
creator and creation time remain unchanged.

```bash
curl --request PATCH 'http://127.0.0.1:8000/api/v1/documents/1/' \
  --header "Authorization: Bearer $API_ACCESS_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"title": "Updated title"}'
```

## Errors and verification

Invalid input returns 400, usually with field names mapped to message lists;
parsing errors use a `detail` string. Missing/invalid/expired credentials and
inactive JWT users return 401. A disallowed or unknown application role returns
403. Missing documents/users and out-of-range valid pages return 404.

Known creation/update/deletion service errors and download signing/lookup errors
return a generic 503. Storage cleanup after an already committed update or
deletion is best effort and does not change its success status. Initial database
outages on list/detail/user queries can still produce a standard server error;
Redis fallback does not make PostgreSQL optional.

Run the regression, migration, and OpenAPI checks:

```bash
make test
make check
docker compose exec -T backend python manage.py spectacular --validate --fail-on-warn --file /tmp/openapi.yaml
```

The schema is generated from the current implementation. Tests validate its
request/response separation, binary multipart fields, query bounds, bearer
security, and error statuses. Optional audit logs, Nginx, Celery, and WebSocket
notifications remain tracked in [the task queue](docs/tasks.md).

Run the real-service integration checks separately:

```bash
make integration
```

This command also requires host Python 3 and Docker Compose supporting the
`!override` and `!reset` merge tags. It builds the current images, creates a
disposable `sanaap-t09-*` project with fresh credentials, migrations, and volumes,
and runs the full regression suite. Random loopback ports expose only its API
and MinIO endpoint. The existing stack keeps running.

Checks cover JWT roles, exact upload/download bytes, private bucket access,
actual 60-second URL expiration, replacement/deletion cleanup, and recovery
from Redis and MinIO outages. Allow several minutes, including the expiration
wait. The command stops only its disposable services during outage checks,
then removes its own containers and volumes and verifies that all pre-existing
volumes remain. If cleanup fails, it reports the private temporary directory
containing recovery configuration. Host VPN settings must permit local Docker
access; the script does not change them.

## Development workflow

Feature branches start from `develop` and return through pull requests.
Release branches prepare delivery to `main` and are merged back into
`develop`. Released versions are tagged.

## Searching and browsing documents

All three authenticated roles share `GET /api/v1/documents/`. Its response now
contains `count`, `next`, `previous`, and `results`; read document metadata from
`results` rather than a top-level array. Documents appear newest first, with
descending id resolving equal creation times.

Optional query parameters:

| Parameter | Behavior |
| --- | --- |
| `search` | Case-insensitive literal substring of title only, up to 255 characters. Leading/trailing whitespace is trimmed; blank text leaves the title unfiltered. |
| `creator` | Exact positive integer user id, at most 9223372036854775807. A valid id without documents returns an empty first page. |
| `page` | Positive integer page number; defaults to 1. |
| `page_size` | Items per page, from 1 to 100; defaults to 10. |

For example, `/api/v1/documents/?search=report&creator=3&page_size=5` returns
documents whose titles contain `report` and whose creator id is 3. Filters apply
before pagination. Follow `next` and `previous` to retain the filters and page
size; either link is `null` when there is no corresponding page. Empty results
have `count: 0`, `results: []`, and both links `null` on page 1.

Invalid parameter values return HTTP 400 with an error under the corresponding
field name. A valid page number beyond the results returns HTTP 404. Filtering
does not restrict shared access to the authenticated user's own documents and
does not affect detail, upload, update, deletion, or download endpoints.
Page-number pagination is not a snapshot: inserts or deletions between requests
can shift which documents fall on later pages.

## Document metadata cache

Individual document GET/HEAD responses cache only their public metadata in
Redis for 60 seconds. Authentication and role/object permission checks run
before cache access. Every read first checks the document's existence and
internal metadata version in PostgreSQL. A cache hit skips the full metadata
fetch and serialization; PostgreSQL remains required even when Redis has a
cached response. Lists and signed download URLs do not use this cache.

Updates rotate an internal UUID version in the database transaction. After an
update or deletion commits, the service attempts to invalidate the previous
version's cache entry. A rollback keeps the previous version and discards that
invalidation. Delayed readers can populate an old version's key after
invalidation, but subsequent reads cannot use it. Equal timestamps and rolled
back updates do not reuse a version. Missing records return 404 even if a cache
entry survives.

Redis read, write, and invalidation errors are logged without backend exception
details and do not prevent database-backed reads or committed writes. A failed
cache read also skips cache population for that request. Old keys left by a
failed invalidation expire within 60 seconds. The version field is internal and
is absent from API responses. Direct database edits outside the document service
must also change `metadata_version` when they change document metadata.

## Private document downloads

An authenticated application admin, editor, or viewer can request
`GET /api/v1/documents/<id>/download/` for any document, regardless of its creator.
Include the access token in the `Authorization: Bearer <access-token>` header.
The response contains `download_url` and `expires_in: 60`. Retrieve the image
directly from `download_url` before it expires. The URL uses
`MINIO_PUBLIC_ENDPOINT_URL`, which must be reachable by the client; the bucket
remains private.

JWT authentication, application-role checks, and the document lookup run before
URL signing. Missing documents return HTTP 404; known signing or document-lookup
database failures return a generic HTTP 503. Only GET and OPTIONS are supported;
HEAD returns 405 without signing. URL responses use `Cache-Control: private,
no-cache, no-store, must-revalidate`, and the backend does not cache signed URLs.
Signing happens locally and does not check object existence or storage
availability, so successful URL issuance does not guarantee a successful file
transfer.

Treat the signed URL as a temporary bearer credential: anyone holding it can
retrieve its target until expiry, provided the object remains available. Logout
blacklists the refresh token; an existing access token remains usable until its
own expiry, including to request another URL. Deactivating a user or removing
their permitted application role blocks new URL requests, but does not revoke
previously issued URLs. Changing between admin, editor, and viewer preserves
download access. Deleting or replacing an object can make its earlier URLs stop
working before expiry. Avoid storing or logging signed URLs.

## Document storage consistency

PostgreSQL transactions do not include MinIO operations. Each image replacement
uploads a new UUID-based object before updating document metadata. The update
locks and rereads the document row so concurrent replacements use its current
storage key. The previous object is deleted only after the database transaction
commits. Metadata-only updates retain the image and original creator.

Initial creation uploads the object before inserting its metadata. Database
failure attempts to delete the acknowledged upload. A storage write that raises
before returning its key triggers best-effort cleanup of the attempted UUID key,
while preserving the original storage error.

Document deletion also locks and rereads the current row, removes its metadata
inside the transaction, and deletes its current storage object after commit.
If the storage object is already missing, metadata deletion can still succeed.
If post-commit storage cleanup fails, the API still returns HTTP 204 because
the database deletion has committed; the private orphan is logged for cleanup.

If recording a replacement fails, the service attempts to remove the new object
and preserves the previous reference. Cleanup errors are logged; failure to
delete an old object after commit does not undo a successful update.

There is no automatic orphan-object reconciler. A process crash between upload
and database commit, a failed cleanup, or a crash before an after-commit callback
runs can leave unreferenced objects that require reconciliation. Creation and replacement
upload errors trigger best-effort deletion of the attempted UUID key. An upload
that finishes after a timeout, or a backend that changes an unacknowledged key,
can still leave an orphan despite that attempt. If a caller wraps the service
in an outer transaction, old-object deletion waits for that
outer commit. An outer rollback preserves the old reference and object but can
leave the new upload unreferenced, because Django has no rollback callback for
this cleanup. The API currently uses the service transaction rather than an
outer request transaction.
