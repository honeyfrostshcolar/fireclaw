# Planner Tool Schema Serialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add stable planner/tool schema serialization for FireClaw skills and LLM planner requests.

**Architecture:** Create a focused `tool_schema.py` module for JSON-compatible schema generation. Let `LLMToolCallingPlanner` use this module when building client requests. Keep model-provider API calls out of scope.

**Tech Stack:** Python 3.11, dict-based JSON schema payloads, pytest.

---

### Task 1: Skill Metadata to Tool Schema

**Files:**
- Create: `src/fireclaw_core/tool_schema.py`
- Test: `tests/test_tool_schema.py`

- [x] **Step 1: Write failing single-tool schema test**

Add a test for `skill_metadata_to_tool_schema(...)` that verifies `type`, function name, description, permissive parameters, and `x-fireclaw` metadata.

- [x] **Step 2: Write failing sorted-tool list test**

Add a test for `build_tool_schemas(...)` verifying schemas are sorted by `function.name`.

- [x] **Step 3: Run tool schema tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_tool_schema.py -v
```

Expected: fails because `tool_schema.py` does not exist.

- [x] **Step 4: Implement tool schema serialization**

Create `skill_metadata_to_tool_schema()` and `build_tool_schemas()`.

- [x] **Step 5: Run tool schema tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_tool_schema.py -v
```

Expected: tool schema tests pass.

### Task 2: Planner Request Payload

**Files:**
- Modify: `src/fireclaw_core/tool_schema.py`
- Test: `tests/test_tool_schema.py`

- [x] **Step 1: Write failing planner request test**

Add a test for `build_planner_request(command, context)` verifying command, context, tools, response schema, and instructions.

- [x] **Step 2: Run tool schema tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_tool_schema.py -v
```

Expected: fails until request builder exists.

- [x] **Step 3: Implement planner request builder**

Add `planner_response_schema()` and `build_planner_request()`.

- [x] **Step 4: Run tool schema tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_tool_schema.py -v
```

Expected: tool schema tests pass.

### Task 3: LLM Planner Uses Serialized Request

**Files:**
- Modify: `src/fireclaw_core/llm_planner.py`
- Test: `tests/test_llm_planner.py`

- [x] **Step 1: Write failing enriched-request test**

Update or add an LLM planner test that asserts the client request includes `tools`, `response_schema`, and `instructions`.

- [x] **Step 2: Run LLM planner tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py -v
```

Expected: fails because LLM planner still builds a minimal request.

- [x] **Step 3: Use `build_planner_request()`**

Update `LLMToolCallingPlanner._build_request()` to delegate to `tool_schema.build_planner_request()`.

- [x] **Step 4: Run LLM planner tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py -v
```

Expected: LLM planner tests pass.

### Task 4: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Document tool schema serialization**

Update README with the provider-agnostic tool schema and planner request shape.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "去二楼救人" --session-id schema-demo --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and CLI remains compatible.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and verification results to `memory/2026-06-02/fireclaw-dry-run-core.md`, then mark all checkboxes complete.
