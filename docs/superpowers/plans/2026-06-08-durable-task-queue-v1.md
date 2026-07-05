# Durable Task Queue v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable Gateway task queue/registry with idempotent submission and fail-safe restart reconciliation.

**Architecture:** Create a focused `fireclaw_core.task_queue` module with JSONL append-only records and compacted read APIs. Wire Gateway task submission, cancellation, terminal recording, state, and trace to the queue while preserving the existing event ledger as the audit stream. On Gateway initialization, mark stale non-terminal queue records as `lost` instead of replaying robot actions.

**Tech Stack:** Python dataclasses, JSONL files, `threading.Lock`, existing `GatewayConfig`, pytest.

---

### Task 1: Durable Queue Store

**Files:**
- Create: `src/fireclaw_core/task_queue.py`
- Create: `tests/test_task_queue.py`

- [x] Write failing tests for queue append/update, terminal filtering, and dedupe lookup.
- [x] Implement `TaskQueueRecord`, `JsonlTaskQueue`, and status helpers.
- [x] Run `./.venv/bin/python -m pytest tests/test_task_queue.py -q`.

### Task 2: Gateway Queue Wiring

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`

- [x] Write failing Gateway tests for accepted/running/terminal queue states.
- [x] Add `GatewayConfig.task_queue_path`.
- [x] Initialize `JsonlTaskQueue` in `FireClawGateway`.
- [x] Write queue state on submission, worker start, and terminal result/failure.
- [x] Run targeted Gateway tests.

### Task 3: Idempotent Submission and Cancellation State

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`

- [x] Write failing test for duplicate non-terminal `dedupe_key`.
- [x] Parse optional `dedupe_key` from `POST /tasks`.
- [x] Return existing task for non-terminal duplicate dedupe keys.
- [x] Write failing test for cancel updating queue state.
- [x] Mark queue status `cancel_requested` when cancellation is accepted.
- [x] Run targeted Gateway tests.

### Task 4: Restart Reconciliation and API Projection

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`
- Modify: `README.md`
- Modify: `memory/2026-06-08/fireclaw-work-resume.md`

- [x] Write failing test proving Gateway startup marks previous non-terminal records as `lost`.
- [x] Add startup reconciliation and `task.lost` audit event.
- [x] Add `queue` summary to `/state` and `queue_record` to task trace.
- [x] Document durable queue behavior and fail-safe non-replay policy.
- [x] Record implementation notes and verification results in memory.
- [x] Run `./.venv/bin/python -m pytest -q`.
