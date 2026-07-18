# Session Conversation State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add local session state with session metadata, session-scoped recall, and minimal multi-turn clarification.

**Architecture:** Reuse JSONL memory as the persistence layer. Add session fields to agent results and memory records; extend memory filtering; keep planner deterministic and unchanged except that the agent may resolve a command before calling it.

**Tech Stack:** Python 3.11, JSONL, pytest.

---

### Task 1: Session Metadata and Turn Index

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Modify: `src/fireclaw_core/memory.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_memory.py`

- [x] **Step 1: Write failing agent session metadata test**

Add a test creating `FireClawAgent(session_id="session-a")`, running two commands, and asserting result/session memory records include `session_id="session-a"` with `turn_index` values `1` and `2`.

- [x] **Step 2: Write failing memory filter test**

Add a test for `JsonlMemoryStore.latest_records(limit=5, session_id="session-a")`.

- [x] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_memory.py -v
```

Expected: fails because session metadata and memory filtering are not implemented.

- [x] **Step 4: Implement session metadata**

Add `session_id` to `FireClawAgent.__init__`, compute turn index from memory records, and add a `session` object to task results before appending memory.

- [x] **Step 5: Implement memory filtering**

Allow `JsonlMemoryStore.latest_records(limit=5, session_id=None)` to filter by `record["session"]["session_id"]`.

- [x] **Step 6: Run tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_memory.py -v
```

Expected: agent and memory tests pass.

### Task 2: Session-Scoped Recall

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing session recall test**

Add records for two sessions and assert `FireClawAgent(session_id="session-a").run("之前做过什么")` only returns session-a records.

- [x] **Step 2: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: fails until recall passes the session id.

- [x] **Step 3: Implement session-scoped recall**

Call `latest_records(limit=5, session_id=self.session_id)` when supported. Keep compatibility with test double memory stores.

- [x] **Step 4: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

### Task 3: Minimal Multi-Turn Clarification

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing two-turn clarification test**

Run `agent.run("救人")`, then `agent.run("二楼")` in the same session. Assert the second result succeeds, `command == "二楼"`, and `session.resolved_command == "去二楼救人"`.

- [x] **Step 2: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: fails because the second command still clarifies.

- [x] **Step 3: Implement command resolution**

If the previous same-session record is a clarification and current command contains a floor but not `救人`, resolve it to `去<floor>救人` before planning. Keep the original `command` field unchanged.

- [x] **Step 4: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: agent tests pass.

### Task 4: CLI Session ID

**Files:**
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI session id test**

Run CLI with `--session-id cli-session` and assert output session metadata contains that id.

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: fails because `--session-id` is not supported.

- [x] **Step 3: Add CLI `--session-id`**

Parse the option and pass it into `FireClawAgent`.

- [x] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: CLI tests pass.

### Task 5: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Document session state**

Update README with `--session-id`, session-scoped recall, and the minimal clarification behavior.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m fireclaw_core "去二楼救人" --session-id demo-session --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: all tests pass and CLI output includes `session.session_id="demo-session"`.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and verification results, then mark all checkboxes complete.
