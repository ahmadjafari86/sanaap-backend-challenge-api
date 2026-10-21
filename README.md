# Sanaap Document Management API

A Django REST Framework API for shared JPEG/PNG documents, using PostgreSQL,
private MinIO storage, Celery background uploads, Redis metadata caching,
authenticated WebSocket notifications, and JWT application roles.

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
initializes the MinIO buckets, and collects static assets.

Services:
- API through Nginx: http://127.0.0.1:8000
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

Nginx runs as an unprivileged user and forwards requests to Gunicorn's Uvicorn
ASGI workers on the
private Compose network. Gunicorn has no published host port. A shared
`static_data` volume is writable by the backend's `collectstatic` command and
read-only in Nginx, which serves `/static/`. Nginx uses the pinned official
1.30.6 Alpine image; `/healthz` checks the proxy, while backend health is checked
separately. The interactive Swagger UI still loads its reviewed CDN assets.

The proxy accepts request bodies up to **11 MiB**, leaving multipart overhead
for the API's **10 MiB file** limit. The exact file limit is accepted; larger
files within the proxy limit get an API 400, and oversized whole requests get
Nginx 413. An unavailable backend yields Nginx 502 or 504, depending on whether
the connection fails or times out. These proxy errors happen
before API auditing. API JSON errors and authentication/no-store headers pass
through; API responses are not cached or automatically retried by Nginx.

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
| POST | `documents/` | admin, editor | 202, PENDING document metadata |
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
`content_type`, `file_size`, `status`, `creator`, `created_at`, and `updated_at`; internal
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

Creation returns **202 Accepted** with `status: PENDING`. Poll the returned id's
detail endpoint until `status` becomes `READY` or `FAILED`. A Celery worker reads
validated bytes from a private shared staging volume and uploads them to MinIO.
Backend startup initializes the configured MinIO storage instance with
`initialize_document_storage`; this supports the timeout-configured backend
subclass, which the library's name-filtered `initialize_buckets` command skips.
Existing documents migrate as READY. Pending/failed metadata remains visible to
all three authenticated roles; only READY documents can be downloaded or updated
(otherwise 409). Admins can delete documents in any state. Client-supplied status,
staging identifiers and attempt counts cannot override server state.

PATCH accepts a title and/or file; an empty update returns 400. PUT requires a
title and optionally replaces the image. Send JSON or URL-encoded form data for
title-only changes, and multipart form data for an image replacement. Original
creator and creation time remain unchanged. These updates remain synchronous and
return 200 for READY documents.

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
403. Missing documents/users and out-of-range valid pages return 404. Download or
update of a PENDING/FAILED document returns 409.

Known creation/update/deletion service errors and download signing/lookup errors
return a generic 503. Storage cleanup after an already committed update or
deletion is best effort and does not change its success status. Initial database
outages on list/detail/user queries can still produce a standard server error;
Redis fallback does not make PostgreSQL optional. A staging/database failure
before acceptance returns 503 without a new document. Once accepted, broker
publication or exhausted worker storage failures produce a FAILED document;
the original acceptance remains 202 rather than a completed-upload guarantee.

Run the regression, migration, and OpenAPI checks:

```bash
make test
make check
docker compose exec -T backend python manage.py spectacular --validate --fail-on-warn --file /tmp/openapi.yaml
```

The schema is generated from the current implementation. Tests validate its
request/response separation, binary multipart fields, query bounds, bearer
security, and error statuses. Audit events are described below. Nginx deployment
Celery background storage and authenticated WebSocket notifications are included;
delivery checkpoints are tracked in [the task queue](docs/tasks.md).

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
from Redis and MinIO outages, plus durable audit records for every tested
operation. It also checks static file bytes/MIME types, public pagination links,
forwarded-header spoof resistance, a real 10 MiB transfer, 400/413 boundaries,
and backend outage/recreation recovery through Nginx. It stops/restarts only the
disposable worker to prove queued PENDING work reaches READY, verifies broker
publication failure and exhausted storage retries reach FAILED, and checks all
terminal staging files are removed. WebSocket checks use the container's client library with credentials over
private stdin: all three roles, Origin/refresh/query rejection, Celery READY and
replacement events through Nginx/Redis, no FAILED/delete events, and Redis
disconnection/reconnect are exercised. Unit tests use an isolated in-memory
channel layer so fixture IDs cannot reach running application subscribers.
Allow several minutes, including the expiration
wait. The command stops only its disposable services during outage checks,
then removes its own containers and volumes and verifies that all pre-existing
volumes remain. If cleanup fails, it reports the private temporary directory
containing recovery configuration. Host VPN settings must permit local Docker
access; the script does not change them.

## Development workflow

Feature branches start from `develop` and return through pull requests.
Release branches prepare delivery to `main` and are merged back into
`develop`. Released versions are tagged.

## HTTPS deployment example

The default stack binds HTTP only to loopback. The provided
`compose.production.yaml.example` and `docker/nginx/production.conf.example`
show TLS termination at this Nginx container. They are applied explicitly;
the normal `make up` command does not publish the service to the internet.

Before using the example:

1. Replace `api.example.com` in the Nginx example with the actual public DNS
   name and set `DJANGO_ALLOWED_HOSTS` accordingly in `.env`. Set
   `WEBSOCKET_ALLOWED_ORIGINS=https://api.example.com` (or the actual browser
   frontend's HTTPS origin); list explicit comma-separated origins with their
   ports, without paths or wildcard `*`. Clients connect with `wss://`.
2. Use deployment-specific Django/database/MinIO credentials. Set
   `MINIO_PUBLIC_ENDPOINT_URL` to a separately reachable **HTTPS** object
   endpoint; the API proxy does not expose private MinIO objects directly.
3. Obtain certificates for that DNS name through the deployment's certificate
   provider. Set absolute `TLS_CERTIFICATE_PATH` and `TLS_PRIVATE_KEY_PATH`
   values in `.env`, pointing to existing PEM files outside the repository.
   Make them readable by the Nginx container user (UID/GID 101); a private key
   with mode `0640` and the appropriate group is sufficient. Mounts stay
   read-only, and missing files are not created automatically.
4. Review the example's **public ports 80 and 443**, then run:

```bash
docker compose -f compose.yaml -f compose.production.yaml.example config --quiet
docker compose -f compose.yaml -f compose.production.yaml.example up -d --build --wait
docker compose -f compose.yaml -f compose.production.yaml.example exec nginx nginx -t
```

The example disables debug, enables secure session/CSRF cookies and Django's
HTTPS redirect, redirects public HTTP to the canonical HTTPS host, and retains
an internal HTTP proxy health check. Nginx overwrites forwarded scheme/client
headers. Compose explicitly enables `DJANGO_TRUST_PROXY`; native Django runs
default to `False`. Only the private backend behind this controlled proxy
should trust `X-Forwarded-Proto`. A different external TLS terminator needs its
own explicit trust configuration, rather than forwarding client-supplied scheme
headers. Certificate issuance and renewal remain deployment responsibilities.

Static assets are public and images remain private in MinIO. The access-log
format excludes query strings, request headers and response bodies; normal
Nginx error logs still provide proxy diagnostics. Existing audit logs keep their
separate restricted schema.

Configuration references: [Nginx proxy headers](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header),
[request body limit](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size),
[upstream DNS resolution](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#resolve),
[Django static deployment](https://docs.djangoproject.com/en/5.2/howto/static-files/deployment/),
and [trusted proxy scheme](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header).

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

HTTP creation stages validated bytes privately before inserting PENDING metadata
and its acceptance audit in one transaction. A database/staging failure attempts
staging cleanup. Dispatch runs only after commit. The worker locks the row for
one bounded storage attempt, uses the document's stable UUID object key, and
atomically commits READY plus its storage audit. Failed acknowledgement/database
recording triggers attempted-object cleanup. After rollback, compensation reacquires
the row lock and rechecks current ownership so it cannot delete a duplicate worker's
committed READY object. If database ownership cannot be verified, cleanup is deferred
and a possible orphan is retained. Repeated delivery of terminal or
deleted rows cannot upload again or duplicate storage events. Concurrent workers,
updates and deletion serialize on that row; writers may wait for storage I/O.

Document deletion also locks and rereads the current row, removes its metadata
inside the transaction, and deletes its current storage object and any staged file after commit.
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

## Background worker and recovery

`make up` starts the worker alongside the API. Backend and worker share only the
private `staging_data` volume (files mode 0600, owner UID 10001); Nginx cannot
read it. The worker uses the backend image and waits for backend startup/migrations,
without running migrations or collectstatic itself. Redis DB 0 is metadata cache;
DB 1 is the JSON-only Celery broker. Redis uses AOF in `redis_data`; no Celery
result backend is used, because the document row is the authoritative state.
Native runs can set `CELERY_BROKER_URL` and `DOCUMENT_STAGING_ROOT`.

Transient storage errors receive at most **four persisted attempts** with bounded
retry delays. Missing staged files and unexpected storage errors fail immediately.
Terminal outcomes clean staging after commit and rotate the metadata cache version.
Celery acknowledges after processing and requeues on worker-process loss. Uploads
use bounded connect/read timeouts; broker publication does not retry indefinitely.
Database-unavailable task retries are bounded separately; if a terminal state
cannot be recorded, the row may remain PENDING for operator recovery.

Publishing after commit is not a transactional outbox: a crash in the commit-to-
publish gap, broker data loss, or an interrupted retry publication may leave a
PENDING row. After restoring Redis/worker/database availability, republish these
rows explicitly (duplicate messages are safe):

```bash
docker compose exec backend python manage.py requeue_pending_documents
docker compose logs --tail=100 worker
```

The command touches only PENDING rows. FAILED rows stay terminal: delete/re-upload
after correcting the cause. If publication fails, the accepted row is marked
FAILED with a redacted storage-failure audit, and staging cleanup is attempted.
The response object can still say PENDING while a following detail read already
reports FAILED or READY. A process crash/outer rollback between staging and commit,
or failure/crash before cleanup, can leave private staged or object orphans;
automatic reconciliation and exactly-once transport are not provided. Disk use
must be monitored when workers are stopped. A killed attempt can be redelivered
without its uncommitted attempt counter; the four-attempt limit bounds recorded
failures, not repeated forced process termination.

Celery behavior references: [Django transaction dispatch](https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html#trigger-tasks-at-the-end-of-the-database-transaction),
[idempotence and late acknowledgements](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
and [Redis visibility timeout](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html#visibility-timeout).

## Document audit events

`DocumentAuditEvent` stores the actor id, action (`create`, `update`, `delete`,
`download_url`, or `store`), document id, UTC `occurred_at`, `success`/`failure` outcome,
and HTTP status code. Events begin with this feature's deployment; historical
operations are not reconstructed. IDs are snapshots without foreign keys,
so deleting a document or user retains its audit history.

Successful mutations and their audit events commit in the same database
transaction. If the success event cannot be recorded, the API returns its
existing safe 503 response and rolls back the mutation; new uploads use the
existing best-effort compensation. A deletion whose metadata committed but
whose object cleanup failed still records a successful deletion with status
204. Cleanup failures remain in the redacted operator logs.

For background creation, `create/success/202` records acceptance. The worker's
`store/success/200` records READY, or `store/failure/503` records FAILED, with the
original creator as actor. These storage-event status codes describe the outcome
and are not a second HTTP response. The terminal state and event share a transaction;
retries/duplicate messages add no duplicate terminal event. API 409 denials record
their existing operation failure event.

Failed API attempts record authentication, permission, validation, missing-id,
and service failures. Anonymous or invalid-token attempts have a null actor;
authenticated identities come from the server. Missing document ids are kept
when representable as database integers, otherwise stored as null. Failure
auditing is best effort: database outages or a process crash can prevent its
record, and it never replaces the original error response.

`download_url` means the server produced a URL for the response. It does not
prove client receipt or a completed MinIO transfer. No request/response bodies,
headers, passwords, tokens, signed URLs, filenames, object keys, or exception
text are stored in events. Routine list/detail/HEAD/OPTIONS requests and
unsupported methods do not produce operation events. Direct internal service
calls may have a null actor when none is supplied; failed attempts are captured
at the API boundary.

There is no public audit endpoint or editable audit administration page.
Operators can inspect records through `make shell`:

```python
from apps.documents.models import DocumentAuditEvent

list(DocumentAuditEvent.objects.filter(document_id=123).values(
    "actor_id", "action", "document_id", "occurred_at", "outcome", "status_code",
)[:20])
```

Application code appends events; database administrators can still modify
them. Cryptographic tamper evidence and automated retention are not implemented.

## WebSocket document notifications

Connect to `ws://127.0.0.1:8000/ws/documents/` locally and `wss://<api-host>/ws/documents/`
in production. The browser page's Origin must match `WEBSOCKET_ALLOWED_ORIGINS`;
local defaults allow `http://127.0.0.1:8000` and `http://localhost:8000`.
Native clients must send an allowed Origin too. Origin checking supplements JWT
and is not authentication. Tokens never belong in the URL or subprotocol.

Send the first text frame within **5 seconds**:

```json
{"type":"authenticate","access":"<access JWT>"}
```

Wait for `{"type":"authenticated"}` before treating the connection as subscribed.
The handshake accepts before authentication, but no document data or group
subscription is available before a valid access token and active application
role. Session cookies and refresh tokens do not authenticate this endpoint.
All three roles receive the same shared-document hints:

```json
{"type":"document.ready","document_id":42}
{"type":"document.updated","document_id":42}
```

READY hints follow a committed initial upload, including the internal synchronous
creation service; update hints follow committed title/file updates. PENDING,
FAILED and deletion do not produce document messages. Events contain only type
and ID: obtain current metadata from the REST API and request a new download URL
when needed. Notifications are best effort, with no durable replay, ordering or
exactly-once guarantee. A concurrent update/delete may already have changed the
row by the time a hint arrives; refetch and handle 404. Reconcile through the
list/detail API after reconnect, timeout or Redis outage. Publication waits at
most two seconds for Redis; failure cannot undo the document or its audit row.

The server rechecks token expiry and the current user's active role before every
hint and application ping. It closes even idle connections at access-token
expiry; obtain/refresh an access token over REST, reconnect and authenticate again.
Logout retains the existing access-token lifetime. Role/deactivation changes take
effect on the next message check; idle connections still expire on schedule.
`{"type":"ping"}` returns `{"type":"pong"}` after authentication. Other commands,
binary frames, invalid JSON and repeated authentication close the connection.
Frames are limited to **8 KiB** and the server limits the receive queue to 16.

| Close code | Meaning |
| --- | --- |
| 4400 | Invalid protocol, repeated authentication or query string (direct ASGI) |
| 4401 | Missing/invalid/expired access token, inactive/deleted user or auth timeout |
| 4403 | User no longer has a supported application role |
| 1009 | Frame exceeds the size limit |
| 1013 | Temporary identity/channel dependency failure; reconnect with backoff |

Missing/disallowed Origin is denied during handshake with HTTP 403. Nginx rejects
any query string on this path with HTTP 400 before forwarding to application
request logs. Transport loss can instead appear as an abnormal closure; the
close code is not a delivery guarantee. Never log first authentication frames.

Redis DB **2** is the Channels layer, separate from metadata cache DB 0 and Celery
broker DB 1. Compose sets `CHANNEL_REDIS_URL=redis://redis:6379/2`; native development
uses the loopback port in `.env.example`. Group queues hold at most 100 messages
and messages expire after 60 seconds; slow clients may miss hints. Nginx upgrades
only `/ws/documents/` and has a **75-second upstream idle timeout**. ASGI sends
protocol pings every 20 seconds with a 20-second response timeout. Custom proxies
must support HTTP/1.1 upgrade and an idle timeout above that interval. Changing
these deployment limits requires restarting the backend/proxy.

References: [Channels authentication](https://channels.readthedocs.io/en/stable/topics/authentication.html),
[Origin protection](https://channels.readthedocs.io/en/stable/topics/security.html),
[channel-layer delivery](https://channels.readthedocs.io/en/stable/channel_layer_spec.html),
[Gunicorn ASGI worker](https://github.com/Kludex/uvicorn-worker),
and [Nginx WebSocket proxying](https://nginx.org/en/docs/http/websocket.html).
