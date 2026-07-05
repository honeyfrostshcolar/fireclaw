# Skill Input Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add JSON-compatible skill input schemas and use them in planner tool schemas.

**Architecture:** Store `input_schema` on `Skill`, pass it through manifest loading and registry metadata, and have `tool_schema.py` use it as function parameters. Keep executor validation out of scope for this phase.

**Tech Stack:** Python 3.11, dict-based JSON schema payloads, pytest.

---

### Task 1: Skill Input Schema Metadata

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing built-in schema metadata test**

Add a test asserting `create_default_skill_registry(...).list_metadata()` includes:

- `navigate_to_floor.input_schema.required == ["floor"]`;
- `return_to_safe_zone.input_schema.additionalProperties is False`.

- [x] **Step 2: Run execution tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails because `input_schema` is not in metadata.

- [x] **Step 3: Add input_schema to Skill**

Add `input_schema` with a generic default and explicit schemas for built-in skills.

- [x] **Step 4: Run execution tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: execution tests pass.

### Task 2: Manifest Input Schema

**Files:**
- Modify: `src/fireclaw_core/skill_manifest.py`
- Modify: `src/fireclaw_core/skills.py`
- Test: `tests/test_skill_manifest.py`

- [x] **Step 1: Write failing valid manifest input_schema test**

Add a manifest with `input_schema` requiring `text` and assert loaded skill stores it.

- [x] **Step 2: Write failing invalid manifest input_schema test**

Add a manifest where `input_schema` is not an object or has `type!="object"` and assert loader rejects it.

- [x] **Step 3: Run manifest tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_skill_manifest.py -v
```

Expected: fails until manifest parsing is implemented.

- [x] **Step 4: Implement manifest input_schema parsing**

Validate and pass `input_schema` into `create_subprocess_skill()`.

- [x] **Step 5: Run manifest tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_skill_manifest.py -v
```

Expected: manifest tests pass.

### Task 3: Tool Schema Uses Input Schema

**Files:**
- Modify: `src/fireclaw_core/tool_schema.py`
- Test: `tests/test_tool_schema.py`
- Modify: `skills/examples/echo_policy.skill.json`

- [x] **Step 1: Write failing tool schema input_schema test**

Add a test asserting `skill_metadata_to_tool_schema()` uses `skill_metadata["input_schema"]` as `function.parameters`.

- [x] **Step 2: Run tool schema tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_tool_schema.py -v
```

Expected: fails because tool schema still uses generic parameters.

- [x] **Step 3: Implement tool schema parameter selection**

Use declared `input_schema` when present; otherwise use the generic fallback.

- [x] **Step 4: Update example manifest**

Add a permissive `input_schema` to `skills/examples/echo_policy.skill.json`.

- [x] **Step 5: Run tool schema and workspace example tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_tool_schema.py tests/test_workspace_skill_example.py -v
```

Expected: tests pass.

### Task 4: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Document input_schema**

Update README manifest examples and planner tool schema examples with `input_schema`.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "你有哪些技能" --session-id input-schema-demo --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and skill listing includes `input_schema`.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and verification results to `memory/2026-06-02/fireclaw-dry-run-core.md`, then mark all checkboxes complete.
