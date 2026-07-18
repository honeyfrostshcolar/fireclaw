# Operator Confirmation Safety Workflow v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add an auditable operator confirmation workflow for safety-critical FireClaw plans.

**Architecture:** Extend skill metadata with `risk_level`, extend `SafetyGate` with `require_confirmation`, and persist pending confirmation plans in JSONL memory so CLI sessions can confirm or cancel later.

**Tech Stack:** Python 3.11, JSONL, pytest.

---

### Task 1: Safety Metadata and Gate

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Modify: `src/fireclaw_core/skill_manifest.py`
- Modify: `src/fireclaw_core/safety.py`
- Tests: `tests/test_safety.py`, `tests/test_skill_manifest.py`, `tests/test_execution.py`

- [x] **Step 1: Write failing safety confirmation tests**

Add tests for:

- real-run allowed skill returns `require_confirmation`;
- high-risk dry-run skill returns `require_confirmation`;
- confirmed real-run allowed skill returns `allow`.

- [x] **Step 2: Write failing risk metadata tests**

Assert skill metadata and manifests expose `risk_level`.

- [x] **Step 3: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py tests/test_skill_manifest.py tests/test_execution.py -v
```

- [x] **Step 4: Implement risk metadata and safety confirmation**

Add `risk_level`, manifest validation, metadata serialization, and `operator_confirmed` support in `SafetyGate.evaluate`.

- [x] **Step 5: Run targeted tests and verify GREEN**

Run the same targeted pytest command.

### Task 2: Agent Pending Confirmation Workflow

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Tests: `tests/test_agent.py`

- [x] **Step 1: Write failing pending confirmation test**

Use a real-run allowed high-risk workspace skill or injected registry scenario. Assert the first command returns `awaiting_confirmation`, does not execute, and writes pending memory.

- [x] **Step 2: Write failing confirm execution test**

After pending memory exists, run `确认执行`. Assert it executes the pending plan and appends confirmation metadata.

- [x] **Step 3: Write failing cancel test**

After pending memory exists, run `取消`. Assert no execution and a `cancelled` memory record.

- [x] **Step 4: Write failing no-pending test**

Run `确认执行` with no pending plan. Assert `status="clarify"` and no execution.

- [x] **Step 5: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

- [x] **Step 6: Implement agent workflow**

Add confirmation command routing, pending lookup, planning reconstruction, confirmed execution, cancellation, and memory serialization.

- [x] **Step 7: Run agent tests and verify GREEN**

Run agent tests again.

### Task 3: CLI and Docs

**Files:**
- Modify: `src/fireclaw_core/__main__.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

- [x] **Step 1: Write CLI confirmation workflow test**

Run a command that creates pending confirmation, then `确认执行` with the same session and memory path.

- [x] **Step 2: Update CLI success statuses**

Treat `awaiting_confirmation` and `cancelled` as successful command results.

- [x] **Step 3: Document confirmation workflow**

Add README examples and explain pending/confirm/cancel semantics.

- [x] **Step 4: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

### Task 4: Final Verification and Memory

**Files:**
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
```

- [x] **Step 2: Run manual CLI confirmation demo**

Use a temporary skill manifest that requires confirmation and verify pending then confirmed execution.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and mark all checkboxes complete.
