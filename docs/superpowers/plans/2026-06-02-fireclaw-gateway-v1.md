# FireClaw Gateway v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a local HTTP control plane that lets a resident FireClaw process receive natural-language tasks and route them through the existing agent core.

**Architecture:** Add a dependency-free `gateway.py` built on `ThreadingHTTPServer`; add shared adapter factory; keep agent/planner/safety/executor behavior unchanged.

**Tech Stack:** Python 3.11, standard library HTTP server, pytest.

---

### Task 1: Shared Runtime Factory

**Files:**
- Add/modify: `src/fireclaw_core/runtime_config.py`
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Add shared adapter factory tests**

Assert CLI still supports `--adapter simulator` after moving adapter creation into a shared helper.

- [x] **Step 2: Implement shared adapter factory**

Move adapter creation out of `__main__.py` into `runtime_config.py`.

- [x] **Step 3: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

### Task 2: Gateway HTTP Runtime

**Files:**
- Add: `src/fireclaw_core/gateway.py`
- Test: `tests/test_gateway.py`

- [x] **Step 1: Write failing gateway health/state tests**

Start Gateway on port `0`; assert `/health` and `/state` return JSON.

- [x] **Step 2: Write failing task and memory tests**

Assert `POST /tasks` runs `去二楼救人`; assert `/memory/recent` returns the record.

- [x] **Step 3: Write failing skills test**

Assert `/skills` returns built-in skill metadata.

- [x] **Step 4: Write failing confirmation test**

Use high-risk workspace skill. Assert `POST /tasks` returns `awaiting_confirmation`, then `POST /confirm` returns `succeeded`.

- [x] **Step 5: Run gateway tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py -v
```

- [x] **Step 6: Implement gateway runtime**

Implement `FireClawGateway`, request handling, JSON helpers, endpoint routing, and `main()`.

- [x] **Step 7: Run gateway tests and verify GREEN**

Run gateway tests again.

### Task 3: CLI Entrypoint and Docs

**Files:**
- Add: gateway module CLI behavior through `python -m fireclaw_core.gateway`
- Modify: `README.md`

- [x] **Step 1: Add subprocess smoke test if needed**

Verify gateway module can start and respond in a test-friendly way, or rely on direct server tests.

- [x] **Step 2: Document Gateway v1**

Add README usage examples:

- start gateway;
- `POST /tasks`;
- `POST /confirm`;
- `GET /state`;
- `GET /memory/recent`.

### Task 4: Final Verification and Memory

**Files:**
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
```

- [x] **Step 2: Run manual Gateway demo**

Start Gateway on localhost, call `/health` and `/tasks`, then stop it.

- [x] **Step 3: Update memory and mark plan complete**

Append implementation notes and mark all checkboxes complete.
