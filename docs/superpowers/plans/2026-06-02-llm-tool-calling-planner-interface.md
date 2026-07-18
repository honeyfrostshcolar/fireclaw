# LLM Tool-Calling Planner Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a provider-agnostic LLM/tool-calling planner interface with validation and deterministic fallback.

**Architecture:** Extend planner contracts with `PlannerContext`, add `LLMToolCallingPlanner` in a new module, and let `FireClawAgent` inject any compatible planner. Keep the rule planner as default.

**Tech Stack:** Python 3.11, dataclasses, protocols, pytest.

---

### Task 1: Planner Context Contract

**Files:**
- Modify: `src/fireclaw_core/planner.py`
- Test: `tests/test_planner.py`

- [x] **Step 1: Write failing context compatibility test**

Add a test that creates `PlannerContext(session_id="s1", turn_index=2, recent_records=[], skills=[])` and passes it to `RuleBasedPlanner().plan("去二楼救人", context=context)`.

- [x] **Step 2: Run planner tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py -v
```

Expected: fails because `PlannerContext` and context argument do not exist.

- [x] **Step 3: Add PlannerContext**

Add `PlannerContext` dataclass and update `RuleBasedPlanner.plan(command, context=None)`.

- [x] **Step 4: Run planner tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py -v
```

Expected: planner tests pass.

### Task 2: LLM Tool-Calling Planner

**Files:**
- Create: `src/fireclaw_core/llm_planner.py`
- Test: `tests/test_llm_planner.py`

- [x] **Step 1: Write failing valid-response test**

Add a fake client returning a structured planned response and assert `LLMToolCallingPlanner.plan(...)` returns `PlanningResult` with the expected `PlanStep`.

- [x] **Step 2: Write failing request-shape test**

Assert the fake client receives command, session id, turn index, recent records, and skills.

- [x] **Step 3: Write failing fallback test**

Use a malformed planned response and assert planner falls back to `RuleBasedPlanner`, successfully planning `去二楼救人`.

- [x] **Step 4: Run LLM planner tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py -v
```

Expected: fails because module does not exist.

- [x] **Step 5: Implement LLMToolCallingPlanner**

Implement client protocol, request construction, response validation, clarification conversion, and fallback.

- [x] **Step 6: Run LLM planner tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py -v
```

Expected: LLM planner tests pass.

### Task 3: Agent Planner Injection and Context Passing

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing injected-planner test**

Add a planner test double that records the received context and returns a direct skill plan. Assert `FireClawAgent(planner=...)` uses it.

- [x] **Step 2: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: fails because agent does not accept injected planners or pass context.

- [x] **Step 3: Implement planner injection**

Add a planner parameter and build `PlannerContext` from session id, turn index, recent memory, and `registry.list_metadata()`.

- [x] **Step 4: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

### Task 4: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Document planner interface**

Update README with the rule planner default and mockable LLM planner boundary.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "去二楼救人" --session-id llm-interface-demo --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and CLI remains rule-planner compatible.

- [x] **Step 3: Update memory and mark plan complete**

Create/update `memory/2026-06-02/fireclaw-dry-run-core.md`, append implementation notes and verification results, then mark all checkboxes complete.
