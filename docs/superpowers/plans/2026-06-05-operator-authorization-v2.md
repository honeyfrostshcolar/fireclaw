# Operator Authorization v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gateway-level operator approval and cancellation authorization for safety-sensitive FireClaw tasks.

**Architecture:** Extend `control.py` with risk-aware authorization decisions and approval requests. Store pending approvals in `FireClawGateway`, keyed by session, and enforce them in `/confirm` and task cancel endpoints while preserving the existing agent confirmation path.

**Tech Stack:** Python dataclasses, datetime, existing Gateway HTTP tests, pytest.

---

### Task 1: Risk-Aware Control Decisions

**Files:**
- Modify: `src/fireclaw_core/control.py`
- Test: `tests/test_control.py`

- [ ] Add tests for `approval_required` when operator approves high risk without `safety.override`.
- [ ] Implement `AuthorizationRequest` and `ControlPolicy.evaluate_risk(...)`.
- [ ] Run targeted tests.

### Task 2: Gateway Pending Approval

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Test: `tests/test_gateway.py`

- [ ] Add tests for high-risk task creating `authorization.requested`.
- [ ] Add tests that supervisor `/confirm` records `authorization.approved`.
- [ ] Add tests that expired approval denies `/confirm`.
- [ ] Implement pending approval storage and confirmation enforcement.
- [ ] Run targeted tests.

### Task 3: Cancel Authorization

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Test: `tests/test_gateway.py`

- [ ] Add tests that observer cannot cancel active tasks.
- [ ] Implement operator-aware `cancel_task(...)`.
- [ ] Wire HTTP `/tasks/<id>/cancel` operator payload.
- [ ] Run targeted tests.

### Task 4: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Create: `memory/2026-06-05/fireclaw-operator-authorization-v2.md`

- [ ] Document Operator Authorization v2 behavior.
- [ ] Record commands and known gaps in memory.
- [ ] Run `./.venv/bin/python -m pytest -q`.
