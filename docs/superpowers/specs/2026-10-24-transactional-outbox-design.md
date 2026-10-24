# Transactional Outbox — Proposed Design

**Status:** The user requested two delivery phases, starting with T16–T19.
Implementation has not started. Detailed mechanisms and defaults below remain
proposals to verify during task review.
**Scope:** Phase 1 delivers upload and cleanup reliability with essential
operations, recovery and integration checks. Phase 2 extends the same outbox to
WebSocket publication, cache invalidation and advanced operations.
**Task queue:** [T16–T23](../../tasks.md#transactional-outbox--t16t23).
**Implementation plan:** [2026-10-24-transactional-outbox.md](../plans/2026-10-24-transactional-outbox.md).

## Intent and Current Behavior

Persist the obligation to perform a side effect in the same PostgreSQL
transaction as the document mutation. Deliver it after commit with bounded
attempts, observable failures, and explicit replay. A process crash must not
silently erase a committed obligation.

Today, `background.stage_document` registers a Celery publication with
`transaction.on_commit`; publication failure marks the accepted document FAILED
and attempts staging deletion. `services` and `background.transition_document`
use after-commit callbacks for cleanup, invalidation and notifications. Cleanup
and notification errors are logged and swallowed. Recovery republishes PENDING
document IDs manually. These behaviors leave a commit-to-callback crash window.

## Approaches

| Approach | Tradeoff | Decision |
| --- | --- | --- |
| Poll PostgreSQL outbox with an independent relay; retain Celery/Redis | Additional small service and table; explicit retry/recovery without another messaging platform | Recommended basis for this task breakdown |
| Periodically republish PENDING documents using the existing command | Small upload-only change; no independent durable cleanup, notification or invalidation obligations | Does not cover the requested scope |
| CDC-based publication | Potentially lower polling overhead; additional connector/deployment/recovery complexity | Defer unless future volume justifies it |

## Delivery Phases

### Phase 1 — Uploads and Cleanup (T16–T19, next for implementation)

- T16: Outbox schema, receipts and transactional enqueue/deduplication.
- T17: Independent relay, upload/cleanup adapters, retry/lease recovery and basic
  `outbox_status`/explicit `replay_outbox` commands.
- T18: Upload acceptance and receipts, durable storage retries, automatic stale
  upload recovery, legacy PENDING seeding and compatibility with queued old tasks.
- T19: Durable object/staging cleanup, safe compensation and the phase's full
  regression, real-service integration and rollout/operator documentation gate.

Phase 1 is independently deliverable. It has no dependency on T20–T23. Basic
status/replay moved from T22 to T17, upload recovery moved from T22 to T18, and
core integration/rollout checks moved from T23 to T19. All business audit writes
stay transactional from the beginning; that requirement is not deferred to T21.
Existing versioned-cache invalidation and WebSocket callbacks remain best effort
throughout Phase 1, including READY transitions. Do not enqueue cache/notification
messages or implement their delivery adapters until Phase 2.

### Phase 2 — Remaining Side Effects and Operations (T20–T23, deferred)

- T20: Durable READY/update hint publication through Channels.
- T21: Durable previous-version cache invalidation and expanded consistency tests.
- T22: Detailed operational reporting, safe retention and read-only orphan reports.
- T23: Whole-scope integration, Phase 1-to-Phase 2 upgrade checks and final docs.

Phase 2 starts after the Phase 1 checkpoint is reviewed. Its final verification
extends the checks already required by T19; it is not the first verification of
the core outbox.

## Constraints and Delivery Boundaries

- Retain Django 5.2, PostgreSQL 17, Celery 5.6, Redis, Channels and private MinIO;
  introduce no Kafka, CDC connector, Celery Beat, or result backend.
- Preserve roles, JWT/Origin checks, private storage, 10 MiB JPEG/PNG validation,
  download URL lifetime of 60 seconds, metadata cache TTL of 60 seconds, and
  synchronous document update behavior.
- Preserve POST 202, update 200, delete 204 and PENDING/READY/FAILED public states.
  Broker failure leaves an accepted upload PENDING and retains its staging file.
- Four persisted logical storage attempts remain the maximum, matching the
  existing contract. An interrupted MinIO call whose database transaction rolls
  back can be repeated as the same logical attempt; physical calls across crashes
  are not bounded to four. Broker and database failures do not consume an attempt
  unless the corresponding storage attempt is committed. Missing staging is a
  terminal storage failure.
- Preserve WebSocket frames `{type, document_id}` and the existing
  `document.ready`/`document.updated` types. FAILED and deletion do not emit hints.
- Guarantee durable publication obligations and replayable failures for the
  active phase's kinds. Channels
  acceptance is not proof of client receipt. Disconnected-client replay, client
  acknowledgements and strict event ordering are not introduced; clients refetch
  authoritative REST metadata and tolerate duplicate or delayed hints.
- Audit remains synchronous PostgreSQL data in the mutation transaction. Outbox
  is not an audit store and does not replace `DocumentAuditEvent`.
- Payloads/logs must not contain file bytes, JWTs, credentials, presigned URLs,
  document titles or original filenames. Internal object/staging keys and version
  UUIDs may be stored in restricted payloads where required for cleanup.
- No cascading foreign key from an outbox message to a deletable document.
  Deletion must preserve its cleanup obligations.

## Data and Interfaces

Keep the new infrastructure within `apps/documents`, matching this repository's
single domain. Add `OutboxMessage` and `OutboxReceipt` to `models.py` with migration
`0005_transactional_outbox.py` (recheck migration numbering at implementation).

`OutboxMessage` fields: UUID `id`, unique `dedupe_key`, `kind`, `schema_version`
(initially 1), nullable document ID snapshot, JSON `payload`, `status`, publication
`attempts`, `available_at`, nullable `lease_token`/`locked_until`, nullable
`published_at`, sanitized `last_error_code`, `created_at` and `updated_at`.
Statuses are PENDING, PROCESSING, PUBLISHED and DEAD. Add indexes for due work,
expired leases and stale upload publication. Validate known version/kind/payload
shapes before executing any handler; unknown or malformed messages become DEAD.

`OutboxReceipt` records a unique event UUID and processing timestamp for a
completed upload attempt. A receipt is committed with document changes and any
follow-up messages. It does not claim that external I/O is exactly once.

| Kind | Payload | Destination | Phase |
| --- | --- | --- | --- |
| `document.upload_requested` | `{attempt_number}` plus document ID snapshot | Celery `store_document(document_id, event_id)` | 1 |
| `storage.object_delete_requested` | `{object_key}` | Strict MinIO deletion handler | 1 |
| `storage.staging_delete_requested` | `{staging_key}` | Strict staging deletion handler | 1 |
| `document.metadata_invalidate_requested` | `{metadata_version}` plus document ID snapshot | Strict cache deletion handler | 2 |
| `document.notification_requested` | `{event}` plus document ID snapshot | Strict Channels publication handler | 2 |

T16 may reserve all five kind/schema definitions. Phase 1 writers emit only its
three active kinds, and its dispatcher implements only those destinations.

`outbox.py` owns `enqueue_message(*, kind, document_id, payload, dedupe_key,
available_at=None) -> OutboxMessage`. Call it inside an existing atomic block;
validate payloads and return the existing matching record on an identical
dedupe key. Reuse with different content is an error. Do not suppress database
errors: the corresponding mutation must roll back if its obligation cannot save.

## Relay and Retry

`outbox_delivery.py` owns a `DeliveryClaim` value containing event ID, payload,
kind, document ID and lease token, `claim_batch(*, now, limit)`,
`dispatch_message(claim)` and `relay_once(*, now=None) -> int` (attempted count).
`run_outbox_relay` runs it independently of Celery, supports `--once` and bounded
batch/poll options, and exits cleanly on SIGTERM. No import starts a relay.

Claim committed due messages or expired leases under
`select_for_update(skip_locked=True)` in a short transaction. Release database
locks before broker/Channels/cache/MinIO/filesystem I/O. Mark success or failure
only with a conditional update matching PROCESSING and the current lease token.
A stale publisher cannot overwrite another claimant or a recovery transition.
Bound each network operation; expired claims may be delivered again.

Proposed settings: poll 1 second, batch 10, lease 60 seconds, publication maximum
20 attempts, exponential retry starting at 2 seconds capped at 300 seconds with
0–20% positive jitter. Exhausted publication or permanent payload failure sets
DEAD and retains data for operator replay. Upload exhaustion leaves the document
PENDING/staging intact; only storage processing changes it to FAILED.

Use the backend image in a dedicated `outbox-relay` Compose service. Start after
migrations, mount staging when needed by cleanup, expose no ports, and do not
require Redis/MinIO health for the relay loop to keep running after startup.
Add a relay-loop heartbeat and health check, separate from backlog age metrics.
Integrate it into both disposable Compose configuration and safety allowlists.

## Upload Attempts and Duplicate Safety

Initial acceptance writes Document + acceptance audit + upload attempt 1 message
atomically after staging bytes. There is no required publication callback.
An optional after-commit wakeup can only be a latency optimization.

Publish the event UUID with the Celery message. Celery `task_id` alone does not
deduplicate. For outbox-aware workers, lock the message row, then the document,
using the same order in worker/recovery paths. Check the receipt and expected
attempt number before executing storage. Terminal/deleted documents are safe
no-ops that receive a receipt; duplicate/completed attempts do not consume the
storage-attempt budget or duplicate audit/notification events.

One worker invocation executes one logical storage attempt. On a transient
failure, atomically persist the attempt count, the current event's receipt, and
the next uniquely keyed upload attempt with a delay; retain staging. On READY
or terminal FAILED, atomically persist the state, one storage audit, the receipt,
and cleanup obligations. Phase 2 additionally persists invalidation/notification
obligations; Phase 1 retains their existing after-commit callbacks. Do not rely on
`self.retry` publication for durable storage retry scheduling. Database rollback leaves the
current attempt without a receipt so it can be recovered.

PUBLISHED means only that publication returned successfully. Periodically rearm
published upload events older than 300 seconds without a receipt, under row locks
and conditional updates. This closes Redis message loss and interrupted-worker
recovery windows; it does not create a new logical storage attempt. Also seed a
deduplicated current-attempt message for legacy PENDING rows without work.
Never resurrect FAILED/deleted rows or reset persisted storage attempts.

Support already queued `store_document(document_id)` messages during rollout:
resolve the appropriate current outbox attempt first and use the same receipt
path. Recovery and live processing must not create competing logical attempts.

## Cleanup, Cache, Audit and Notifications

- Snapshot the exact replaced/deleted object key and staging key in transactional
  cleanup messages before losing the reference. Missing files are successful
  deletion. Strict adapters propagate failures to the relay; existing helpers
  that swallow failures must not be used to decide successful delivery.
- Capture staging deletion on both READY and FAILED even after `staging_key` is
  cleared. Cleanup replay must work after document deletion and outbox reordering.
- Retain ownership checks for failed-upload compensation so another worker's
  committed object is never deleted. Best-effort rollback compensation should
  persist a cleanup obligation in a new transaction only when ownership can be
  established; a PostgreSQL outage cannot make that persistence atomic.
- In Phase 2, cache invalidation messages identify the previous metadata version.
  Keep database-backed version rotation as the freshness mechanism, so delayed or
  duplicate invalidation cannot delete current-version entries or serve stale data.
- Successful audit, document mutation, receipts and emitted messages either
  commit together or roll back together. Infrastructure publication failures do
  not create false document storage-failure audits. Request-failure audits retain
  their existing best-effort behavior during database unavailability.
- In Phase 2, enqueue READY/update hints for background transitions and the
  existing internal synchronous create/update paths. Publication adapters must surface
  timeouts/errors to the relay while HTTP/worker success remains committed.

## Operations, Rollout and Retention

Phase 1 provides `outbox_status` (state counts, oldest due/unreceipted age, expired
leases), `replay_outbox` (explicit event IDs, DEAD records only) in T17 and
`recover_outbox_uploads` (stale publication/legacy seeding, dry-run and bounded
batch) in T18. Make the existing `requeue_pending_documents` command delegate to
durable recovery, never `.delay`. `outbox_operations.py` owns status/replay;
`outbox_recovery.py` owns automatic and manual upload recovery.

Phase 2 T22 extends operational reporting and adds `prune_outbox` (dry-run
default) and read-only `report_storage_orphans`. It must not be the first
implementation of status, explicit replay or periodic recovery.

Run recovery periodically in the independent relay process, every 60 seconds by
default; manual commands must call the same bounded functions. Queue growth,
DEAD counts, oldest age, lease recovery and sanitized failure codes must be
visible in structured logs/status. Limit command output and do not dump payloads.

Phase 1 retains messages and receipts without automatic pruning. Phase 2 retains
delivered records for at least 30 days. Prune only completed direct
effects and receipted upload attempts belonging to terminal/deleted documents;
never prune pending, leased, DEAD, unreceipted or active-upload history. Preserve
receipts for the same retention horizon. Never auto-replay DEAD messages forever.

Outbox does not make pre-transaction filesystem/MinIO writes atomic. A crash
before recording a new staging/object reference can still leave an orphan.
Phase 2 includes a bounded, read-only orphan report with an age threshold of 24 hours,
checking current document references and active cleanup/upload obligations.
Report uncertain candidates for manual investigation; this change does not add
automatic deletion based only on an absent reference. Automatic orphan removal
would require a separate durable write-intent/ownership design.

Deploy additive migrations before outbox-aware writers/workers, stop legacy
writers before declaring coverage, then enable the relay and recover legacy
PENDING rows. Preserve staging volumes. Drain/inspect pending messages before
any rollback to legacy binaries; do not reverse/drop the schema with work present.
T19 verifies and documents this rollout for Phase 1. T23 extends it to Phase 2
message producers/adapters; core deployment does not wait for T23.
No new release tag, production deployment, merge or GitHub issue creation is
part of the requested task breakdown.

## Acceptance and Task Mapping

| Task | Phase | Required evidence |
| --- | --- | --- |
| T16 | 1 | Additive migration; enqueue/dedupe/rollback and deletion independence |
| T17 | 1 | PostgreSQL claims, lease fencing, retry/DEAD, bounded publication, basic status and explicit replay |
| T18 | 1 | Broker-down PENDING, receipts, persisted attempt budget, periodic stale/legacy recovery and queued legacy tasks |
| T19 | 1 gate | Durable cleanup/ownership races plus seven-service core integration, full regression and safe rollout |
| T20 | 2 | READY/update after commit only, retry after Channels failure, authentication/protocol compatibility |
| T21 | 2 | Versioned-cache delivery and expanded audit/mutation/outbox atomicity checks |
| T22 | 2 | Detailed operations, safe retention and conservative orphan report; existing core recovery remains passing |
| T23 | 2 gate | Whole-scope integration, Phase 1 upgrade, rollout/rollback/operator docs and full regression |

Implement and verify Phase 1 tasks first, with the existing shared-review
checkpoint between tasks and a phase acceptance gate at T19. Phase 2 is deferred
until the Phase 1 checkpoint is reviewed. Design mechanisms can be refined at
review without changing these delivery boundaries.

Builder check found one material ambiguity: whether four attempts meant physical
MinIO calls across crashes or committed attempts. The proposal preserves the
repository's four persisted attempts; interrupted calls can repeat safely under
the stable-key/ownership rules. This clarification needs no new product feature.
