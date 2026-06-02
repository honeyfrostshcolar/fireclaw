# Memory Retrieval v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic structured memory retrieval over JSONL memory records.

**Architecture:** Extend `JsonlMemoryStore` with `search_records(...)`, add agent-level query parsing for memory retrieval commands, and keep retrieval read-only so it does not execute skills or append memory records.

**Tech Stack:** Python 3.11, JSONL, pytest.

---

### Task 1: Memory Store Search API

**Files:**
- Modify: `src/fireclaw_core/memory.py`
- Test: `tests/test_memory.py`

- [x] **Step 1: Write failing structured search test**

Add tests for `search_records(...)` filtering by `session_id`, `status`, `intent`, `target_floor`, and `command_contains`.

- [x] **Step 2: Run memory tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory.py -v
```

Expected: fails because `search_records` does not exist.

- [x] **Step 3: Implement search_records**

Add `search_records(..., limit=5)` returning newest matching records first.

- [x] **Step 4: Run memory tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory.py -v
```

Expected: memory tests pass.

### Task 2: Agent Memory Retrieval Queries

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing successful rescue retrieval test**

Seed memory with successful and unrelated records. Ask `之前二楼救人成功了吗`. Assert `status="retrieved"` and only the matching current-session record is returned.

- [x] **Step 2: Write failing failure retrieval test**

Seed memory with a failed record. Ask `上次失败原因是什么`. Assert the failed record is returned and no robot action runs.

- [x] **Step 3: Write failing empty retrieval test**

Ask for a missing memory query and assert `status="retrieved"` with empty records.

- [x] **Step 4: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: fails because retrieval commands are not recognized.

- [x] **Step 5: Implement retrieval parser and response**

Add `_is_memory_search_command`, `_build_memory_query`, and `_retrieve_memory`. Prefer `memory.search_records` when available and fall back to in-memory filtering for test doubles.

- [x] **Step 6: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

### Task 3: CLI and Docs Verification

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `README.md`

- [x] **Step 1: Write CLI retrieval test**

Run one successful rescue command, then query `之前二楼救人成功了吗` in the same session and assert CLI returns `status="retrieved"`.

- [x] **Step 2: Run CLI tests and verify RED/GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: initially fails until agent retrieval is wired, then passes.

- [x] **Step 3: Document memory retrieval**

Update README with examples for structured memory retrieval.

### Task 4: Final Verification and Memory

**Files:**
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "之前二楼救人成功了吗" --session-id retrieval-demo --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and CLI returns a structured retrieval result.

- [x] **Step 2: Update memory and mark plan complete**

Append implementation notes and verification results, then mark all checkboxes complete.
