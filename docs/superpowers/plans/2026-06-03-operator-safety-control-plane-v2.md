# Operator Safety Control Plane v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first FireClaw operator/control-plane skeleton: operator identity, control policy decisions, and Gateway audit events for task submission.

**Architecture:** Create a focused `control.py` module with `OperatorContext`, role-to-scope defaults, and `ControlPolicy`. Then wire Gateway `POST /tasks` through this policy, recording `operator.identified` and `control.decision` events before background execution is accepted.

**Tech Stack:** Python 3.11 dataclasses, pytest, existing FireClaw Gateway/EventLedger.

---

### Task 1: Operator Context And Control Policy

**Files:**
- Create: `src/fireclaw_core/control.py`
- Create: `tests/test_control.py`

- [x] **Step 1: Write failing control tests**

Create tests for:

- local default operator has `task.submit`, `task.confirm`, `task.cancel`, `state.read`;
- observer role cannot submit;
- operator role can submit and cancel but cannot emergency stop;
- admin role can emergency stop.

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: fail because `fireclaw_core.control` does not exist.

- [x] **Step 3: Implement `control.py`**

Add:

- `OperatorContext`
- `ControlDecision`
- `DEFAULT_LOCAL_OPERATOR`
- `scopes_for_role(role)`
- `operator_from_payload(payload)`
- `ControlPolicy.evaluate(operator, action)`

- [x] **Step 4: Run control tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: all control tests pass.

### Task 2: Gateway Task Submission Control Events

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`

- [x] **Step 1: Write failing Gateway audit test**

Add/extend a Gateway test so `POST /tasks` with an operator payload emits:

- `operator.identified`
- `control.decision`

and the task is accepted when policy allows `task.submit`.

- [x] **Step 2: Run Gateway test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_records_operator_and_control_decision_for_task_submission -q
```

Expected: fail because Gateway does not record operator/control events.

- [x] **Step 3: Wire Gateway submit policy**

Add optional `operator` parameter to `submit_agent(...)`, evaluate `task.submit`, append `operator.identified` and `control.decision` events, and deny submission if the decision is not `allow`.

- [x] **Step 4: Run focused Gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py -q
```

Expected: all Gateway tests pass.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Update README**

Document optional `operator` payload on `POST /tasks` and new control-plane events.

- [x] **Step 2: Update memory**

Record commands, files, results, and remaining gaps.

- [x] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.
