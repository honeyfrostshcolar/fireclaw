# Safety Gate Skill Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Enforce skill metadata in the safety gate before any execution starts.

**Architecture:** Keep safety as a pure pre-execution decision over `PlanningResult`, `SkillRegistry`, dry-run mode, and available sensors. Add `available_sensors` to the safety evaluation context and let `FireClawAgent` forward it.

**Tech Stack:** Python 3.11, dataclasses, pytest.

---

### Task 1: Sensor Requirement Enforcement

**Files:**
- Modify: `src/fireclaw_core/safety.py`
- Test: `tests/test_safety.py`

- [x] **Step 1: Write failing sensor block test**

Add a direct skill plan where the skill declares `required_sensors=["thermal_camera"]`. Evaluate with no available sensors and assert safety returns `block` with a missing sensor reason.

- [x] **Step 2: Write available sensor allow test**

Evaluate the same plan with `available_sensors={"thermal_camera"}` and assert safety allows it.

- [x] **Step 3: Run safety tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py -v
```

Expected: fails because `available_sensors` is not supported yet.

- [x] **Step 4: Implement sensor checks**

Add `available_sensors: set[str] | None = None` to `SafetyGate.evaluate()`. Block missing required sensors after missing-skill validation.

- [x] **Step 5: Run safety tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py -v
```

Expected: safety tests pass.

### Task 2: Retry and Real-Robot Metadata Enforcement

**Files:**
- Modify: `src/fireclaw_core/safety.py`
- Test: `tests/test_safety.py`

- [x] **Step 1: Write failing retry safety test**

Add a skill with `max_attempts=2` and `idempotent=False`. Assert safety blocks it.

- [x] **Step 2: Write real-robot metadata tests**

Add tests that:

- dry-run mode blocks `dry_run_only=false`;
- non-dry-run mode blocks skills without `allow_real_robot=true`;
- non-dry-run mode allows a direct skill with `allow_real_robot=true` and `dry_run_only=false`.

- [x] **Step 3: Run safety tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py -v
```

Expected: fails until metadata enforcement is updated.

- [x] **Step 4: Implement metadata checks**

Update safety logic for retry idempotency and real-robot eligibility.

- [x] **Step 5: Run safety tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py -v
```

Expected: safety tests pass.

### Task 3: Agent Sensor Context

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing agent sensor propagation test**

Create an agent with a workspace skill requiring a sensor not listed in `available_sensors`; invoke it directly and assert result is `block`, execution is `None`, and the robot took no actions.

- [x] **Step 2: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: fails because `FireClawAgent` does not pass sensor context yet.

- [x] **Step 3: Add agent available_sensors parameter**

Add `available_sensors` to `FireClawAgent.__init__` and pass it to `SafetyGate.evaluate()`.

- [x] **Step 4: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

### Task 4: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Document safety metadata enforcement**

Update README with how safety uses skill metadata and sensor context.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and the original dry-run demo still succeeds.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and verification results, then mark all checkboxes complete.
