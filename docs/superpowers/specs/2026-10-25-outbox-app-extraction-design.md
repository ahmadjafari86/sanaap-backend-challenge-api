# Extract Outbox Into a Dedicated Django App

Status: Approved by the user on 2026-10-25.

## Intent

Make the assessment code easier to read, maintain, and debug by moving message
delivery infrastructure into `apps.outbox`, while keeping document processing
rules in `apps.documents`. The user permits rebuilding migrations from scratch;
preserving existing database data and migration history is not required.

## Approaches

- Move every `outbox_*` module into the new app: simple file relocation, but it
  makes the new app own document state transitions and storage rules.
- Separate the delivery infrastructure from document adapters: a small amount of
  explicit wiring keeps responsibilities clear. This is the recommended approach,
  consistent with the boundary discussed with the user.
- Build a reusable messaging framework: unnecessary scope for this assessment.

## Responsibilities and Layout

`apps.outbox` owns:

- `OutboxMessage` and `OutboxReceipt`, with normal model ownership in the new app.
- Transactional enqueue and deduplication.
- Message claiming, leases, retry/backoff, delivery finalization, and the relay.
- General status/replay operations and the `outbox_status`, `replay_outbox`, and
  `run_outbox_relay` management commands.
- A small explicit registration mechanism for event validation and dispatch.
  Domain apps register their adapters during app initialization; registration
  performs no database queries, starts no workers, and rejects conflicting
  duplicate kinds. Repeating an identical registration is harmless, including
  when Django initializes the same app configuration again during tests.

`apps.documents` owns:

- Document and audit models, storage attempts, transitions, and compensation.
- Document event names, payload rules, delivery handlers, upload consumption,
  stale upload recovery, and document-specific status information.
- A focused `outbox` package for these adapters, replacing the scattered
  `outbox_handlers.py`, `outbox_uploads.py`, and `outbox_recovery.py` modules.
- `recover_outbox_uploads` and its `requeue_pending_documents` alias.

Document code imports the public outbox API. The outbox app never imports
`apps.documents` directly. The registration mechanism also exposes the existing
bounded recovery and document-specific status callbacks to the relay/operator
commands, so those commands retain their current behavior without owning upload
business rules. It is an in-process mechanism, with no plugin discovery, new
dependency, or additional service.

## Preserved Behavior

- Document changes, audit writes, receipts, and emitted messages retain their
  current transaction boundaries on the same database.
- Keep event identities, payload shapes, document ID snapshots without cascading
  foreign keys, receipt deduplication, lease fencing, retry limits, and cleanup
  ownership checks. The document ID snapshot may remain on the message model;
  generalizing it is outside this refactor.
- Keep HTTP/WebSocket contracts, task identity, settings, command names/output,
  the relay service, and current Phase 1 delivery scope.
- Keep validation before enqueue and dispatch, unknown-kind rejection, sanitized
  errors, and the existing distinction between publication and upload processing.
- Use direct imports from the final module locations; update callers and tests
  instead of retaining unnecessary aliases at the old locations.

## Migrations, Tests, and Documentation

Generate initial migrations for the final models in `accounts`, `documents`, and
`outbox`, with correct user-model dependencies. Verify them on a fresh disposable
database. Do not drop the user's existing database or Docker volumes as part of
this refactor; existing local databases will require an explicit reset before
using the rewritten migration history.

Move infrastructure tests into `apps.outbox.tests`; retain document integration,
upload, recovery, and cleanup ownership tests with the document adapters. Update
imports, patch targets, log expectations, integration scripts, and README paths.
Preserve historical task records, marking the prior outbox design as superseded
for app placement and migration strategy where needed.

Verify Django checks, migration drift, fresh migration application, the existing
PostgreSQL regression suite, and the available outbox integration checks. Add
focused checks for adapter registration and dispatch boundaries only where the
existing behavioral coverage does not cover the new wiring.

## Scope

This is a structural refactor. It does not implement Phase 2, change storage
policies, add API features, merge into `develop`/`main`, or deploy a release.
Names of small internal helpers and mechanical test-file splits are left to the
implementation, following the responsibilities above.
