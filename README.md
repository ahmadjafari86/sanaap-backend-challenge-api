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

## Development workflow

Feature branches start from `develop` and return through pull requests.
Release branches prepare delivery to `main` and are merged back into
`develop`. Released versions are tagged.

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
runs can leave unreferenced objects that require reconciliation. Replacement
upload errors trigger best-effort deletion of the attempted UUID key. An upload
that finishes after a timeout, or a backend that changes an unacknowledged key,
can still leave an orphan despite that attempt. If a caller wraps the service
in an outer transaction, old-object deletion waits for that
outer commit. An outer rollback preserves the old reference and object but can
leave the new upload unreferenced, because Django has no rollback callback for
this cleanup. The API currently uses the service transaction rather than an
outer request transaction.
