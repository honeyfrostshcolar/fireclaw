# CLI Runtime Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let command-line runs configure robot id, available sensors, and dry-run mode.

**Architecture:** Keep CLI context construction in `src/fireclaw_core/__main__.py`. Use the existing `FireClawAgent` and `DryRunRobotAdapter` parameters; do not change planner, executor, or memory behavior.

**Tech Stack:** Python 3.11, argparse, pytest subprocess tests.

---

### Task 1: Robot ID CLI Option

**Files:**
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing robot-id CLI test**

Add a CLI test that runs `去二楼救人 --robot-id robot-cli` and asserts the first execution output has `robot_id == "robot-cli"`.

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: fails because `--robot-id` is not supported.

- [x] **Step 3: Add `--robot-id`**

Parse `--robot-id`, create `DryRunRobotAdapter(robot_id=args.robot_id)`, and pass it to `FireClawAgent`.

- [x] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: CLI tests pass.

### Task 2: Available Sensors CLI Option

**Files:**
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing sensor allow/block tests**

Add a temporary workspace skill requiring `thermal_camera`. Assert direct invocation blocks without `--available-sensor thermal_camera` and succeeds with it.

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: fails because `--available-sensor` is not supported.

- [x] **Step 3: Add repeatable `--available-sensor`**

Parse the option with `action="append"` and pass a set to `FireClawAgent`.

- [x] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: CLI tests pass.

### Task 3: Real-Run Safety Check CLI Option

**Files:**
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing real-run block test**

Add a CLI test that runs `去二楼救人 --real-run` and asserts nonzero exit with a real-robot safety block.

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: fails because `--real-run` is not supported.

- [x] **Step 3: Add `--real-run`**

Set `dry_run=not args.real_run` when constructing `FireClawAgent`.

- [x] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: CLI tests pass.

### Task 4: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Document CLI runtime context**

Update README with examples for `--robot-id`, `--available-sensor`, and `--real-run`.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "去二楼救人" --robot-id robot-cli --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and CLI output uses `robot_id="robot-cli"`.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and verification results, then mark all checkboxes complete.
