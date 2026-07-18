# Config Doctor v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free FireClaw doctor command that reports adapter, skill manifest, safety hook, feedback boundary, and path readiness.

**Architecture:** Create `fireclaw_core.doctor` with a pure `run_doctor(...)` function and module CLI. Reuse existing adapter factory and workspace skill loader.

**Tech Stack:** Python 3.11 dataclasses, argparse, JSON output, pytest.

---

### Task 1: Doctor Core

**Files:**
- Create: `src/fireclaw_core/doctor.py`
- Create: `tests/test_doctor.py`

- [x] **Step 1: Write failing tests**

Test that `run_doctor(...)` returns warnings for mock adapters, passes emergency-stop and feedback-boundary checks, and fails invalid workspace skill manifests.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_doctor.py -q
```

Expected: fail because `fireclaw_core.doctor` does not exist.

- [x] **Step 3: Implement doctor core**

Add `DoctorCheck`, `run_doctor(...)`, path checks, skill checks, adapter checks, emergency-stop hook check, and feedback-boundary check.

- [x] **Step 4: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_doctor.py -q
```

Expected: pass.

### Task 2: Doctor CLI

**Files:**
- Modify: `src/fireclaw_core/doctor.py`
- Modify: `tests/test_doctor.py`

- [x] **Step 1: Write CLI test**

Run `python -m fireclaw_core.doctor ...` and assert JSON output.

- [x] **Step 2: Verify CLI test RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_doctor.py::test_doctor_module_cli_outputs_json_report -q
```

Expected: fail until CLI is implemented.

- [x] **Step 3: Implement CLI**

Add argparse options for adapter, robot id, memory path, event path, and skills dir.

- [x] **Step 4: Verify focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_doctor.py -q
```

Expected: pass.

### Task 3: Docs, Memory, Full Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Document doctor command**
- [x] **Step 2: Update memory**
- [x] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.

