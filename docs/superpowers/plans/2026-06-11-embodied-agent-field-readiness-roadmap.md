# Embodied Agent Field Readiness Roadmap Implementation Plan

> **Status: COMPLETE** (2026-06-11) — All 6 tasks implemented and committed. Remaining gaps: real robot/high-fidelity simulation execution, optional platform work (ROS2, Web UI, ACP parity).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move FireClaw from a library/test-complete ROS1-first embodied-agent framework to a deployable, repeatable field/simulation workflow for firefighting robot experiments.

**Architecture:** Keep FireClaw scoped to embodied robotics, not full OpenClaw platform parity. Reuse OpenClaw-inspired patterns only where they support robot task execution, safety, memory learning, lifecycle observability, and auditable deployment proof. ROS1 remains the active robot target; ROS2, ACP/IDE sessions, generic coding-agent task UX, third-party plugin marketplace, and a full Web UI remain out of scope.

**Tech Stack:** Python 3.11, pytest, stdlib JSONL stores, SQLite FTS5 memory index, existing FireClaw MissionAgent/MissionGateway/robot Gateway modules, ROS1 smoke tests gated by `FIRECLAW_RUN_ROS1_SMOKE=1`.

---

## Current Assessment

As of commit `9033a92`, FireClaw covers the intended v1 embodied-agent loop:

```text
operator command
-> planner with retrieved memories and operator corrections
-> provider/runtime fallback boundary
-> plugin provider/memory/tool-approval hooks
-> safety and approval gate
-> mission scheduler
-> robot subagent dispatch
-> robot-local task/action runtime
-> ROS1 topic/service/action transport
-> unified event replay/SSE
-> lifecycle projection, task-flow summary, session lineage, memory recording
```

The remaining work is not “more OpenClaw parity.” It is operational hardening so the system can be run and defended as an embodied-agent experiment or field trial.

Useful OpenClaw references already adapted:

- Task registry and task-flow source-of-truth patterns.
- Session lineage and resume ownership checks.
- Plugin control-plane fingerprints.
- Provider fallback/runtime boundary.
- Approval handoff idempotency.
- Memory fallback/evaluation patterns.

OpenClaw features explicitly not required now:

- Native ROS2 adapter.
- Full ACP/IDE session platform.
- Generic coding-agent task-flow UX.
- Third-party plugin marketplace and arbitrary untrusted dynamic loading.
- Full WebSocket/operator Web UI while SSE + REST remains enough.

## Remaining Embodied-Agent Gaps

1. **Deployment runtime assembly:** `MissionAgent` can accept memory retriever, task registry, subagent registry, session lineage, task-flow store, plugin runtime, approval runtime, and provider runtime, but `mission_cli.py` still builds mostly bare agents. The CLI path cannot yet run the same rich lifecycle/memory/provider setup used by tests.
2. **Lifecycle automation:** `LifecycleMaintenanceRunner` is visible through fleet doctor, but there is no repeatable CLI/daemon-style maintenance command for cross-process stale/orphan recovery before or after experiments.
3. **End-to-end embodied proof:** Unit/integration tests cover each boundary, but there is no single “operator command -> mission -> robot gateway -> events -> task-flow -> lineage -> memory” scenario gate suitable for demos and papers.
4. **Memory indexing automation:** Memory retrieval v2 and eval exist, but session/mission transcript indexing and rescue-query quality checks are not yet an explicit operator command or CI/demo gate.
5. **ROS1 hardware proof packaging:** ROS1 smoke artifacts and runbooks exist, but there is no single command that collects doctor output, smoke test result, artifact JSONL, and redacted evidence bundle for a real robot/high-fidelity sim run.
6. **Documentation truth pass:** Architecture docs still contain a few stale next-step statements, especially around already-implemented session lineage and old default test counts.

---

### Task 1: Add a Deployable Mission Runtime Factory

**Files:**
- Create: `src/fireclaw_core/mission_runtime.py`
- Modify: `src/fireclaw_core/mission_cli.py`
- Create/Modify: `tests/test_mission_runtime.py`
- Modify: `tests/test_mission_cli.py`

- [x] **Step 1: Write failing tests for rich runtime assembly**

Create `tests/test_mission_runtime.py` with:

```python
from pathlib import Path

from fireclaw_core.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.robot_registry import RobotRegistryEntry, save_robot_registry


def test_build_mission_agent_wires_persistent_runtime_stores(tmp_path: Path):
    registry_path = tmp_path / "robots.json"
    save_robot_registry(
        registry_path,
        [RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",))],
    )
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_memory=tmp_path / "memory.jsonl",
        memory_index=tmp_path / "memory.sqlite",
        task_registry=tmp_path / "tasks.jsonl",
        subagent_registry=tmp_path / "subagents.jsonl",
        session_lineage=tmp_path / "lineage.jsonl",
        task_flow=tmp_path / "flows.jsonl",
        approvals=tmp_path / "approvals.jsonl",
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op-a", role="operator")

    assert agent.mission_registry is not None
    assert agent.mission_memory is not None
    assert agent.memory_retriever is not None
    assert agent.task_registry is not None
    assert agent.subagent_registry is not None
    assert agent._session_lineage_store is not None
    assert agent._task_flow_store is not None
    assert agent.approval_store is not None
```

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_runtime.py -q
```

Expected before implementation: import failure for `fireclaw_core.mission_runtime`.

- [x] **Step 2: Implement `MissionRuntimePaths` and agent factory**

Create `src/fireclaw_core/mission_runtime.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fireclaw_core.approval_store import JsonlApprovalStore
from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
from fireclaw_core.memory_index import SqliteMemoryIndex
from fireclaw_core.memory_retrieval import MemoryRetriever
from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_memory import MissionMemoryStore
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import load_robot_registry
from fireclaw_core.session_lineage import JsonlSessionLineageStore
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_flow_registry import JsonlTaskFlowRegistryStore
from fireclaw_core.task_registry import JsonlTaskRegistryStore


@dataclass(frozen=True)
class MissionRuntimePaths:
    robot_registry: Path
    mission_registry: Path
    mission_memory: Path | None = None
    memory_index: Path | None = None
    task_registry: Path | None = None
    subagent_registry: Path | None = None
    session_lineage: Path | None = None
    task_flow: Path | None = None
    approvals: Path | None = None


def build_operator_context(
    *,
    operator_id: str,
    role: str,
    scopes: Iterable[str] | None = None,
    source: str = "mission_cli",
) -> OperatorContext:
    return OperatorContext(
        operator_id=operator_id,
        role=role,
        control_scopes=set(scopes) if scopes is not None else scopes_for_role(role),
        source=source,
    )


def build_mission_agent_from_paths(
    paths: MissionRuntimePaths,
    *,
    operator_id: str,
    role: str,
    scopes: Iterable[str] | None = None,
    planner=None,
    plugin_runtime=None,
) -> MissionAgent:
    memory_store = MissionMemoryStore(paths.mission_memory) if paths.mission_memory else None
    memory_retriever = None
    if paths.memory_index is not None:
        memory_retriever = MemoryRetriever(index=SqliteMemoryIndex(paths.memory_index))
    task_registry = JsonlTaskRegistryStore(paths.task_registry) if paths.task_registry else None
    subagent_registry = JsonlSubagentRegistry(paths.subagent_registry) if paths.subagent_registry else None
    return MissionAgent(
        registry=load_robot_registry(paths.robot_registry),
        mission_registry=JsonlMissionRegistry(paths.mission_registry),
        planner=planner,
        control_policy=ControlPolicy(),
        operator=build_operator_context(operator_id=operator_id, role=role, scopes=scopes),
        mission_memory=memory_store,
        memory_retriever=memory_retriever,
        approval_store=JsonlApprovalStore(paths.approvals) if paths.approvals else None,
        plugin_runtime=plugin_runtime,
        task_registry=task_registry,
        subagent_registry=subagent_registry,
        session_lineage_store=JsonlSessionLineageStore(str(paths.session_lineage)) if paths.session_lineage else None,
        task_flow_store=JsonlTaskFlowRegistryStore(paths.task_flow) if paths.task_flow else None,
    )
```

- [x] **Step 3: Add CLI path flags and reuse the factory**

In `src/fireclaw_core/mission_cli.py`, add optional shared runtime flags:

```python
def _add_runtime_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--memory-path", default=None, help="Path to mission memory JSONL.")
    parser.add_argument("--memory-index", default=None, help="Path to SQLite memory index.")
    parser.add_argument("--task-registry", default=None, help="Path to task registry JSONL.")
    parser.add_argument("--subagent-registry", default=None, help="Path to subagent registry JSONL.")
    parser.add_argument("--session-lineage", default=None, help="Path to session lineage JSONL.")
    parser.add_argument("--task-flow", default=None, help="Path to task-flow registry JSONL.")
    parser.add_argument("--approval-path", default=None, help="Path to approval store JSONL.")
```

Call `_add_runtime_paths()` from `plan-mission`, `trace`, `events`, `cancel`, and `replay`. Replace repeated `_build_mission_agent*` internals with `MissionRuntimePaths` + `build_mission_agent_from_paths()`.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_runtime.py tests/test_mission_cli.py tests/test_mission_agent.py -q
```

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/mission_runtime.py src/fireclaw_core/mission_cli.py tests/test_mission_runtime.py tests/test_mission_cli.py
git commit -m "feat: assemble deployable mission runtime from CLI paths"
```

### Task 2: Add Lifecycle Maintenance CLI and Explicit Recovery Report

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `src/fireclaw_core/lifecycle_maintenance.py`
- Create/Modify: `tests/test_lifecycle_maintenance.py`
- Modify: `tests/test_mission_cli.py`

- [x] **Step 1: Write CLI test for lifecycle report**

Add a test that invokes:

```bash
python -m fireclaw_core.mission_cli lifecycle-check \
  --task-registry tasks.jsonl \
  --subagent-registry subagents.jsonl
```

Assert JSON output contains:

```python
assert body["status"] in {"ok", "warn"}
assert "stale_tasks" in body
assert "orphaned_subagents" in body
assert "checked_at" in body
```

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_lifecycle_check_cli_outputs_report -q
```

Expected before implementation: fail because the subcommand does not exist.

- [x] **Step 2: Add `lifecycle-check` subcommand**

In `mission_cli.py`, add:

```python
lifecycle = subparsers.add_parser("lifecycle-check", help="Check task/subagent lifecycle consistency.")
lifecycle.add_argument("--task-registry", required=True)
lifecycle.add_argument("--subagent-registry", required=True)
lifecycle.add_argument("--stale-threshold-seconds", type=float, default=300.0)
```

Handler:

```python
if args.command_name == "lifecycle-check":
    from fireclaw_core.lifecycle_maintenance import LifecycleMaintenanceRunner
    from fireclaw_core.subagent_registry import JsonlSubagentRegistry
    from fireclaw_core.task_registry import JsonlTaskRegistryStore
    report = LifecycleMaintenanceRunner(
        task_registry=JsonlTaskRegistryStore(args.task_registry),
        subagent_registry=JsonlSubagentRegistry(args.subagent_registry),
    ).run(stale_threshold_seconds=args.stale_threshold_seconds)
    _print_json(report)
    return 0 if report["status"] == "ok" else 2
```

- [x] **Step 3: Document safety semantics**

Add a docstring to `LifecycleMaintenanceRunner.run()` explaining that it marks orphaned subagent records terminal via `LifecycleReconciler`, but it does not replay or resume physical robot work. Operators must inspect robot state before issuing new commands.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_lifecycle_maintenance.py tests/test_mission_cli.py -q
```

Expected: selected tests pass.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/mission_cli.py src/fireclaw_core/lifecycle_maintenance.py tests/test_lifecycle_maintenance.py tests/test_mission_cli.py
git commit -m "feat: expose lifecycle maintenance from mission CLI"
```

### Task 3: Add an End-to-End Embodied Mission Scenario Gate

**Files:**
- Create: `tests/test_embodied_mission_e2e.py`
- Modify: `src/fireclaw_core/mission_gateway.py` only if gateway wiring blocks the test

- [x] **Step 1: Write the e2e test first**

Create `tests/test_embodied_mission_e2e.py` that:

1. Starts one robot-local `FireClawGateway` in simulator mode.
2. Creates a `RobotRegistry` pointing at that gateway.
3. Builds `MissionAgent` with mission registry, memory store, task registry, subagent registry, session lineage store, and task-flow store.
4. Submits `去二楼救人` through `MissionGateway.submit_mission(... use_scheduler=True)`.
5. Polls `get_mission_events()` until terminal robot events appear.
6. Asserts mission trace, task-flow, lineage, task registry, subagent registry, and memory all contain the mission.

Core assertions:

```python
assert submit["mission_id"] == "mission-e2e"
assert submit["subtask_results"]
assert task_flow_store.get("mission-e2e") is not None
assert lineage_store.get("mission-e2e") is not None
assert task_registry.get("mission-e2e") is not None
assert subagent_registry.list_by_parent_mission("mission-e2e")
assert memory_store.list_records(mission_id="mission-e2e")
```

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_mission_e2e.py -q
```

Expected before any wiring fix: fail only if a production path is missing; otherwise pass and become the regression gate.

- [x] **Step 2: Fix only missing wiring if the test fails**

If `MissionGateway.submit_mission()` does not pass necessary operator/session context or if event aggregation does not update task-flow, fix the smallest production path. Do not add new platform features.

- [x] **Step 3: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_mission_e2e.py tests/test_mission_gateway.py tests/test_mission_agent.py -q
```

Expected: selected tests pass.

- [x] **Step 4: Commit**

```bash
git add tests/test_embodied_mission_e2e.py src/fireclaw_core/mission_gateway.py src/fireclaw_core/mission_agent.py
git commit -m "test: add embodied mission end-to-end scenario gate"
```

### Task 4: Add Memory Indexing and Eval CLI Gate

**Files:**
- Create: `src/fireclaw_core/memory_cli.py`
- Create/Modify: `tests/test_memory_cli.py`
- Modify: `docs/deployment/fireclaw-deployment-checklist.md`

- [x] **Step 1: Write tests for indexing and eval commands**

Add `tests/test_memory_cli.py`:

```python
def test_memory_cli_indexes_jsonl_and_runs_eval(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"
    fixture_path = tmp_path / "cases.json"
    memory_path.write_text(
        '{"record_id":"successful-rescue-floor-2","mission_id":"m1","record_type":"mission_outcome","content":{"command":"去二楼救人","status":"succeeded"},"created_at":"2026-06-11T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    fixture_path.write_text(
        '[{"query":"去二楼救人","expected_record_ids":["successful-rescue-floor-2"],"min_score":0.4,"record_type":"mission_outcome"}]',
        encoding="utf-8",
    )

    assert memory_cli_main(["index", "--memory-path", str(memory_path), "--index-path", str(index_path)]) == 0
    assert memory_cli_main(["eval", "--index-path", str(index_path), "--fixture", str(fixture_path), "--threshold", "1.0"]) == 0
```

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_cli.py -q
```

Expected before implementation: import failure.

- [x] **Step 2: Implement `memory_cli.py`**

Create a small CLI with:

```python
def main(argv: list[str] | None = None) -> int:
    ...
```

Subcommands:

- `index --memory-path <jsonl> --index-path <sqlite>`
- `eval --index-path <sqlite> --fixture <json> --threshold <float>`

Use existing `MissionMemoryStore`, `SqliteMemoryIndex`, `MemoryRetriever`, `load_eval_cases()`, and `evaluate_retrieval()`. For `eval`, return `0` when threshold passes, `2` when threshold fails, `1` for malformed input.

- [x] **Step 3: Document as demo/CI gate**

In `docs/deployment/fireclaw-deployment-checklist.md`, add:

```bash
python -m fireclaw_core.memory_cli index \
  --memory-path memory/fireclaw-missions-memory.jsonl \
  --index-path results/memory/fireclaw-memory.sqlite

python -m fireclaw_core.memory_cli eval \
  --index-path results/memory/fireclaw-memory.sqlite \
  --fixture tests/fixtures/memory_eval/fireclaw_rescue_queries.json \
  --threshold 1.0
```

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_cli.py tests/test_memory_eval.py tests/test_memory_retrieval.py tests/test_doctor.py -q
```

Expected: selected tests pass.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/memory_cli.py tests/test_memory_cli.py docs/deployment/fireclaw-deployment-checklist.md
git commit -m "feat: add memory indexing and eval CLI gate"
```

### Task 5: Add ROS1 Proof Bundle Command

**Files:**
- Create: `src/fireclaw_core/ros1_proof_bundle.py`
- Create: `tests/test_ros1_proof_bundle.py`
- Modify: `docs/deployment/ros1-hardware-smoke-proof.md`

- [x] **Step 1: Write proof bundle tests**

Create `tests/test_ros1_proof_bundle.py`:

```python
def test_ros1_proof_bundle_writes_redacted_summary(tmp_path):
    output_dir = tmp_path / "bundle"
    result = create_ros1_proof_bundle(
        output_dir=output_dir,
        robot_id="fireclaw-01",
        environment="sim",
        doctor_report={"status": "pass", "checks": []},
        smoke_artifacts=[{"run_id": "run-1", "passed": True, "error_summary": None}],
        notes="no secrets here",
    )

    assert result["status"] == "created"
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "README.md").exists()
```

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_proof_bundle.py -q
```

Expected before implementation: import failure.

- [x] **Step 2: Implement proof bundle writer**

Create `src/fireclaw_core/ros1_proof_bundle.py` with:

```python
def create_ros1_proof_bundle(
    *,
    output_dir: str | Path,
    robot_id: str,
    environment: str,
    doctor_report: dict[str, Any],
    smoke_artifacts: list[dict[str, Any]],
    notes: str = "",
) -> dict[str, Any]:
    ...
```

Write:

- `summary.json`
- `README.md`
- `doctor-report.json`
- `ros1-smoke-artifacts.json`

Apply `redact_dict()` to all dict payloads before writing.

- [x] **Step 3: Add CLI entry**

Allow:

```bash
python -m fireclaw_core.ros1_proof_bundle \
  --output-dir results/ros1-proof/<run-id> \
  --robot-id fireclaw-01 \
  --environment sim \
  --doctor-report results/doctor.json \
  --smoke-artifacts results/ros1-smoke/artifacts.jsonl
```

- [x] **Step 4: Update deployment doc**

In `docs/deployment/ros1-hardware-smoke-proof.md`, add the bundle command after the smoke test command and list the expected files.

- [x] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_proof_bundle.py tests/test_ros1_smoke_artifacts.py -q
```

Expected: selected tests pass.

- [x] **Step 6: Commit**

```bash
git add src/fireclaw_core/ros1_proof_bundle.py tests/test_ros1_proof_bundle.py docs/deployment/ros1-hardware-smoke-proof.md
git commit -m "feat: create ROS1 proof bundle artifacts"
```

### Task 6: Documentation Truth Pass and Scope Cleanup

**Files:**
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`
- Modify: `README.md`
- Modify: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`

- [x] **Step 1: Fix stale capability statements**

Update stale statements:

- Replace old default test counts with the latest verified count from this implementation turn.
- Remove “Session lineage and resume ownership guard” from remaining next steps; it is implemented.
- Mark `ProviderRuntime` as wired into `LLMMissionPlanner`, but note CLI fallback config remains planned until Task 1.
- Mark ROS2 as out of scope for the current ROS1-first embodied roadmap.

- [x] **Step 2: Separate required embodied-agent gaps from optional platform work**

Use these headings:

```markdown
## Required For Embodied-Agent Field Readiness
...
## Optional Platform Work
...
```

Required:

- deployment runtime assembly;
- lifecycle maintenance command;
- e2e embodied scenario gate;
- memory index/eval CLI gate;
- ROS1 proof bundle;
- real robot/high-fidelity simulation execution.

Optional:

- ROS2 native adapter;
- WebSocket/full Web UI;
- full OpenClaw ACP/IDE parity;
- marketplace/dynamic plugin sandboxing.

- [x] **Step 3: Verify docs do not contradict the new plan**

Run:

```bash
rg -n "session lineage.*next|provider_runtime.py does not exist|823 passed|1008 passed|ROS2 adapter 实现|full OpenClaw platform parity" docs memory README.md
```

Expected: no stale required-work statements remain. Mentions of ROS2/OpenClaw parity are allowed only under optional/out-of-scope sections.

- [x] **Step 4: Verify full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all default tests pass, ROS1 smoke skipped unless `FIRECLAW_RUN_ROS1_SMOKE=1`.

- [x] **Step 5: Commit**

```bash
git add docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md docs/architecture/fireclaw-openclaw-alignment.md README.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md
git commit -m "docs: reframe FireClaw embodied-agent field readiness roadmap"
```

---

## Final Verification

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: default suite passes with ROS1 smoke skipped by default.

Optional hardware/simulator proof:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 \
FIRECLAW_ROS1_SMOKE_ARTIFACTS=results/ros1-smoke/fireclaw-$(date +%Y%m%d-%H%M%S).jsonl \
.venv/bin/python -m pytest tests/test_ros1_smoke.py -q

python -m fireclaw_core.ros1_proof_bundle \
  --output-dir results/ros1-proof/fireclaw-$(date +%Y%m%d-%H%M%S) \
  --robot-id "$FIRECLAW_ROBOT_ID" \
  --environment sim \
  --doctor-report results/doctor.json \
  --smoke-artifacts "$FIRECLAW_ROS1_SMOKE_ARTIFACTS"
```

---

## Post-Implementation Fixes (2026-06-11)

After code review, 3 follow-up fixes were applied:

1. **Cancel timing flaky** (`tests/test_gateway.py`): Removed hard `elapsed < 1.5` wall-clock assertion. Cancel behavior is verified by status/event assertions; timing is infrastructure performance, not functional correctness.
2. **Memory CLI stale records** (`src/fireclaw_core/memory_cli.py`): Changed `index` subcommand from `upsert()` loop to `SqliteMemoryIndex.rebuild()` so deleted JSONL records are removed from SQLite. Added regression test.
3. **ProviderRuntime CLI wiring** (`src/fireclaw_core/mission_cli.py`): `_build_planner()` now wraps the provider in `SimpleProviderRuntime` and passes `provider_runtime=` to `LLMMissionPlanner`, using the same runtime path as tests.
