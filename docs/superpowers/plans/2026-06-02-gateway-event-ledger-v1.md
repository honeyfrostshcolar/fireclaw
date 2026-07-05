# Gateway Event Ledger v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add task ids and append-only event traces to FireClaw Gateway.

**Architecture:** Add `EventLedger` JSONL store; Gateway creates a task id per task/confirm/cancel command, derives events from agent results, and exposes task/event query endpoints.

**Tech Stack:** Python 3.11, JSONL, standard library HTTP server, pytest.

---

### Task 1: Event Ledger Store

**Files:**
- Add: `src/fireclaw_core/event_ledger.py`
- Test: `tests/test_event_ledger.py`

- [x] **Step 1: Write failing event ledger tests**

Test append/list, task filtering, session filtering, and latest ordering.

- [x] **Step 2: Implement EventLedger**

Add append, list, latest, events_for_task, and latest_for_session helpers.

- [x] **Step 3: Run event ledger tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_event_ledger.py -v
```

### Task 2: Gateway Task IDs and Events

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Test: `tests/test_gateway.py`

- [x] **Step 1: Write failing task_id/event endpoint tests**

Assert `POST /tasks` includes `task_id`; `/tasks/<task_id>` and `/tasks/<task_id>/events` return trace data.

- [x] **Step 2: Write failing recent events test**

Assert `/events/recent?session_id=...` returns recent task events.

- [x] **Step 3: Write failing confirmation event test**

Assert pending and confirm flows emit confirmation events.

- [x] **Step 4: Implement Gateway event recording and endpoints**

Add event ledger path/config, event derivation, and HTTP route handling.

- [x] **Step 5: Run gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py -v
```

### Task 3: Docs and Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Document event endpoints**

Add `task_id`, task trace, and recent events examples.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
```

- [x] **Step 3: Run manual Gateway event demo**

Start Gateway, submit task, query `/tasks/<task_id>/events`, then stop Gateway.

- [x] **Step 4: Update memory and mark plan complete**
