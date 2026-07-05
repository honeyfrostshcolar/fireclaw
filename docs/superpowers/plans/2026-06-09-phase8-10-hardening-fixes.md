# Phase 8-10 Hardening Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix the remaining hardening gaps after Phase 8-10: safe FTS queries, default-skipped ROS1 smoke tests, reconciled docs/memory checklists, and ROS1 service request conversion.

**Architecture:** Keep this as a narrow stabilization pass. Each issue gets a failing test first, one minimal implementation, focused verification, then final full-suite verification. Do not add new research features or expand Phase 10 beyond the already-approved memory/plugin v1 scope.

**Tech Stack:** Python 3.11, pytest, stdlib `sqlite3`, stdlib `shutil`, ROS1 Noetic test marker, existing FireClaw memory/ROS transport/docs.

---

## Plan Table

| Priority | Issue | Root Cause | Files | Fix Strategy | Focused Verification |
|---|---|---|---|---|---|
| P0 | FTS query unsafe | Natural-language/user queries go directly into SQLite FTS5 `MATCH` | `memory_index.py`, `mission_memory.py`, `tests/test_memory_index.py`, `tests/test_mission_memory.py`, `tests/test_mission_agent.py` | Add query escaping/tokenization and fallback behavior for invalid MATCH syntax | `pytest tests/test_memory_index.py tests/test_mission_memory.py tests/test_mission_agent.py -q` |
| P0 | ROS1 smoke not skipped by default | Marker is registered but no collection-time skip exists | `tests/conftest.py`, `tests/test_ros1_smoke.py`, `pyproject.toml`, docs | Add env-gated skip: run only when `FIRECLAW_RUN_ROS1_SMOKE=1` and ROS commands exist | `pytest tests/test_ros1_smoke.py -q` should skip by default; env-enabled run should execute |
| P1 | Docs/memory state inconsistent | Completion records were appended but old known gaps/checklists remain stale | `memory/2026-06-09/fireclaw-work-resume.md`, phase plan docs, roadmap docs | Remove contradicted gaps, mark completed checklist items, record current verification truth | Search memory/plan docs for stale incomplete-status wording |
| P1 | ROS service dict conversion missing | `Ros1Transport` only converts topic/action dicts; service dicts are passed raw | `ros1_transport.py`, `tests/test_ros1_transport.py`, ROS deployment docs | Add service request class resolution and dict-to-request conversion while preserving empty service calls | `pytest tests/test_ros1_transport.py -q` |

---

## Task 1: Make FTS Search Safe for Natural-Language Queries

**Files:**
- Modify: `src/fireclaw_core/memory_index.py`
- Modify: `src/fireclaw_core/mission_memory.py`
- Modify: `tests/test_memory_index.py`
- Modify: `tests/test_mission_memory.py`
- Modify: `tests/test_mission_agent.py`

- [x] **Step 1: Write failing index tests for unsafe FTS syntax**

Add to `tests/test_memory_index.py`:

```python
def test_search_escapes_colon_query(tmp_path):
    idx = SqliteMemoryIndex(tmp_path / "mem.db")
    idx.upsert(_make_record(record_id="mem-1", content={"note": "command: 去二楼搜索"}))

    results = idx.search("command: 去二楼")

    assert len(results) == 1
    assert results[0]["record_id"] == "mem-1"


def test_search_escapes_unterminated_quote(tmp_path):
    idx = SqliteMemoryIndex(tmp_path / "mem.db")
    idx.upsert(_make_record(record_id="mem-1", content={"note": 'operator said "search second floor'}))

    results = idx.search('"search second floor')

    assert len(results) == 1
    assert results[0]["record_id"] == "mem-1"


def test_search_escapes_trailing_operator(tmp_path):
    idx = SqliteMemoryIndex(tmp_path / "mem.db")
    idx.upsert(_make_record(record_id="mem-1", content={"note": "二楼 OR 搜索"}))

    results = idx.search("二楼 OR")

    assert len(results) == 1
    assert results[0]["record_id"] == "mem-1"
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_memory_index.py::TestSearch::test_search_escapes_colon_query \
  tests/test_memory_index.py::TestSearch::test_search_escapes_unterminated_quote \
  tests/test_memory_index.py::TestSearch::test_search_escapes_trailing_operator \
  -q
```

Expected:

- FAIL with `sqlite3.OperationalError` or assertion failures.

- [x] **Step 3: Implement query sanitizer**

In `src/fireclaw_core/memory_index.py`, add:

```python
def _safe_fts_query(query: str) -> str:
    stripped = query.strip()
    if not stripped or stripped == "*":
        return "*"
    tokens = [
        token.strip('"')
        for token in stripped.replace(":", " ").split()
        if token.strip('"') and token.upper() not in {"AND", "OR", "NOT", "NEAR"}
    ]
    if not tokens:
        return "*"
    return " ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens)
```

Then in `SqliteMemoryIndex.search()` use:

```python
safe_query = _safe_fts_query(query)
if safe_query == "*":
    fts_sql = "SELECT record_id FROM memory_fts"
else:
    fts_sql = "SELECT record_id FROM memory_fts WHERE memory_fts MATCH ?"
    params.append(safe_query)
```

- [x] **Step 4: Add fallback test at MissionMemoryStore layer**

Add to `tests/test_mission_memory.py`:

```python
def test_store_index_search_handles_unsafe_natural_language_query(tmp_path):
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    store.append(_make_record(
        record_id="mem-1",
        content={"note": "command: 去二楼搜索"},
    ))

    results = store.search(keyword="command: 去二楼")

    assert len(results) == 1
    assert results[0].record_id == "mem-1"
```

- [x] **Step 5: Run focused memory tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_index.py tests/test_mission_memory.py -q
```

Expected:

- PASS.

- [x] **Step 6: Run planner-context regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_agent_passes_retrieved_memories_and_corrections_to_planner -q
```

Expected:

- PASS.

---

## Task 2: Skip ROS1 Smoke Tests by Default

**Files:**
- Create: `tests/conftest.py` if missing
- Modify: `tests/test_ros1_smoke.py`
- Modify: `docs/deployment/ros1-deployment-guide.md`
- Modify: `docs/deployment/fireclaw-deployment-checklist.md`

- [x] **Step 1: Write collection behavior test**

Create or modify `tests/conftest.py` with pytest hook behavior. If testing the hook directly is too brittle, use command-level verification in Steps 3 and 5 as the red/green proof.

- [x] **Step 2: Add env-gated skip hook**

Create `tests/conftest.py`:

```python
from __future__ import annotations

import os
import shutil

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_ros1 = os.environ.get("FIRECLAW_RUN_ROS1_SMOKE") == "1"
    ros_available = shutil.which("roscore") is not None and shutil.which("rosrun") is not None
    skip_ros1 = pytest.mark.skip(
        reason=(
            "ROS1 smoke tests require FIRECLAW_RUN_ROS1_SMOKE=1 "
            "and ROS1 commands (roscore, rosrun) on PATH."
        )
    )
    for item in items:
        if "ros1_smoke" in item.keywords and not (run_ros1 and ros_available):
            item.add_marker(skip_ros1)
```

- [x] **Step 3: Verify default skip**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

Expected:

- All ROS1 smoke tests are skipped by default.

- [x] **Step 4: Verify env-enabled ROS smoke path**

Run:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

Expected:

- PASS on machines with ROS1 Noetic/turtlesim/actionlib_tutorials installed.
- SKIP only if ROS commands are unavailable.

- [x] **Step 5: Update docs**

Update docs to say:

```bash
# Default unit suite skips ROS1 smoke tests.
.venv/bin/python -m pytest -q

# Explicit ROS1 smoke proof.
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

---

## Task 3: Reconcile Plan and Memory State

**Files:**
- Modify: `memory/2026-06-09/fireclaw-work-resume.md`
- Modify: `docs/superpowers/plans/2026-06-09-phase8-10-completion-and-deployment-cleanup.md`
- Modify: `docs/superpowers/plans/2026-06-09-phase8-realtime-event-stream.md`
- Modify: `docs/superpowers/plans/2026-06-09-phase9-ros1-integration-proof.md`
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`

- [x] **Step 1: Remove contradicted memory gaps**

In `memory/2026-06-09/fireclaw-work-resume.md`, replace the outdated known gaps about gateway test failures and missing ROS message-builder unit coverage with:

```text
- Gateway tests are green in the latest full-suite verification.
- `_build_ros_message` has topic/action conversion unit coverage.
```

- [x] **Step 2: Keep real remaining gaps**

Keep or update these gaps:

```text
- Real robot hardware proof remains future work.
- ROS2 implementation remains future work (protocol boundary only).
- Memory retrieval still needs stronger ranking/embedding provider lifecycle.
- Plugin SDK runtime hooks remain future work beyond descriptor v1.
```

- [x] **Step 3: Mark umbrella plan checklist complete where implemented**

In `docs/superpowers/plans/2026-06-09-phase8-10-completion-and-deployment-cleanup.md`, mark completed Task 1-12 checkboxes as `[x]`. Do not mark a step complete if Task 1, Task 2, or Task 4 in this hardening plan changes its meaning.

- [x] **Step 4: Verify no stale text remains**

Run:

```bash
rg -n "gateway test failures|missing ROS message-builder unit coverage|\\[ \\]" \
  memory/2026-06-09/fireclaw-work-resume.md \
  docs/superpowers/plans/2026-06-09-phase8-10-completion-and-deployment-cleanup.md
```

Expected:

- No stale gateway failure or missing `_build_ros_message` unit-test claims.
- No unchecked boxes in the completed umbrella plan.

---

## Task 4: Add ROS1 Service Dict-to-Request Conversion

**Files:**
- Modify: `src/fireclaw_core/ros1_transport.py`
- Modify: `tests/test_ros1_transport.py`
- Modify: `docs/deployment/ros1-deployment-guide.md`

- [x] **Step 1: Add fake service request classes**

Add to `tests/test_ros1_transport.py`:

```python
class FakeTriggerRequest:
    __slots__ = ("reason",)
    _slot_types = ("string",)

    def __init__(self):
        self.reason = ""
```

Extend `FakeRos1Module`:

```python
def resolve_service_request_class(self, type_name):
    assert type_name == "std_srvs/Trigger"
    return FakeTriggerRequest
```

- [x] **Step 2: Write failing service conversion test**

Add:

```python
def test_ros1_transport_builds_service_request_from_dict():
    fake = FakeRos1Module()
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(interface="service", name="/stop", type="std_srvs/Trigger")

    result = transport.execute(endpoint, {"reason": "operator stop"}, Ros1TransportConfig(enabled=True))

    assert result["status"] == "succeeded"
    request = fake.service.calls[0]
    assert isinstance(request, FakeTriggerRequest)
    assert request.reason == "operator stop"
```

- [x] **Step 3: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_transport.py::test_ros1_transport_builds_service_request_from_dict -q
```

Expected:

- FAIL because service dicts are currently passed raw.

- [x] **Step 4: Implement service request resolver**

In `Ros1RuntimeModule`, add:

```python
def resolve_service_request_class(self, service_type_name: str) -> Any:
    service_cls = self._resolve_ros_type(service_type_name, preferred_module="srv")
    request_cls_name = service_cls.__name__ + "Request"
    package = service_type_name.partition("/")[0]
    module = import_module(f"{package}.srv")
    return getattr(module, request_cls_name)
```

- [x] **Step 5: Convert service payloads**

In `Ros1Transport.execute()` service branch:

```python
if payload:
    if isinstance(payload, dict) and hasattr(module, "resolve_service_request_class"):
        request_cls = module.resolve_service_request_class(endpoint.type)
        request = _build_ros_message(request_cls, payload)
    else:
        request = payload
    response = service(request)
else:
    response = service()
```

- [x] **Step 6: Run service/transport tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_transport.py -q
```

Expected:

- PASS.

---

## Task 5: Final Focused and Full Verification

**Files:**
- No production files unless tests reveal a bug.
- Modify current memory record with final results.

- [x] **Step 1: Run focused hardening suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_memory_index.py \
  tests/test_mission_memory.py \
  tests/test_mission_agent.py \
  tests/test_ros1_transport.py \
  tests/test_ros1_smoke.py \
  -q
```

Expected:

- PASS for non-ROS tests.
- ROS1 smoke tests SKIP by default.

- [x] **Step 2: Run explicit ROS smoke proof**

Run:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

Expected:

- PASS on the current ROS1-equipped workstation.

- [x] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

- PASS, with ROS1 smoke skipped by default.

- [x] **Step 4: Update memory**

Append a timestamped record to `memory/2026-06-09/fireclaw-work-resume.md` or the current date memory file:

```text
Task goal
Files modified
Commands executed
Focused test results
Full suite result
Remaining known gaps
```

- [x] **Step 5: Review git state**

Run:

```bash
git status --short --branch
git diff --stat
```

Expected:

- Only intentional hardening changes are present.

---

## Acceptance Criteria

- Unsafe FTS queries such as `command: 去二楼`, `"unterminated`, and `二楼 OR` do not crash retrieval.
- Planner memory retrieval no longer silently loses context because of FTS syntax errors.
- ROS1 smoke tests are skipped by default and run only when explicitly enabled.
- ROS1 service dict payloads are converted into request objects when a resolver is available.
- Phase 8-10 plan and memory records no longer contradict actual test results.
- Focused tests and full suite pass.

## Explicit Non-Goals

- No embedding/vector retrieval in this plan.
- No ROS2 implementation in this plan.
- No real robot hardware proof in this plan.
- No changes to plugin descriptor semantics unless tests reveal a direct bug.
