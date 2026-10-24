# Transactional Outbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver upload/cleanup reliability independently in Phase 1, then extend
the outbox to remaining document side effects in Phase 2 while preserving contracts.

**Architecture:** Store outbox messages alongside document/audit mutations in
PostgreSQL. An independent relay publishes uploads to Celery and executes strict
cleanup adapters in Phase 1; Channels/cache adapters follow in Phase 2. Upload
receipts, durable next-attempt messages and automatic recovery distinguish broker
publication from completed processing.

**Tech Stack:** Existing Django 5.2, PostgreSQL 17, Celery 5.6/Redis, Channels,
MinIO, Docker Compose and Django's unittest runner.

**Spec:** [2026-10-24-transactional-outbox-design.md](../specs/2026-10-24-transactional-outbox-design.md).

**Status:** Phase 1 (T16–T19) is implemented and verified on
`codex/transactional-outbox-phase1`; user review of the unmerged branch is pending.
Independent review findings are fixed. Phase 2 (T20–T23) remains deferred.
T16–T23 extend [the project queue](../../tasks.md).

## Delivery Phases

| Phase | Tasks | Independently deliverable result | Status |
| --- | --- | --- | --- |
| 1 | T16–T19 | Durable uploads and cleanup, basic status/replay, automatic upload recovery, core integration and rollout docs | Implemented; 334 tests and rebuilt core integration pass; user review pending |
| 2 | T20–T23 | Durable notifications/cache invalidation, advanced operations and whole-scope verification | Deferred until Phase 1 review |

Phase 1 has no dependency on T20–T23. Move basic status/replay from T22 to T17,
automatic stale/legacy upload recovery from T22 to T18, and core integration,
full regression and initial rollout/operator docs from T23 to T19. Keep audit
transactional in every Phase 1 change. Keep existing best-effort notification and
cache callbacks during Phase 1, including background READY/FAILED transitions;
do not produce their outbox messages or implement their adapters before Phase 2.

## Global Constraints

- Retain Django 5.2, PostgreSQL 17, Celery 5.6, Redis, Channels and private MinIO;
  introduce no Kafka, CDC connector, Celery Beat, or result backend.
- Preserve POST 202, update 200, delete 204 and PENDING/READY/FAILED public states.
  Broker failure leaves an accepted upload PENDING and retains its staging file.
- Preserve roles, JWT/Origin checks, private storage, 10 MiB JPEG/PNG validation,
  download URL lifetime of 60 seconds, metadata cache TTL of 60 seconds, and
  synchronous document update behavior.
- Four persisted logical storage attempts remain the maximum. Interrupted
  external calls can repeat after rollback; physical-call exactly-once is not promised.
- Preserve WebSocket frames `{type, document_id}` and the existing
  `document.ready`/`document.updated` types. FAILED and deletion do not emit hints.
- Audit remains synchronous PostgreSQL data in the mutation transaction. Outbox
  is not an audit store and does not replace `DocumentAuditEvent`.
- Payloads/logs must not contain file bytes, JWTs, credentials, presigned URLs,
  document titles or original filenames. No cascading document FK on messages.
- Delivery to Channels is not client receipt; duplicates/delay are allowed.
- Implement and verify the requested Phase 1 batch with per-task checkpoint
  commits and one final independent branch review. Shared user review remains a
  final branch checkpoint; tests do not imply merge or release permission.
- T19 is the Phase 1 acceptance gate: core integration, regression, recovery and
  rollout evidence are required before the phase is called complete.
- User branch constraint: implement Phase 1 on `codex/transactional-outbox-phase1`
  from `develop` at `008f1f7`. Do not merge into `develop` or `main`.

## Review Focus

- A worker completes before the relay records publication: stale publisher
  updates must not erase receipts or rearm completed work (T17/T18).
- Redis loses a published task or a retry send is interrupted: recovery reuses
  the same logical attempt without resetting its budget (T18/T19).
- Delete/replacement races with retries and compensation: never delete a
  currently owned object or lose the old key needed for cleanup (T19).
- Delayed invalidation/notification arrives after another update or deletion:
  cache freshness and the existing minimal hint protocol remain valid (T20/T21).
- Replay runs during active processing or a Phase 1 upgrade: preserve active
  obligations and legacy task compatibility (T17/T18/T19). Later pruning preserves
  deduplication history (T22/T23).

## File Responsibilities

| File | Responsibility |
| --- | --- |
| `apps/documents/models.py`, `migrations/0005_transactional_outbox.py` | Message, lease, receipt data and indexes |
| `apps/documents/outbox.py` | Kind/payload validation and transactional enqueue/deduplication |
| `apps/documents/outbox_delivery.py` | Claims, lease fencing, retry scheduling and dispatch loop |
| `apps/documents/outbox_handlers.py` | Phase 1 upload/cleanup adapters; Phase 2 Channels/cache adapters |
| `apps/documents/outbox_uploads.py` | Upload event resolution, receipts and durable attempt scheduling |
| `apps/documents/outbox_recovery.py` | Phase 1 stale/legacy upload recovery and periodic scheduling |
| `apps/documents/outbox_operations.py` | Phase 1 status/replay; Phase 2 reporting, retention and orphan reporting |
| `apps/documents/background.py`, `services.py`, `tasks.py` | Document workflows producing/consuming obligations |
| `apps/documents/notifications.py`, `caching.py`, `audit.py` | Existing contracts and compatibility adapters |
| `apps/documents/management/commands/*outbox*.py` | Relay/operator command entry points |
| `compose.yaml`, `compose.integration.yaml`, `config/settings.py`, `.env.example` | Runtime configuration and independent relay |
| `apps/documents/tests/test_outbox*.py` | Focused Django unit/transaction regressions |
| `scripts/integration_check.py`, `scripts/outbox_checks.py`, existing script/test files | Disposable real-service verification and safety checks |

Migration numbering must be rechecked before implementation. Each task adds its
own focused tests and necessary documentation; T19 is the core system gate and
T23 extends it to the full scope.
The existing Make targets run in a container, so rebuild/recreate disposable test
images before verifying changed source rather than testing an old running image.

## Dependencies and Reviewable Deliverables

| ID | Phase | Deliverable | Depends on |
| --- | --- | --- | --- |
| T16 | 1 | Data model and transactional enqueue contract | None |
| T17 | 1 | Relay, upload/cleanup adapters, retry, leases and basic status/replay | T16 |
| T18 | 1 | Upload receipts/retries, periodic recovery and legacy compatibility | T16, T17 |
| T19 | 1 gate | Durable cleanup, core integration, regression and rollout documentation | T16–T18 |
| T20 | 2 | Durable WebSocket publication | Reviewed Phase 1 |
| T21 | 2 | Durable cache invalidation and expanded consistency tests | T16–T20 |
| T22 | 2 | Detailed operations, retention and orphan reports | T16–T21 |
| T23 | 2 gate | Full-scope integration, Phase 1 upgrade and documentation | T16–T22 |

Follow this order for the existing shared-review workflow. Start with T16 and
finish the T19 acceptance gate before moving to the deferred phase. Phase 1
verification must not wait for T23.

---

## Phase 1 — Uploads and Cleanup (Implemented)

### T16: Data Model and Transactional Enqueue

**Files:** Modify `apps/documents/models.py`; create
`apps/documents/migrations/0005_transactional_outbox.py`,
`apps/documents/outbox.py`, `apps/documents/tests/test_outbox_models.py`.

**Interfaces:** Produces `OutboxMessage`, `OutboxReceipt`, and
`enqueue_message(*, kind: str, document_id: int | None, payload: dict,
dedupe_key: str, available_at: datetime | None = None) -> OutboxMessage`.
Enqueue requires an existing atomic block. Message kinds, fields, states,
payloads and dedupe mismatch behavior are defined in the spec.
Reserve the Phase 2 schema kinds if useful, but activate only the three Phase 1
destinations. No cache/notification writer or adapter is required by this task.

- [x] Add failing tests `test_mutation_and_message_rollback_together`,
  `test_identical_dedupe_returns_same_event`, `test_dedupe_content_mismatch_fails`,
  `test_document_deletion_preserves_message`, and
  `test_unknown_kind_version_or_private_payload_rejected` with the spec's exact
  allowed fields and version 1. Test unique receipt event IDs and claim indexes.
- [x] Run `python manage.py test apps.documents.tests.test_outbox_models --noinput`
  with isolated PostgreSQL; confirm failures name the missing model/helper.
  Verify the full suite again in the rebuilt final image.
- [x] Add the additive migration and enqueue validation. Enqueue database errors
  must propagate; use a savepoint for a concurrent dedupe-key race rather than
  leaving the caller's transaction broken. Test rollback after conflicting enqueue.
- [x] Rerun focused tests plus `python manage.py makemigrations --check --dry-run`;
  verify fresh migration and upgrade from documents migration 0004 without changing
  existing document/audit rows.
- [x] Commit as `feat: add transactional document outbox models`; retain the
  combined Phase 1 branch for shared user review.

### T17: Independent Relay, Retry and Basic Operations

**Files:** Create `apps/documents/outbox_delivery.py`,
`apps/documents/outbox_handlers.py`,
`apps/documents/outbox_operations.py`,
`apps/documents/management/commands/run_outbox_relay.py`,
`apps/documents/management/commands/outbox_status.py`,
`apps/documents/management/commands/replay_outbox.py`,
`apps/documents/tests/test_outbox_delivery.py`,
`apps/documents/tests/test_outbox_operations.py`; modify `config/settings.py`,
`.env.example`, `compose.yaml`, `compose.integration.yaml`,
`scripts/integration_check.py`, `apps/documents/tests/test_integration_safety.py`
and `README.md`.

**Interfaces:** Consumes T16. Produces immutable `DeliveryClaim` (event ID, kind,
document ID, payload, token), `claim_batch(*, now: datetime, limit: int) -> list[DeliveryClaim]`,
`dispatch_message(claim: DeliveryClaim) -> None`, and
`relay_once(*, now: datetime | None = None) -> int`. Adapters raise on failure.
The upload adapter sends `store_document(document_id, event_id)`; T18 supplies
the compatible worker before any writer creates upload events.
Also produces `get_outbox_status(*, now: datetime) -> dict` and
`replay_dead_messages(*, event_ids: list[UUID], now: datetime) -> int` in
`outbox_operations.py`. Replay accepts explicit DEAD IDs, preserves UUIDs and
receipts, resets publication retry scheduling, and never changes document state
or storage attempts. State counts, oldest work age and expired leases are visible.

- [x] Add failing `TransactionTestCase` coverage for two concurrent PostgreSQL
  claimants, rollback invisibility, expired leases and stale-token finalization.
  Add deterministic-clock/jitter cases for 2-second base, 300-second cap,
  0–20% jitter, DEAD after 20 publication attempts, malformed payload DEAD,
  and publication success followed by failure to record its result.
  Add tests for basic status and explicit replay, rejecting active/non-DEAD rows
  and preserving document attempt budgets/receipts during concurrent replay.
- [x] Run `python manage.py test apps.documents.tests.test_outbox_delivery apps.documents.tests.test_outbox_operations --noinput`;
  confirm missing implementation failures using PostgreSQL, not SQLite.
- [x] Implement short claim/finalization transactions, I/O outside DB locks,
  conditional token updates, strict adapters for upload/object-delete/staging-delete,
  and bounded external calls. Missing files succeed; errors propagate with only
  sanitized codes persisted. Celery task UUID is tracing, not deduplication.
- [x] Implement bounded status output and explicit DEAD replay commands; redact
  payloads and private exception details. Notification/cache adapters wait for
  T20/T21. Update README with the initial relay/status/replay command examples.
- [x] Add relay command/Compose service, poll/batch/lease/retry settings and loop
  heartbeat. Support `--once`, SIGTERM and database reconnect/backoff. Relay startup
  waits for migrations; its loop survives Redis/MinIO outages. Add the seventh
  service to disposable image/env configuration and exact service allowlists.
- [x] Verify focused tests, `docker compose config --quiet`, independent
  empty-queue startup/shutdown, health checks and no published relay ports.
  Assert another transaction can acquire an event lock while its handler blocks.
- [x] Commit as `feat: add independent outbox relay and retry delivery`; retain
  the combined Phase 1 branch for shared user review.

### T18: Upload Attempts, Receipts and Automatic Recovery

**Files:** Create `apps/documents/outbox_uploads.py`,
`apps/documents/outbox_recovery.py`,
`apps/documents/management/commands/recover_outbox_uploads.py`,
`apps/documents/tests/test_outbox_uploads.py`,
`apps/documents/tests/test_outbox_recovery.py`; modify `background.py`, `tasks.py`,
`apps/documents/management/commands/requeue_pending_documents.py`,
`apps/documents/management/commands/run_outbox_relay.py`, `config/settings.py`,
`.env.example`, `README.md`, `scripts/integration_check.py`,
`apps/documents/tests/test_background_storage.py` and relevant API tests.

**Interfaces:** Consumes T16/T17. Produces
`resolve_upload_message(*, document_id: int, event_id: str | None) -> OutboxMessage | None`
and `process_outbox_upload(*, document_id: int, event_id: UUID) -> None`.
`store_document(document_id, event_id=None)` supports old broker messages.
Keep `process_staged_document` as a compatibility wrapper if still needed by
existing callers/tests; it must use the same receipt path.
Produces `recover_uploads(*, now: datetime, limit: int, dry_run: bool = False) -> int`
in `outbox_recovery.py`, used by both recovery commands and the relay's periodic
scan. Recovery is part of Phase 1, not a dependency on T22.

- [x] Add failing tests for 202/PENDING plus retained staging with Redis down;
  atomic acceptance audit/upload event; duplicate UUID and legacy delivery;
  worker completion before relay finalization; and DB rollback after MinIO I/O.
  Assert one logical attempt/audit/follow-up set, including after redelivery.
- [x] Add transient-attempt tests: a receipt and attempt 2 message commit together
  after attempt 1; four persisted failures produce FAILED, one store-failure
  audit and cleanup; missing staging fails terminally. Broker/DB failure alone
  does not consume the persisted storage budget. Future/obsolete attempts do
  not run early or duplicate an already receipted attempt.
- [x] Add recovery tests for publication older than 300 seconds without a receipt,
  Redis losing a published task, legacy PENDING rows, concurrent worker/recovery,
  completed receipt before publisher finalization and periodic scans while Redis
  is unavailable. Reuse the logical attempt/UUID without resetting storage attempts;
  terminal/deleted documents never return to PENDING. Dry-run makes no changes.
- [x] Run `python manage.py test apps.documents.tests.test_outbox_uploads apps.documents.tests.test_outbox_recovery apps.documents.tests.test_background_storage --noinput`
  and record the expected pre-change failures.
- [x] Replace required `.delay`/`on_commit` acceptance publication with enqueue;
  lock event then document, check receipt/attempt, and write receipts with state,
  audit and cleanup messages. Use the strict T17 cleanup adapters. Retain existing
  best-effort after-commit cache/notification callbacks until T20/T21. Schedule
  transient storage retries as new uniquely keyed outbox events, not an unrecorded
  Celery `self.retry` chain. Inject audit/enqueue/receipt failures and verify full
  rollback together; audit consistency is required already in Phase 1.
- [x] Implement stale-publication recovery and legacy seeding with the worker's
  lock order. Run recovery every 60 seconds in the independent relay; wire manual
  commands to the same bounded function and make the old requeue command delegate
  without calling `.delay`. Include basic recovery/legacy rollout instructions.
- [x] Rerun focused/API tests; verify exact uploaded bytes, stable object-key
  compensation, terminal/deleted no-ops and old message compatibility. Extend
  the existing broker-outage integration expectation and corresponding audit
  fixtures to PENDING/recovery now, so later gates do not preserve obsolete FAILED
  behavior. Preserve current WebSocket and versioned-cache regressions.
- [x] Commit as `feat: make document upload attempts durable and idempotent`;
  retain the combined Phase 1 branch for shared user review.

### T19: Durable Cleanup and Phase 1 Acceptance

**Files:** Modify `services.py`, `background.py`, `outbox_handlers.py`,
`apps/documents/tests/test_delete_api.py`, `test_update_api.py`,
`test_background_storage.py`; create `apps/documents/tests/test_outbox_cleanup.py`
and `scripts/outbox_checks.py`. Also modify `scripts/integration_check.py`,
`compose.integration.yaml`, integration safety tests, `README.md`, `CHANGELOG.md`,
`docs/design.md`, `docs/implementation-plan.md`, `docs/tasks.md` and
`docs/release-checklist.md` for the Phase 1 acceptance/rollout gate.

**Interfaces:** Consumes enqueue/strict handlers from T16/T17 and receipts from
T18. Cleanup event payloads snapshot the exact old object/staging keys;
dedupe keys identify the originating event or document version. Existing
best-effort helpers may remain for compensation but never acknowledge delivery.
Produces `run_outbox_checks(*, compose: list[str], phase: str = "core") -> None`
in `scripts/outbox_checks.py`. The `core` profile covers only Phase 1 and calls
the existing harness's private/safe helpers; T23 adds the `full` profile. No Phase
2 module, adapter, command or test is required to deliver the core profile.

- [x] Add failing tests for delete/replacement commit followed by relay crash,
  MinIO outage/recovery, staging outage/recovery, missing-file success and duplicate
  deletion. Assert cleanup messages survive the document row and terminal clearing
  of `staging_key`; rollback preserves referenced objects and emits no cleanup.
- [x] Add races for replacement vs delete and failed-worker compensation vs a
  duplicate worker's committed READY object. Assert a currently owned key is
  never scheduled/deleted as compensation. Reordered cleanup targets only the
  exact historical key and cannot affect the newest replacement.
- [x] Run `python manage.py test apps.documents.tests.test_outbox_cleanup apps.documents.tests.test_delete_api apps.documents.tests.test_update_api --noinput`;
  confirm the missing durable-obligation behavior fails before changes.
- [x] Enqueue committed cleanup atomically in all service/state paths. Preserve
  ownership rechecks and persist compensating cleanup in a separate transaction
  only when ownership is established; preserve/log uncertainty during DB outage.
  Upload before database reference remains a documented orphan window.
- [x] Rerun focused and background-storage tests; prove 204/200 responses remain
  committed while cleanup retries, and no swallowed error marks a message delivered.
- [x] Add core disposable integration checks for seven-service startup, relay
  stop/restart, send-before-finalization crash, concurrent claims, broker outage,
  Redis message loss, worker loss, MinIO cleanup outage/recovery and operator
  status/replay. Extend exact audit expectations and keep all existing API,
  permissions, private-file bytes/expiry, proxy, WebSocket and cache regressions.
- [x] Rehearse additive upgrade from migration 0004 with legacy READY/PENDING
  rows and queued old tasks. Document migrations-before-writers, relay activation,
  legacy recovery, preserved staging, DEAD replay and drain/inspection before
  binary rollback. Do not drop the schema while obligations remain.
- [x] Run the `make test`/`make check` command equivalents in the disposable
  Compose project, warning-free `spectacular --validate --fail-on-warn`, and the
  integration entry point with rebuilt current images and the `core` profile.
  Require passing full regression, no migration/schema warnings, recovery or
  explicit DEAD status in new fault scenarios, and preserved existing volumes.
  Record actual evidence and Phase 1 limits in operator/release docs now.
- [x] Commit durable cleanup/core checks and final evidence; retain the combined
  Phase 1 branch for shared user review. T19 is the Phase 1 gate;
  do not wait for T23 to run these checks or claim Phase 1 completion prematurely.

## Phase 2 — Remaining Side Effects and Operations (Deferred)

Start after the Phase 1 acceptance checkpoint is reviewed. These tasks extend
the working core rather than supplying missing core recovery or verification.

### T20: WebSocket Notification Publication

**Files:** Modify `notifications.py`, `services.py`, `background.py`,
`outbox_handlers.py`, `apps/documents/tests/test_notifications.py`; create
`apps/documents/tests/test_outbox_notifications.py`. Reuse `test_websocket.py`
and `scripts/websocket_checks.py` for protocol/authentication regression.

**Interfaces:** Consumes `document.notification_requested` with event payload
allowlisted to `document.ready` or `document.updated`. Preserve `_send`'s internal
channel event shape and consumer's external `{type, document_id}` frame.
Expose `publish_document_notification(document_id: int, event: str) -> None`
as a strict bounded adapter; route relay delivery through it.

- [ ] Add failing tests for committed internal create/background READY/update
  notifications, rollback/audit failure, Channels error and 2-second timeout,
  retry after recovery, and a crash after send before marking publication.
  Assert current HTTP/worker results survive publication failure.
- [ ] Run `python manage.py test apps.documents.tests.test_outbox_notifications apps.documents.tests.test_notifications --noinput`
  to reproduce missing durable retry before implementation.
- [ ] Replace remaining notification callbacks with transactional enqueue and
  add the strict Channels adapter to the T17 dispatcher; Phase 1 did not implement
  this adapter. Propagate errors to the relay. A redelivered logical upload must not
  create another READY event; publication redelivery may produce duplicate hints.
  Delayed hints for deleted rows are valid; clients refetch and may receive 404.
- [ ] Rerun focused plus `apps.documents.tests.test_websocket`; retain all JWT,
  expiry, Origin and role protections, unchanged frames, and no FAILED/delete hints.
- [ ] Present evidence for shared review, then commit as
  `feat: publish document websocket hints through outbox`.

### T21: Cache Invalidation and Audit Atomicity

**Files:** Modify `services.py`, `background.py`, `caching.py`, `outbox_handlers.py`
and only if necessary `audit.py`; create
`apps/documents/tests/test_outbox_consistency.py`; update
`test_cache_api.py` and `test_audit_api.py`.

**Interfaces:** Consumes the invalidation message's previous `metadata_version`;
the strict adapter deletes exactly that version's key. `record_document_event`
retains its current signature and synchronous transactional semantics.
Audit atomicity already applies in Phase 1; this task expands consistency checks
for the new cache/notification obligations rather than introducing audit safety.

- [ ] Add failing tests for Redis-down mutation and later invalidation, delayed
  previous-version deletion after another update, concurrent cache-fill, and
  delete-before-delivery. Verify DB version checks always yield current metadata.
- [ ] Inject audit-write, enqueue-write and receipt-write errors separately;
  assert document changes, audit and all related messages roll back together.
  Retry/publication failure must not create document storage-failure audits.
- [ ] Run `python manage.py test apps.documents.tests.test_outbox_consistency apps.documents.tests.test_cache_api apps.documents.tests.test_audit_api --noinput`
  and establish the missing-enqueue failures.
- [ ] Replace remaining required invalidation callbacks with enqueue, retain
  metadata version rotation/TTL, add the strict cache adapter to the dispatcher,
  and keep business audit inside the existing
  atomic blocks. Keep request-failure auditing best effort during DB outages.
- [ ] Rerun the focused tests and scan mutation paths for required side effects
  still implemented only with `on_commit`, `.delay`, or error-swallowing delivery.
  Document any remaining compensation-only best-effort paths explicitly.
- [ ] Present evidence for shared review, then commit as
  `feat: make document cache and audit effects transactionally consistent`.

### T22: Advanced Operations, Retention and Orphan Reports

**Files:** Create commands `apps/documents/management/commands/prune_outbox.py`,
`apps/documents/management/commands/report_storage_orphans.py`, and
`apps/documents/tests/test_outbox_maintenance.py`; modify
`apps/documents/outbox_operations.py`, existing `outbox_status.py`,
`config/settings.py`, `.env.example` and `README.md`.

**Interfaces:** Consumes T17's status/replay and T18's recovery without replacing
them. Adds `prune_completed_messages(*, before: datetime, limit: int,
dry_run: bool = True) -> int` and
`report_storage_orphans(*, before: datetime, limit: int) -> list[dict]` to
`outbox_operations.py`. Extend `get_outbox_status` with detailed per-kind/state
reporting and sanitized failure/age breakdowns; retain the basic fields.

- [ ] Add failing retention tests: keep pending/leased/DEAD/unreceipted/active-upload
  history; prune only completed effects older than 30 days and keep receipt
  retention consistent. Dry-run changes nothing; pruning races with replay,
  processing and publication must preserve active work and receipts.
- [ ] Add failing orphan-report tests: consider only candidates older than 24
  hours, check current references and active upload/cleanup obligations, cap scans
  and never delete uncertain files. Detailed status must cover the new Phase 2
  kinds without exposing payloads, filenames or private exception strings.
- [ ] Run `python manage.py test apps.documents.tests.test_outbox_maintenance --noinput`
  and confirm the new maintenance/reporting behavior fails before implementation.
- [ ] Implement dry-run-first bounded pruning, conservative read-only reporting
  and expanded operational status. Phase 1 status/replay, every-60-second upload
  recovery and legacy seeding already work and remain required regressions.
- [ ] Rerun maintenance, operations and recovery tests; verify commands against a
  disposable dataset with active leases. Document retention, storage-disk/backlog
  monitoring and manual orphan investigation. No automatic orphan deletion.
- [ ] Present evidence for shared review, then commit as
  `feat: extend outbox operations and maintenance reporting`.

### T23: Whole-System Integration and Delivery Documentation

**Files:** Modify `scripts/outbox_checks.py` created in T19, `scripts/integration_check.py`,
`scripts/websocket_checks.py`, `compose.integration.yaml`, relevant integration
safety tests, `README.md`, `CHANGELOG.md`, `docs/design.md`,
`docs/implementation-plan.md`, `docs/tasks.md`, `docs/release-checklist.md`.

**Interfaces:** Consumes all prior tasks. Extend T19's
`run_outbox_checks(*, compose: list[str], phase: str = "core") -> None` with a
`full` profile that includes core scenarios plus notifications/cache/maintenance.
Both profiles remain usable. Reuse the harness's private credentials/channel
isolation without touching the user's running stack or volumes.

- [ ] Add failing full-profile scenarios for Channels publication timeout/outage
  and later delivery, delayed cache invalidation, replay/prune races and detailed
  operational reporting. Keep T19's seven-service relay/broker/worker/cleanup fault
  checks in both profiles; they are already required for Phase 1 delivery.
- [ ] Run focused harness/safety tests first; distinguish intentional new behavior
  from an unrelated failure. Retain the Phase 1 broker-down PENDING/recovery
  expectations and extend exact scenario-based audit outcomes for Phase 2; keep
  all existing role, bytes, expiry, proxy and WebSocket checks.
- [ ] Rehearse upgrade from the delivered Phase 1 with pending/leased/published
  upload/cleanup messages, then activate Phase 2 producers after their adapters
  are deployed. Verify new-kind drain/inspection before rollback to Phase 1, and
  retain T19's upgrade-from-0004/legacy-task rehearsal without lost staging.
- [ ] Run `make test`, `make check`,
  `docker compose exec -T backend python manage.py spectacular --validate --fail-on-warn --file /tmp/openapi.yaml`,
  and `make integration` with the `full` profile using rebuilt current images.
  Require all tests to pass, no missing migrations/schema warnings, all new fault scenarios to recover or
  remain explicitly DEAD/replayable, and disposable cleanup to preserve existing volumes.
- [ ] Update docs with actual evidence, remaining pre-transaction orphan/client
  receipt limits, operational commands, and reviewed task states. Review the
  whole feature and address blocking findings before proposing delivery.
- [ ] Present the verified diff/evidence for shared review, then commit as
  `test: verify transactional outbox end to end`. Create a draft PR only during
  an authorized implementation/delivery phase; merging/tagging/deployment retain
  separate authorization.

## Planning Self-Review

- Phase 1 maps to T16–T19, including status/replay in T17, completion/recovery in
  T18, and its own deployment/integration gate in T19. No core requirement waits
  for Phase 2. T20–T23 extend the remaining effects, operations and verification.
- Interfaces and payload names above match the spec; output fields do not extend
  the public API or WebSocket protocol.
- Each Review Focus condition has a targeted owning task, in addition to integration.
- Proposed defaults are explicit; four attempts means persisted logical attempts,
  and PUBLISHED means destination acceptance rather than user-visible completion.
- Task queue/checklist records verified implementation separately from pending
  branch review. Phase 2 remains deferred; no merge is authorized.
