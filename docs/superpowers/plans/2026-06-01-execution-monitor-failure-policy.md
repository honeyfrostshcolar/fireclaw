# Execution Monitor Failure Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add auditable execution attempts, opt-in skill retries, and explicit escalation output when a skill cannot complete.

**Architecture:** Keep planning and safety unchanged. Add a small `FailurePolicy` used by `PlanExecutor`; store attempt history on each executed step; expose retry policy through `Skill.max_attempts` with a conservative default of one attempt.

**Tech Stack:** Python 3.11, dataclasses, pytest.

---

### Task 1: Execution Attempt History

**Files:**
- Modify: `src/fireclaw_core/executor.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing attempt-history test**

Add a test asserting a normal rescue step includes `attempt_count == 1` and one recorded attempt with structured output.

- [x] **Step 2: Run execution tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails because `attempt_count` and `attempts` do not exist yet.

- [x] **Step 3: Implement attempt-history fields**

Add a `StepAttemptResult` dataclass and fields on `StepExecutionResult`:

- `attempt_count`
- `attempts`
- `failure_category`
- `operator_action`

Populate attempt history for every skill call.

- [x] **Step 4: Run execution tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: execution tests pass.

### Task 2: Opt-In Retry Policy

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Modify: `src/fireclaw_core/executor.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing retry-success test**

Add a test with a custom skill using `max_attempts=2`; the first call fails and the second succeeds. Assert the step succeeds with two attempts.

- [x] **Step 2: Run execution tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails because `Skill.max_attempts` and retry behavior do not exist yet.

- [x] **Step 3: Add skill retry metadata and executor retry loop**

Add `max_attempts: int = 1` to `Skill`. Update `PlanExecutor` to retry a failed result while attempts remain.

- [x] **Step 4: Run execution tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: execution tests pass.

### Task 3: Failure Policy and Escalation

**Files:**
- Create: `src/fireclaw_core/monitor.py`
- Modify: `src/fireclaw_core/executor.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing exhausted-retry test**

Add a test with a custom skill using `max_attempts=2` that always fails. Assert execution stops with:

- `status == "failed"`
- `attempt_count == 2`
- `failure_category == "recoverable_exhausted"`
- `operator_action == "escalate"`

- [x] **Step 2: Run execution tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails until failure policy output is implemented.

- [x] **Step 3: Implement FailurePolicy**

Create `FailurePolicy` with `decide(skill, attempt_number)` returning `retry` or `stop_and_escalate`. Use it from `PlanExecutor`.

- [x] **Step 4: Run execution tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: execution tests pass.

### Task 4: Agent, CLI, Docs, Memory Verification

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Add agent-level retry output test**

Add an agent test showing execution output serializes attempt history.

- [x] **Step 2: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

- [x] **Step 3: Document execution monitoring**

Update README with retry policy, attempt history, and escalation behavior.

- [x] **Step 4: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and CLI result includes attempt history.

- [x] **Step 5: Update memory and mark plan complete**

Append implementation notes and verification results to `memory/2026-06-01/fireclaw-dry-run-core.md`, then mark all checkboxes complete.
