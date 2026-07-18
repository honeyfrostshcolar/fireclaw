# Skill Manifest Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Extend workspace skill manifests with execution and robotics-safety metadata that FireClaw can validate, list, and log.

**Architecture:** Store metadata on `Skill`, validate it in `skill_manifest.py`, pass it through `create_subprocess_skill()`, and expose it through `SkillRegistry.list_metadata()`. Keep planner, safety, and executor behavior unchanged except that executor already uses `Skill.max_attempts`.

**Tech Stack:** Python 3.11, dataclasses, pytest.

---

### Task 1: Skill Metadata Model

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing metadata listing test**

Add a test that creates a `Skill` with `max_attempts=2`, `idempotent=True`, `required_sensors=["rgb_camera"]`, `failure_categories=["timeout"]`, `allow_real_robot=False`, and `timeout_seconds=3.0`, then verifies `SkillRegistry.list_metadata()` exposes those fields.

- [x] **Step 2: Run execution tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails because the new metadata fields do not exist or are not listed.

- [x] **Step 3: Add Skill metadata fields**

Add fields to `Skill`:

- `idempotent: bool = False`
- `required_sensors: list[str]`
- `failure_categories: list[str]`
- `allow_real_robot: bool = False`
- `timeout_seconds: float | None = None`

Update `list_metadata()` to include them plus existing `max_attempts`.

- [x] **Step 4: Run execution tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: execution tests pass.

### Task 2: Manifest Metadata Validation

**Files:**
- Modify: `src/fireclaw_core/skill_manifest.py`
- Modify: `src/fireclaw_core/skills.py`
- Test: `tests/test_skill_manifest.py`

- [x] **Step 1: Write failing rich-manifest test**

Add a test loading a manifest with all new metadata fields and assert the resulting `Skill` stores them.

- [x] **Step 2: Write failing invalid-manifest tests**

Add tests that reject:

- `max_attempts=2` with `idempotent=false`;
- malformed `required_sensors`;
- `allow_real_robot=true` with `dry_run_only=true`.

- [x] **Step 3: Run manifest tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_skill_manifest.py -v
```

Expected: fails until validation and field propagation are implemented.

- [x] **Step 4: Implement manifest parsing**

Validate optional fields, pass them into `create_subprocess_skill()`, and keep defaults conservative.

- [x] **Step 5: Run manifest tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_skill_manifest.py -v
```

Expected: manifest tests pass.

### Task 3: Workspace Example and Agent Skill Listing

**Files:**
- Modify: `skills/examples/echo_policy.skill.json`
- Modify: `tests/test_agent.py`
- Modify: `README.md`

- [x] **Step 1: Update example manifest**

Add conservative metadata to `echo_policy.skill.json`.

- [x] **Step 2: Add skill-listing test**

Assert agent skill listing exposes metadata for workspace skills.

- [x] **Step 3: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

- [x] **Step 4: Document manifest metadata**

Update README manifest example and describe the safety rules.

### Task 4: Final Verification and Memory

**Files:**
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "你有哪些技能" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: tests pass and skill listing includes the new metadata fields.

- [x] **Step 2: Update memory and mark plan complete**

Append implementation notes and verification results, then mark all checkboxes complete.
