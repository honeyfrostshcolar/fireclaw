# Subprocess Skill Cancellation v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator cancellation request terminate an active subprocess-backed FireClaw skill.

**Architecture:** Pass the existing Gateway `TaskControl.cancel_event` callback through `FireClawAgent` and `PlanExecutor` into subprocess skills. Replace `subprocess.run(...)` with cancellable `subprocess.Popen(...)` polling in `SubprocessSkillRunner`.

**Tech Stack:** Python 3.11, stdlib `subprocess`, pytest, FireClaw JSONL Gateway/EventLedger.

---

### Task 1: Runtime-Level Cancellable Subprocess Runner

**Files:**
- Modify: `tests/test_runtime.py`
- Modify: `src/fireclaw_core/runtime.py`

- [x] **Step 1: Write the failing test**

Add a test that starts a Python subprocess which sleeps for one second, flips a cancellation flag after the runner starts, and asserts the runner returns a cancelled result quickly:

```python
def test_subprocess_skill_runner_terminates_process_when_cancelled():
    requested = {"cancel": False}
    runner = SubprocessSkillRunner(
        command=[
            sys.executable,
            "-c",
            "import time; time.sleep(1); print('should-not-finish')",
        ],
        timeout_seconds=5,
    )

    started = time.monotonic()

    def cancellation_requested():
        if time.monotonic() - started > 0.05:
            requested["cancel"] = True
        return requested["cancel"]

    result = runner.run({}, cancellation_requested=cancellation_requested)

    assert result.ok is False
    assert result.status == "cancelled"
    assert result.mode == "subprocess"
    assert "cancelled" in result.error
    assert time.monotonic() - started < 0.8
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_runtime.py::test_subprocess_skill_runner_terminates_process_when_cancelled -q
```

Expected: fail because `SubprocessSkillRunner.run()` does not accept `cancellation_requested`.

- [x] **Step 3: Implement minimal cancellable subprocess execution**

Change `SubprocessSkillRunner.run(...)` to accept `cancellation_requested=None`, launch `Popen`, write JSON input through `communicate(timeout=0.02)` polling, terminate on cancellation, kill after a short grace if needed, and return `RobotActionResult(status="cancelled")`.

- [x] **Step 4: Run runtime tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_runtime.py -q
```

Expected: all runtime tests pass.

### Task 2: Executor-To-Skill Cancellation Propagation

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Modify: `src/fireclaw_core/executor.py`
- Modify: `tests/test_gateway.py`

- [x] **Step 1: Write the failing Gateway regression test**

Update the slow subprocess fixture to sleep long enough to prove active subprocess termination, then assert cancellation returns before the subprocess normal duration and no later rescue skill starts.

- [x] **Step 2: Run Gateway test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_cancels_active_task_between_skills -q
```

Expected: fail or take the full slow subprocess duration because cancellation is not propagated into the subprocess runner.

- [x] **Step 3: Pass cancellation into skills**

Update `Skill.run(inputs, cancellation_requested=None)`. For `runtime="subprocess"`, call the handler with `cancellation_requested=cancellation_requested`; otherwise call the handler with only `inputs`. Update `PlanExecutor` to call `skill.run(step.inputs, cancellation_requested=self._cancellation_requested)`.

- [x] **Step 4: Treat cancelled skill attempts as cancelled execution**

After a skill attempt returns, if `result.status == "cancelled"` or `_cancellation_requested()` is true, return `ExecutionResult(status="cancelled")` without emitting terminal `skill.failed`.

- [x] **Step 5: Run focused Gateway and executor tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py tests/test_gateway.py -q
```

Expected: all focused tests pass.

### Task 3: Documentation, Memory, And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Update README**

Document that Gateway cancellation now propagates to active subprocess skills, while ROS1/CUDA-specific cancellation remains future work.

- [x] **Step 2: Add memory record**

Create or update `memory/2026-06-03/fireclaw-dry-run-core.md` with the task goal, OpenClaw analogue, commands run, files changed, verification, conclusion, and remaining gaps.

- [x] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.

- [x] **Step 4: Inspect status**

Run:

```bash
git status --short
```

Expected: only intended files are modified.
