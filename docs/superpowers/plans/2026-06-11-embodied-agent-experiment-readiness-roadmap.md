# Embodied Agent Experiment Readiness Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move FireClaw from a code-complete ROS1-first embodied-agent framework to an experiment-ready system with a real gateway-to-gateway simulation proof, repeatable scenario evaluation, and clean documentation of what is required versus optional OpenClaw platform work.

**Architecture:** Keep FireClaw scoped to firefighting robot embodied-agent work. Do not pursue full OpenClaw ACP/IDE/Web/marketplace parity. Reuse only the OpenClaw patterns that matter for robot task execution, safety, lifecycle state, memory learning, provider fallback, plugin hooks, approvals, and auditable proof.

**Tech Stack:** Python 3.11, pytest, stdlib HTTP gateways, JSONL stores, SQLite FTS5, existing `MissionAgent`, `MissionGateway`, robot-local `FireClawGateway`, `RobotSubagentClient`, `MissionRuntimePaths`, `memory_cli`, and ROS1 proof bundle modules.

---

## Current Capability Assessment

FireClaw can now complete the intended code-level embodied-agent v1 loop:

```text
operator command
-> planner with memory/correction context
-> safety and approval gates
-> mission scheduler
-> robot subagent dispatch
-> robot-local gateway task execution
-> ROS1/simulator adapter boundary
-> unified event/replay streams
-> task registry, subagent registry, task-flow, session lineage
-> memory recording and retrieval evaluation
```

OpenClaw features already adapted where useful:

- task registry and task-flow registry;
- session lineage / resume ownership guard;
- provider runtime and fallback boundary;
- plugin control-plane fingerprints and executable hook boundaries;
- approval handoff/idempotency concepts;
- memory retrieval/evaluation patterns.

OpenClaw features not required for the current embodied-agent goal:

- full ACP/IDE session platform;
- generic coding-agent UX;
- full Web UI / WebSocket dashboard;
- third-party plugin marketplace;
- native ROS2 adapter while ROS1 remains the active target.

## Remaining Embodied-Agent Gaps

1. **Real gateway-to-gateway e2e proof:** current embodied e2e test uses `FakeSubagentClient`, so it does not prove `MissionGateway -> RobotSubagentClient -> FireClawGateway HTTP -> robot events`.
2. **Scenario-level experiment harness:** there is no repeatable rescue scenario benchmark that emits metrics suitable for a paper/demo report.
3. **Memory learning proof:** memory retrieval/eval exists, but there is no closed-loop test showing operator corrections or previous outcomes change planner context across repeated missions.
4. **Experiment proof bundle:** ROS1 proof bundles exist, but there is no higher-level embodied-agent proof bundle that packages mission trace, event replay, task-flow, lineage, memory eval, doctor output, and redacted notes.
5. **Documentation truth cleanup:** the 2026-06-11 field-readiness plan and architecture docs still contain stale pre-implementation wording.
6. **Real robot / high-fidelity simulation execution:** code can support it, but no artifact from an actual firefighting robot or high-fidelity ROS1 sim run exists yet. This is partly external to code.

---

### Task 1: Clean Up Stale Roadmap and Architecture Wording

**Files:**
- Modify: `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`

- [x] **Step 1: Rename stale pre-implementation sections**

In `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md`, replace:

```markdown
## Remaining Embodied-Agent Gaps
```

with:

```markdown
## Original Embodied-Agent Gaps Addressed By This Plan

The list below is the historical pre-implementation assessment. The tasks in this plan are now complete; current remaining work is tracked in `2026-06-11-embodied-agent-experiment-readiness-roadmap.md`.
```

- [x] **Step 2: Update stale architecture priorities**

In `docs/architecture/fireclaw-openclaw-alignment.md`, replace the old near-term priority list with:

```markdown
The next engineering priorities are:

1. Real MissionGateway -> RobotSubagentClient -> FireClawGateway simulator e2e proof.
2. Scenario-level experiment harness for rescue missions and memory learning.
3. Real ROS1 robot or high-fidelity simulation smoke artifact.

ROS2 native adapter, full ACP/IDE platform parity, plugin marketplace, and full Web UI remain out of scope for the current embodied-agent roadmap.
```

In `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`, remove any current recommendation that says session lineage/resume ownership guard is still missing. Keep real hardware proof and production automation as future work.

- [x] **Step 3: Verify stale text is gone**

Run:

```bash
rg -n "mission_cli.py still builds mostly bare|not yet an explicit operator command|single command that collects|Session lineage 和 resume ownership guard|Remaining Embodied-Agent Gaps" docs/architecture docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md
```

Expected: no misleading current-state statements remain. Historical mentions are allowed only if explicitly labeled as historical.

- [x] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md docs/architecture/fireclaw-openclaw-alignment.md docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md
git commit -m "docs: align embodied-agent roadmap with implemented state"
```

### Task 2: Add Real Gateway-to-Gateway Embodied E2E Proof

**Files:**
- Create: `tests/test_embodied_gateway_e2e.py`
- Modify only if necessary: `src/fireclaw_core/mission_agent.py`, `src/fireclaw_core/mission_gateway.py`

- [x] **Step 1: Write the failing e2e test**

Create `tests/test_embodied_gateway_e2e.py` that:

1. Starts a robot-local `FireClawGateway` with `adapter="simulator"`.
2. Writes a robot registry pointing to `robot_gateway.base_url`.
3. Builds a `MissionAgent` through `build_mission_agent_from_paths(...)`.
4. Starts a real `MissionGateway` with the default `RobotSubagentClient`.
5. Submits `去二楼救人` through HTTP `POST /missions`.
6. Polls `GET /missions/{mission_id}/trace` until terminal.
7. Asserts robot events, mission events, task registry, subagent registry, lineage, task-flow, and memory are populated.

Use this core assertion shape:

```python
assert submit["status"] == "planned"
assert trace["status"] in {"succeeded", "completed", "accepted"}
assert any(event["type"] == "task.accepted" for event in robot_events["events"])
assert task_registry.list_records()
assert subagent_registry.list_records()
assert lineage_store.get(mission_id) is not None
assert task_flow_store.list_recent(limit=10)
assert memory_store.list_records(mission_id=mission_id)
```

- [x] **Step 2: Run the new test**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py -q
```

Expected before any production fix: fail only if real gateway-to-gateway wiring has a missing projection or event aggregation path.

- [x] **Step 3: Fix only real wiring gaps**

If the test fails, keep fixes narrow:

- do not replace `RobotSubagentClient` with a fake;
- do not add ROS2 or Web UI behavior;
- only adjust mission/robot event aggregation, registry projection, or runtime path wiring needed for the real HTTP chain.

- [x] **Step 4: Verify related tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_subagent_client.py tests/test_mission_gateway.py tests/test_mission_gateway_client.py -q
```

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add tests/test_embodied_gateway_e2e.py src/fireclaw_core/mission_agent.py src/fireclaw_core/mission_gateway.py
git commit -m "test: prove real mission-to-robot gateway embodied chain"
```

### Task 3: Add Scenario-Level Experiment Harness

**Files:**
- Create: `src/fireclaw_core/embodied_eval.py`
- Create: `tests/test_embodied_eval.py`
- Create: `tests/fixtures/embodied_eval/rescue_scenarios.json`
- Modify: `docs/deployment/fireclaw-deployment-checklist.md`

- [x] **Step 1: Add scenario fixture**

Create `tests/fixtures/embodied_eval/rescue_scenarios.json`:

```json
[
  {
    "scenario_id": "rescue-floor-2",
    "command": "去二楼救人",
    "expected_floor": 2,
    "expected_capability": "search_for_victims",
    "min_memory_records": 1,
    "requires_terminal_status": true
  },
  {
    "scenario_id": "inspect-floor-1-smoke",
    "command": "检查一楼烟雾",
    "expected_floor": 1,
    "expected_capability": "monitor_environment",
    "min_memory_records": 1,
    "requires_terminal_status": true
  }
]
```

- [x] **Step 2: Write failing tests for metric output**

Create `tests/test_embodied_eval.py` with a temporary fixture and assert:

```python
result = run_embodied_eval(
    scenarios_path=fixture_path,
    output_dir=tmp_path / "results",
    adapter="simulator",
)
assert result["status"] in {"pass", "warn"}
assert result["scenario_count"] == 2
assert result["metrics"]["plan_success_rate"] >= 0.5
assert (tmp_path / "results" / "summary.json").exists()
assert (tmp_path / "results" / "scenarios.jsonl").exists()
```

- [x] **Step 3: Implement `run_embodied_eval(...)`**

Implement a pure-Python harness that runs deterministic simulator scenarios through existing mission/gateway helpers and writes:

- `summary.json`;
- `scenarios.jsonl`;
- `memory_eval.json` when memory evaluation input is provided.

Metric fields:

```python
{
  "scenario_count": int,
  "plan_success_rate": float,
  "dispatch_success_rate": float,
  "terminal_event_rate": float,
  "memory_record_rate": float,
  "average_latency_ms": float
}
```

- [x] **Step 4: Add CLI entry**

Allow:

```bash
python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
```

Return `0` for pass, `2` for warn/failing threshold, `1` for malformed fixture or runtime error.

- [x] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_eval.py tests/test_embodied_gateway_e2e.py -q
```

Expected: selected tests pass.

- [x] **Step 6: Commit**

```bash
git add src/fireclaw_core/embodied_eval.py tests/test_embodied_eval.py tests/fixtures/embodied_eval/rescue_scenarios.json docs/deployment/fireclaw-deployment-checklist.md
git commit -m "feat: add embodied rescue scenario evaluation harness"
```

### Task 4: Add Memory Learning Closed-Loop Proof

**Files:**
- Create: `tests/test_memory_learning_loop.py`
- Modify if necessary: `src/fireclaw_core/mission_agent.py`, `src/fireclaw_core/memory_retrieval.py`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`

- [x] **Step 1: Write test for correction-to-planner-context loop**

Create a test that:

1. Seeds mission memory with an operator correction for `去二楼救人`.
2. Rebuilds the SQLite memory index.
3. Builds `MissionAgent` with `mission_memory` and `memory_retriever`.
4. Uses a spy planner that records `context.retrieved_memories` and `context.operator_corrections`.
5. Calls `plan_and_submit(...)`.
6. Asserts the planner context includes the correction and relevant memory.

Core assertions:

```python
assert spy_planner.last_context is not None
assert spy_planner.last_context.retrieved_memories
assert spy_planner.last_context.operator_corrections
assert "二楼" in json.dumps(spy_planner.last_context.operator_corrections, ensure_ascii=False)
```

- [x] **Step 2: Run the test**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_learning_loop.py -q
```

Expected: pass if existing retrieval/correction loop is complete; fail if corrections are not visible to the planner in the indexed path.

- [x] **Step 3: Fix only missing loop behavior**

If needed, adjust `_retrieve_planner_context(...)` so:

- indexed memory retrieval handles current command;
- correction records remain available even when the FTS index path is used;
- plugin memory hooks still run after retrieval.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_learning_loop.py tests/test_memory_cli.py tests/test_mission_agent.py -q
```

Expected: selected tests pass.

- [x] **Step 5: Commit**

```bash
git add tests/test_memory_learning_loop.py src/fireclaw_core/mission_agent.py src/fireclaw_core/memory_retrieval.py docs/architecture/fireclaw-openclaw-alignment.md
git commit -m "test: prove memory learning reaches planner context"
```

### Task 5: Add Embodied Experiment Proof Bundle

**Files:**
- Create: `src/fireclaw_core/embodied_proof_bundle.py`
- Create: `tests/test_embodied_proof_bundle.py`
- Modify: `docs/deployment/fireclaw-deployment-checklist.md`

- [x] **Step 1: Write proof bundle test**

Create `tests/test_embodied_proof_bundle.py`:

```python
result = create_embodied_proof_bundle(
    output_dir=tmp_path / "bundle",
    run_id="local-sim-1",
    mission_trace={"mission_id": "m1", "status": "succeeded"},
    mission_events={"events": [{"type": "mission.succeeded"}]},
    task_flow={"mission_id": "m1"},
    session_lineage={"session_id": "m1"},
    memory_eval={"hit_rate": 1.0},
    doctor_report={"status": "pass"},
    notes="api_key=sk-abcdef1234567890",
)
assert result["status"] == "created"
assert (tmp_path / "bundle" / "summary.json").exists()
assert "sk-abcdef" not in (tmp_path / "bundle" / "summary.json").read_text()
```

- [x] **Step 2: Implement bundle writer**

Write redacted files:

- `summary.json`;
- `mission-trace.json`;
- `mission-events.json`;
- `task-flow.json`;
- `session-lineage.json`;
- `memory-eval.json`;
- `doctor-report.json`;
- `README.md`.

Use existing `redact_dict(...)` and follow `ros1_proof_bundle.py` style.

- [x] **Step 3: Add CLI**

Allow:

```bash
python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/local-sim-1 \
  --run-id local-sim-1 \
  --mission-trace results/embodied-eval/local-sim/mission-trace.json \
  --mission-events results/embodied-eval/local-sim/mission-events.json \
  --task-flow results/embodied-eval/local-sim/task-flow.json \
  --session-lineage results/embodied-eval/local-sim/session-lineage.json \
  --memory-eval results/embodied-eval/local-sim/memory-eval.json \
  --doctor-report results/doctor.json
```

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_proof_bundle.py tests/test_ros1_proof_bundle.py -q
```

Expected: selected tests pass.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/embodied_proof_bundle.py tests/test_embodied_proof_bundle.py docs/deployment/fireclaw-deployment-checklist.md
git commit -m "feat: package embodied mission proof artifacts"
```

### Task 6: Final Verification and Research-Readiness Notes

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`
- Modify: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`

- [x] **Step 1: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all default tests pass, ROS1 smoke skipped unless `FIRECLAW_RUN_ROS1_SMOKE=1`.

- [x] **Step 2: Document what can and cannot be claimed**

Add a concise section:

```markdown
FireClaw can claim code-level and simulator-level embodied-agent readiness when the gateway-to-gateway e2e test, scenario eval, memory learning loop, and proof bundle all pass.

FireClaw cannot claim real firefighting robot validation until a ROS1 hardware or high-fidelity simulation run produces a proof bundle with doctor output, smoke artifacts, mission trace, event replay, and operator notes.
```

- [x] **Step 3: Commit**

```bash
git add README.md docs/architecture/fireclaw-openclaw-alignment.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md
git commit -m "docs: define embodied-agent experiment readiness claims"
```

---

## Final Acceptance

Run:

```bash
.venv/bin/python -m pytest -q
python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/local-sim \
  --run-id local-sim \
  --mission-trace results/embodied-eval/local-sim/mission-trace.json \
  --mission-events results/embodied-eval/local-sim/mission-events.json \
  --task-flow results/embodied-eval/local-sim/task-flow.json \
  --session-lineage results/embodied-eval/local-sim/session-lineage.json \
  --memory-eval results/embodied-eval/local-sim/memory-eval.json \
  --doctor-report results/doctor.json
```

Expected:

- default tests pass;
- simulator scenario evaluation produces `summary.json`;
- embodied proof bundle contains redacted mission, memory, lifecycle, and doctor artifacts;
- docs distinguish simulator/code readiness from real robot validation.
