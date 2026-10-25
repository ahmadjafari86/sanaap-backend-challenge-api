# Outbox App Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Separate outbox infrastructure from document rules to simplify maintenance and debugging without changing observable behavior.

**Architecture:** `apps.outbox` owns persistence and reliable delivery. `apps.documents.outbox` provides explicit event validators, handlers, recovery, and status callbacks registered from `DocumentsConfig.ready()`. Keep the current document ID snapshot and command contracts rather than generalizing the event schema.

**Tech Stack:** Existing Django 5.2, PostgreSQL 17, Celery 5.6, Redis, Channels, and MinIO; no new dependencies.

**Spec:** [Approved design](../specs/2026-10-25-outbox-app-extraction-design.md).

## Global Constraints

- This is a structural refactor. Keep current Phase 1 scope and HTTP/WebSocket/task/command contracts.
- Document changes, audit writes, receipts, and emitted messages retain their current transaction boundaries on the same database.
- Preserve UUID identities, payloads, retry limits, lease fencing, sanitized errors, and cleanup ownership checks.
- The outbox app never imports `apps.documents` directly.
- Registration performs no database queries and starts no workers.
- Rebuild initial migrations in `accounts`, `documents`, and `outbox`; do not reset existing user databases or volumes.
- No merge into `develop`/`main`, deployment, or Phase 2 implementation.

## Review Focus

- Repeated Django initialization: identical registration remains harmless; conflicting registrations fail clearly (Task 1).
- Unknown kinds and reserved Phase 2 kinds: invalid input becomes DEAD and never invokes external I/O (Tasks 1 and 2).
- Moving commands: relay still runs bounded upload recovery, updates its heartbeat, and status retains upload age information (Task 2).
- Moving models: messages survive document deletion and mutation/receipt rollback remains atomic (Tasks 2 and 3).
- Moving Python paths: task publication, mock targets, logger names, and container fault probes resolve the final modules (Tasks 2 and 3).

## File Map

- Create `apps/outbox/{__init__,apps,models,registry,validation,services,delivery,operations}.py` and `migrations/0001_initial.py`.
- Move generic commands to `apps/outbox/management/commands/{run_outbox_relay,outbox_status,replay_outbox}.py`.
- Replace `apps/documents/outbox.py` with `apps/documents/outbox/{__init__,events,handlers,uploads,recovery,operations}.py`.
- Move `outbox_delivery.py` and `outbox_operations.py` infrastructure into the new app; move domain handlers/uploads/recovery into the document package.
- Modify document `apps.py`, `models.py`, `services.py`, `background.py`, `tasks.py`, and recovery command imports.
- Create `apps/outbox/tests/{__init__,test_registry,test_models,test_delivery,test_operations}.py`; retain document-specific scenarios in the document suite.
- Update `config/settings.py`, initial migrations, `scripts/outbox_checks.py`, `README.md`, and superseded architecture notes.

### Task 1: Explicit Adapter Registration

**Files:** `apps/outbox/registry.py`, package initializers, `apps/outbox/tests/test_registry.py`.

**Interfaces:**
- Produce `Registry` and one module-level `registry` instance.
- `register_event(kind: str, *, validator, handler=None) -> None`; validators receive `document_id` and `payload` keyword arguments; handlers receive a delivery claim.
- `validate_event(*, kind, document_id, payload) -> None` and `dispatch(claim) -> None`.
- `register_adapter(name: str, *, recover, status) -> None`; recovery callbacks accept `now`/`limit`, status callbacks accept `now`.
- `run_recovery(*, now, limit) -> int` and `get_status(*, now) -> dict`; dispatch/validation reject unknown kinds with Django `ValidationError`, and absent handlers reject reserved destinations.

- [x] Write focused registry tests using a fresh registry per test: valid dispatch calls its validator and handler once; invalid/unknown events never reach the handler; handler-less reserved events fail delivery.
- [x] Add checks for identical repeated registration, conflicting registration raising `ImproperlyConfigured`, callback forwarding, and status extensions without exposing payloads.
- [x] Run `uv run --offline python manage.py test apps.outbox.tests.test_registry --noinput`; confirm the tests fail because the registry is absent. If the host environment is unavailable, use the project's backend image with the same Django test labels.
- [x] Implement the registry with ordinary mappings and explicit callbacks; no plugin discovery, abstract base classes, database access, or background startup.
- [x] Run the focused tests again and confirm they pass. Review the diff and record the task checkpoint.

### Task 2: Extract Persistence, Relay, and Document Adapters

**Files:** Remaining new outbox modules/commands, document outbox package, document callers/tests, `config/settings.py`, and all three apps' initial migrations.

**Interfaces:**
- Consume Task 1 registration and callback interfaces.
- Preserve `enqueue_message(*, kind, document_id, payload, dedupe_key, available_at=None) -> OutboxMessage` in `apps.outbox.services`.
- Preserve `validate_message(*, kind, document_id, payload, schema_version=1)` in `apps.outbox.validation`; common envelope checks delegate document payload checks to the registered validator.
- Preserve `DeliveryClaim`, `claim_batch(*, now, limit)`, `dispatch_message(claim)`, `relay_once(*, now=None, batch_size=None)`, and `_finish(claim, *, now, error=None)` in `apps.outbox.delivery`.
- Preserve `get_outbox_status(*, now)` and `replay_dead_messages(*, event_ids, now)` in `apps.outbox.operations`; merge the registered document status callback into generic status.
- Preserve `resolve_upload_message`, `process_outbox_upload`, `_process_upload`, and `recover_uploads` signatures in `apps.documents.outbox.uploads/recovery`.
- Define document event constants and payload validation in `apps.documents.outbox.events`; keep all five current event strings and only the three active Phase 1 delivery handlers.

- [x] Move/adapt the existing behavioral tests to final imports and add model-ownership assertions: both outbox models have app label `outbox`; the document app owns only document/audit models.
- [x] Run the targeted tests against PostgreSQL and confirm they fail for missing final modules or model ownership. Record any baseline environment limitation separately from a test failure.
- [x] Move models, enqueue, delivery, and generic operations. Keep `document_id` as a nullable scalar snapshot; remove domain-specific kind choices from the generic model and use registered validation in `clean()`.
- [x] Move domain adapters into the document package. Put upload status-age queries in its `operations.py`; register validators, handlers, recovery, and status from `DocumentsConfig.ready()` without external I/O.
- [x] Move general commands. Replace their direct upload imports with registered callbacks while retaining current output, periodic recovery, error handling, SIGTERM behavior, and heartbeat settings.
- [x] Update every production/test import, patch target, and logger expectation. Delete superseded modules rather than adding compatibility re-exports; keep the existing Celery task name and recovery command alias.
- [x] Replace checked-in first-party migration histories with fresh generated `0001_initial.py` files using `uv run --offline python manage.py makemigrations accounts documents outbox`. Preserve the custom user manager, role field, indexes, model defaults, and the swappable user dependency.
- [x] Run `uv run --offline python manage.py check` and `uv run --offline python manage.py makemigrations --check --dry-run`.
- [x] Run `uv run --offline python manage.py test apps.outbox apps.documents.tests.test_outbox_uploads apps.documents.tests.test_outbox_recovery apps.documents.tests.test_outbox_cleanup apps.documents.tests.test_background_storage --noinput` against PostgreSQL, using the backend-image alternative if necessary. Confirm existing rollback, duplicate-delivery, ownership, lease, retry, recovery, and command tests pass.
- [x] Review the task diff and record its checkpoint.

### Task 3: Integration, Documentation, and Final Verification

**Files:** `scripts/outbox_checks.py`, `README.md`, the prior outbox spec, and any remaining test references.

**Interfaces:** Consume Task 2 final module paths and unchanged management-command contracts; keep `scripts/integration_check.py` isolation rules and fault-probe behavior.

- [x] Update container-side probe code to import models from `apps.outbox.models` and delivery functions from `apps.outbox.delivery`; retain real duplicate delivery, broker-loss recovery, cleanup retry, receipts, and audit assertions.
- [x] Document app responsibilities and the validator/handler/recovery registration entry point. Replace additive-upgrade instructions for this assessment with fresh-database setup guidance; preserve historical task records and mark the prior app-placement/migration decisions as superseded.
- [x] Run `uv run --offline python manage.py test --noinput` against PostgreSQL and confirm the full regression suite passes.
- [x] Run `python3 scripts/integration_check.py`; confirm fresh startup/migrations, warning-free schema validation, full regression, and real-service outbox fault probes pass. The harness must clean only its disposable project resources.
- [x] Run `git diff --check` and search `apps/outbox` for direct `apps.documents` imports; confirm none remain in production outbox modules. Search executable source for the removed module paths and confirm no callers remain.
- [x] Use `superpowers:requesting-code-review` for a final review of the whole refactor; resolve findings and rerun only affected checks.
- [x] Use `superpowers:verification-before-completion` before reporting completion. Update this plan with actual test results and remaining limitations, and present the final diff in the app.

## Execution Notes

Use the project's existing runtime and credentials without printing `.env`.
If a command is blocked by sandbox/network permissions, request the command's
required escalation instead of silently skipping verification. Do not apply the
rewritten migrations to the current working database; fresh migration validation
belongs to the disposable integration stack.

Each task checkpoint is reviewed before proceeding. Follow the repository and
user's integration preferences when committing; keep all work on a feature branch
and leave `develop`/`main` untouched.

## Plan Self-Review

Checked the plan against the approved responsibilities, migration permissions,
behavioral constraints, test movement, documentation scope, and Phase 1 limits.
The registry callback signatures match their consumers in Tasks 2 and 3. Each
review-focus condition has an existing behavioral test or a focused new wiring
test; no new framework or external dependency is required.

## Execution Progress — 2026-10-25

- User approved direct execution in this chat with one independent final review.
- Worked in the existing `codex/transactional-outbox-phase1` feature checkout; the initial handoff was uncommitted for inspection. The user subsequently requested committing the completed refactor. Main/develop are untouched.
- Runtime: installed the locked Python dependencies with `uv`, then used `.venv/bin/python` directly.
- Baseline: **334 PostgreSQL tests passed** before the refactor.
- Task 1: six registry tests first failed for the missing registry, then passed.
- Task 2: model/command ownership assertions first failed, then passed; **90 focused PostgreSQL tests passed**.
- Django checks and migration drift checks pass; all three first-party apps have fresh initial migrations.
- Full local regression: **343 PostgreSQL tests passed**. Rebuilt Docker image regression also passes **343 tests**.
- Production outbox modules have no document imports; removed Python paths have no executable callers.
- Disposable integration project `sanaap-t09-2dcf405af8` passed fresh migrations, all **343** rebuilt-image tests, schema/Nginx validation, API/WebSocket/storage contracts, and every Phase 1 outbox fault probe. Its resources were removed; all pre-existing volumes were preserved.
- Final independent read-only review found **no Critical, Important, or Minor issues**. No code fixes were required.
- Final full PostgreSQL regression after review: **343/343 passed in 10.457 seconds**. Django checks, migration drift, Python compilation, import-boundary checks, and `git diff --check` pass.
- The review panel was requested in Codex. No merge, push, release, or existing-database reset was performed.

## Execution Decisions and Limits

| Decision | Reason and practical cost |
| --- | --- |
| Use the existing feature checkout | Follows the approved direct execution in this chat; the diff appears on `codex/transactional-outbox-phase1` rather than in another worktree. |
| Commit after user review | The initial handoff was uncommitted and the independent reviewer inspected all working-tree changes; the user then explicitly requested a commit on the same feature branch. |
| Rebuild migration histories | Explicitly authorized for this assessment; older databases require a fresh setup. |
| Keep Phase 2 inactive | Preserve the approved delivery scope; durable cache/WebSocket publication remains future work. |
| Keep recovery bounded per adapter with one documents adapter | Avoid a multi-domain messaging framework; adding domains later requires revisiting aggregate limits/fairness. |
| Preserve existing upload/recovery policies | This is a structural refactor; documented external-write/orphan limitations remain unchanged. |

All reviewer exclusions were assessed against the approved scope and are covered
by these decisions. No deferred minor findings remain.
