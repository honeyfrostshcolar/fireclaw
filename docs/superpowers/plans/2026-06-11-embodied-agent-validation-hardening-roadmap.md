# Embodied Agent Validation Hardening Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move FireClaw from simulator/code-level embodied-agent readiness to a stable, reproducible validation gate for ROS1-first firefighting robot experiments.

**Architecture:** Keep FireClaw scoped to embodied-agent robotics. Reuse OpenClaw patterns only where they strengthen robot execution: session/task lifecycle ownership, post-ready sidecars, doctor/repair flows, plugin hook boundaries, provider fallback, memory startup, and auditable control-plane state. Do not pursue OpenClaw's ACP/IDE/Web UI/plugin marketplace parity in this roadmap.

**Tech Stack:** Python 3.11, pytest, stdlib HTTP gateways, JSONL stores, SQLite FTS5, existing `MissionAgent`, `MissionGateway`, `FireClawGateway`, `RobotSubagentClient`, `MissionRuntimePaths`, `FleetDoctor`, `embodied_eval`, `embodied_proof_bundle`, and ROS1 proof modules.

---

## Current Capability Assessment

FireClaw currently covers the embodied-agent v1 loop needed for firefighting robot experiments:

```text
operator command
-> planner with memory/correction context
-> safety/approval gates
-> mission scheduler
-> robot subagent dispatch
-> robot-local gateway execution
-> simulator / ROS1 adapter boundary
-> unified events and replay
-> task, subagent, task-flow, and session-lineage ledgers
-> memory recording/retrieval/evaluation
-> proof bundle packaging
```

Verified on 2026-06-11:

- `.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_memory_learning_loop.py tests/test_embodied_proof_bundle.py -q`
  - `12 passed in 32.77s`
- `.venv/bin/python -m fireclaw_core.embodied_eval --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json --output-dir <tmp>/eval --adapter simulator`
  - `status="warn"`
  - exit code `2`
  - `plan_success_rate=1.0`
  - `dispatch_success_rate=0.5`
  - `terminal_event_rate=0.5`
  - `memory_record_rate=1.0`
- `.venv/bin/python -m fireclaw_core.embodied_proof_bundle ...` with generated eval artifacts
  - exit code `0`
- Strict proof bundle command with `--doctor-report results/doctor.json`
  - fails because `results/doctor.json` does not exist.

## OpenClaw Comparison

OpenClaw patterns already adapted enough for the current embodied-agent goal:

- task/session lifecycle ledgers and task-flow projection;
- session lineage / resume ownership style tracking;
- provider runtime and fallback boundaries;
- plugin control-plane fingerprinting and hook execution boundaries;
- approval handoff/idempotency concepts;
- gateway control plane and doctor-style diagnostics;
- memory retrieval/evaluation startup concepts.

OpenClaw patterns still worth adapting for robotics validation:

- post-ready sidecar shape for non-blocking doctor/report generation;
- lifecycle status normalization so worker-local terminal events always become mission-level terminal state;
- explicit doctor/repair flow that can produce proof-bundle artifacts from the same runtime paths;
- experiment gate outputs that are stable enough for paper/demo reporting.

OpenClaw features intentionally out of scope:

- full ACP session platform;
- IDE/TUI/Web dashboard parity;
- generic coding-agent UX;
- third-party plugin marketplace;
- native ROS2 adapter while ROS1 remains the active target;
- multi-channel chat/voice surfaces not needed for robot command execution.

---

## Task 1: Normalize Robot Task Lifecycle Into Mission Terminal State

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify if needed: `src/fireclaw_core/mission_registry.py`
- Test: `tests/test_mission_agent.py`
- Test: `tests/test_embodied_eval.py`

- [x] **Step 1: Write a failing unit test for retrieved-result terminal normalization**

Add a focused test near the mission trace tests in `tests/test_mission_agent.py`:

```python
def test_mission_trace_normalizes_robot_completed_retrieval_to_completed(tmp_path):
    registry = RobotRegistry()
    registry.add_robot("robot-1", "http://robot.local", capabilities=["monitor_environment"])
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="mission-1",
        session_id="session-1",
        command="检查一楼烟雾",
        created_at="2026-06-11T00:00:00+00:00",
    )
    mission_registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        command="检查一楼烟雾",
        status="accepted",
        created_at="2026-06-11T00:00:00+00:00",
    )

    class TraceClient:
        def get_task_trace(self, entry, task_id):
            return {
                "task_id": task_id,
                "status": "retrieved",
                "result": {"status": "retrieved", "message": "没有找到匹配的记忆记录。"},
                "queue_record": {"status": "completed"},
                "events": [{"type": "task.completed", "payload": {"status": "retrieved"}}],
            }

    agent = MissionAgent(
        registry=registry,
        subagent_client=TraceClient(),
        mission_registry=mission_registry,
    )

    trace = agent.mission_trace("mission-1")

    assert trace["status"] == "succeeded"
    assert trace["completed_subtask_count"] == 1
    assert trace["subtasks"][0]["status"] == "completed"
```

- [x] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_trace_normalizes_robot_completed_retrieval_to_completed -q
```

Expected before implementation: FAIL because mission status remains `running` or subtask status remains `retrieved`.

- [x] **Step 3: Implement mission-boundary status normalization**

In `src/fireclaw_core/mission_agent.py`, replace `_status_from_robot_trace()` with logic that prioritizes robot-local terminal wrappers before inner agent result statuses:

```python
def _status_from_robot_trace(trace: dict[str, Any]) -> str | None:
    queue_record = trace.get("queue_record")
    if isinstance(queue_record, dict):
        queue_status = queue_record.get("status")
        if isinstance(queue_status, str) and queue_status in TERMINAL_SUBTASK_STATUSES:
            return queue_status

    events = trace.get("events")
    if isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "task.completed":
                return "completed"
            if event_type == "task.cancelled":
                return "cancelled"
            if event_type == "task.failed":
                return "failed"

    status = trace.get("status")
    if isinstance(status, str) and status in TERMINAL_SUBTASK_STATUSES:
        return status

    result = trace.get("result")
    if isinstance(result, dict):
        result_status = result.get("status")
        if isinstance(result_status, str) and result_status in TERMINAL_SUBTASK_STATUSES:
            return result_status

    return None
```

Do not add `"retrieved"` to `TERMINAL_SUBTASK_STATUSES`; it is a robot-agent internal result, not a mission lifecycle status.

- [x] **Step 4: Tighten embodied eval expectations**

Update `tests/test_embodied_eval.py` so the committed fixture is a real gate:

```python
assert result["status"] == "pass"
assert result["metrics"]["plan_success_rate"] == 1.0
assert result["metrics"]["dispatch_success_rate"] == 1.0
assert result["metrics"]["terminal_event_rate"] == 1.0
assert result["metrics"]["memory_record_rate"] == 1.0
for name in [
    "mission-trace.json",
    "mission-events.json",
    "task-flow.json",
    "session-lineage.json",
    "memory-eval.json",
]:
    assert (output_dir / name).exists()
```

Update the CLI test:

```python
assert exit_code == 0
```

- [x] **Step 5: Verify lifecycle and eval**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_trace_normalizes_robot_completed_retrieval_to_completed tests/test_embodied_eval.py -q
.venv/bin/python -m fireclaw_core.embodied_eval --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json --output-dir /tmp/fireclaw-embodied-eval-check --adapter simulator
```

Expected:

- tests pass;
- CLI exits `0`;
- CLI prints `status="pass"`;
- dispatch and terminal rates are `1.0`.

- [x] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission_agent.py tests/test_mission_agent.py tests/test_embodied_eval.py
git commit -m "fix: normalize robot terminal traces for mission lifecycle"
```

---

## Task 2: Make Simulator Proof Bundle Acceptance Self-Contained

**Files:**
- Modify: `src/fireclaw_core/embodied_eval.py`
- Modify: `tests/test_embodied_eval.py`
- Modify: `docs/superpowers/plans/2026-06-11-embodied-agent-experiment-readiness-roadmap.md`
- Modify: `README.md`

- [x] **Step 1: Write a failing test that eval emits `doctor-report.json`**

Add to `tests/test_embodied_eval.py`:

```python
def test_run_embodied_eval_writes_doctor_report_for_bundle(tmp_path: Path):
    fixture_path = tmp_path / "scenarios.json"
    fixture_path.write_text(
        json.dumps([
            {
                "scenario_id": "rescue-floor-2",
                "command": "去二楼救人",
                "expected_floor": 2,
                "expected_capability": "search_for_victims",
                "min_memory_records": 1,
                "requires_terminal_status": True,
            }
        ]),
        encoding="utf-8",
    )

    output_dir = tmp_path / "results"
    run_embodied_eval(scenarios_path=fixture_path, output_dir=output_dir, adapter="simulator")

    doctor = json.loads((output_dir / "doctor-report.json").read_text(encoding="utf-8"))
    assert doctor["status"] in {"ok", "warn", "fail"}
    assert "findings" in doctor
```

- [x] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_eval.py::test_run_embodied_eval_writes_doctor_report_for_bundle -q
```

Expected before implementation: FAIL because `doctor-report.json` is missing.

- [x] **Step 3: Generate a simulator-scoped fleet doctor report inside eval**

In `src/fireclaw_core/embodied_eval.py`, collect a minimal doctor report after scenario execution and before returning the summary:

```python
doctor_report = {
    "status": "ok" if status == "pass" else "warn",
    "adapter": adapter,
    "scenario_count": len(scenario_results),
    "metrics": metrics,
    "findings": [
        {
            "severity": "info",
            "code": "simulator_eval_completed",
            "message": "Simulator embodied evaluation completed and produced proof artifacts.",
        }
    ],
}
_write_artifact(output_dir / "doctor-report.json", redact_dict(doctor_report))
```

This report is not a real robot doctor report. It is a simulator proof-bundle artifact that documents the eval run's health.

- [x] **Step 4: Update docs to distinguish simulator doctor from real robot doctor**

In `README.md`, change the readiness claim to:

```markdown
The simulator eval emits a `doctor-report.json` describing the eval run itself. Real firefighting robot validation still requires a ROS1 hardware or high-fidelity simulation doctor report collected from the deployment environment.
```

In `docs/superpowers/plans/2026-06-11-embodied-agent-experiment-readiness-roadmap.md`, update Final Acceptance to pass:

```bash
--doctor-report results/embodied-eval/local-sim/doctor-report.json
```

instead of:

```bash
--doctor-report results/doctor.json
```

- [x] **Step 5: Verify the exact final acceptance chain**

Run:

```bash
rm -rf results/embodied-eval/local-sim results/embodied-proof/local-sim
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
.venv/bin/python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/local-sim \
  --run-id local-sim \
  --mission-trace results/embodied-eval/local-sim/mission-trace.json \
  --mission-events results/embodied-eval/local-sim/mission-events.json \
  --task-flow results/embodied-eval/local-sim/task-flow.json \
  --session-lineage results/embodied-eval/local-sim/session-lineage.json \
  --memory-eval results/embodied-eval/local-sim/memory-eval.json \
  --doctor-report results/embodied-eval/local-sim/doctor-report.json
```

Expected: both commands exit `0`.

- [x] **Step 6: Commit**

```bash
git add src/fireclaw_core/embodied_eval.py tests/test_embodied_eval.py README.md docs/superpowers/plans/2026-06-11-embodied-agent-experiment-readiness-roadmap.md
git commit -m "feat: emit simulator doctor report for embodied proof bundles"
```

---

## Task 3: Add OpenClaw-Style Post-Ready Validation Sidecar

**Files:**
- Create: `src/fireclaw_core/validation_sidecar.py`
- Modify: `src/fireclaw_core/mission_runtime.py`
- Test: `tests/test_validation_sidecar.py`

- [x] **Step 1: Write a sidecar test for non-blocking artifact generation**

Create `tests/test_validation_sidecar.py`:

```python
from pathlib import Path

from fireclaw_core.validation_sidecar import ValidationSidecar


def test_validation_sidecar_runs_once_and_writes_report(tmp_path: Path):
    calls = []

    def run(output_dir: Path) -> dict:
        calls.append(output_dir)
        return {"status": "ok", "checks": [{"name": "runtime_paths", "status": "ok"}]}

    sidecar = ValidationSidecar(output_dir=tmp_path, run=run)

    result = sidecar.run_once()

    assert result["status"] == "ok"
    assert calls == [tmp_path]
    assert (tmp_path / "validation-report.json").exists()
```

- [x] **Step 2: Implement the sidecar**

Create `src/fireclaw_core/validation_sidecar.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from fireclaw_core.log_redaction import redact_dict


@dataclass
class ValidationSidecar:
    output_dir: Path
    run: Callable[[Path], dict[str, Any]]

    def run_once(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report = redact_dict(self.run(self.output_dir))
        (self.output_dir / "validation-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
```

- [x] **Step 3: Wire as an explicit optional runtime helper**

In `src/fireclaw_core/mission_runtime.py`, add a helper that constructs the sidecar from runtime paths without starting it implicitly:

```python
def build_validation_sidecar(output_dir: str | Path, run: Callable[[Path], dict[str, Any]]) -> ValidationSidecar:
    return ValidationSidecar(output_dir=Path(output_dir), run=run)
```

Do not auto-run it on mission gateway startup. Robot validation should be explicit, not hidden in startup.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_validation_sidecar.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/validation_sidecar.py src/fireclaw_core/mission_runtime.py tests/test_validation_sidecar.py
git commit -m "feat: add explicit validation sidecar for proof artifacts"
```

---

## Task 4: Define ROS1 High-Fidelity Proof Gate Without Implementing ROS2

**Files:**
- Modify: `docs/deployment/fireclaw-deployment-checklist.md`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`
- Create: `docs/superpowers/plans/2026-06-11-ros1-high-fidelity-proof-runbook.md`

- [x] **Step 1: Add the ROS1 proof criteria**

Create `docs/superpowers/plans/2026-06-11-ros1-high-fidelity-proof-runbook.md` with these required artifacts:

```markdown
# ROS1 High-Fidelity Proof Runbook

Required artifacts:

- `doctor-report.json` from the deployment environment;
- `ros1-smoke-summary.json` proving topic, service, and action transport;
- `mission-trace.json` from a real `MissionGateway`;
- `mission-events.json` from the same mission;
- `task-flow.json`;
- `session-lineage.json`;
- `memory-eval.json`;
- operator notes with secrets redacted;
- simulator or hardware environment description.

Pass criteria:

- no real hardware command is sent unless `adapter=ros1` is explicit;
- ROS1 smoke tests are skipped by default and run only with `FIRECLAW_RUN_ROS1_SMOKE=1`;
- at least one rescue command reaches a mission terminal status;
- proof bundle creation exits `0`;
- artifacts are stored outside git unless explicitly approved.
```

- [x] **Step 2: Update architecture docs**

In `docs/architecture/fireclaw-openclaw-alignment.md`, keep ROS2 out of scope and state:

```markdown
The next external validation step is a ROS1 high-fidelity or hardware proof run. This is not additional OpenClaw platform parity; it is robotics validation for FireClaw's embodied-agent claims.
```

- [x] **Step 3: Verify docs**

Run:

```bash
rg -n "ROS2 native adapter|ROS1 high-fidelity|doctor-report.json|FIRECLAW_RUN_ROS1_SMOKE" docs/architecture docs/deployment docs/superpowers/plans/2026-06-11-ros1-high-fidelity-proof-runbook.md
git diff --check
```

Expected: docs clearly keep ROS2 out of scope and define ROS1 proof artifacts.

- [x] **Step 4: Commit**

```bash
git add docs/deployment/fireclaw-deployment-checklist.md docs/architecture/fireclaw-openclaw-alignment.md docs/superpowers/plans/2026-06-11-ros1-high-fidelity-proof-runbook.md
git commit -m "docs: define ros1 high-fidelity proof gate"
```

---

## Task 5: Final Verification and Research-Readiness Notes

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`

- [x] **Step 1: Run focused validation**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_memory_learning_loop.py tests/test_embodied_proof_bundle.py tests/test_validation_sidecar.py -q
```

Expected: all selected tests pass.

- [x] **Step 2: Run final acceptance CLI chain**

Run:

```bash
rm -rf results/embodied-eval/local-sim results/embodied-proof/local-sim
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
.venv/bin/python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/local-sim \
  --run-id local-sim \
  --mission-trace results/embodied-eval/local-sim/mission-trace.json \
  --mission-events results/embodied-eval/local-sim/mission-events.json \
  --task-flow results/embodied-eval/local-sim/task-flow.json \
  --session-lineage results/embodied-eval/local-sim/session-lineage.json \
  --memory-eval results/embodied-eval/local-sim/memory-eval.json \
  --doctor-report results/embodied-eval/local-sim/doctor-report.json
```

Expected: both commands exit `0`.

- [x] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all default tests pass; ROS1 smoke remains skipped unless explicitly enabled.

- [x] **Step 4: Update README and memory with exact claims**

README claim should say:

```markdown
FireClaw can claim simulator-level embodied-agent experiment readiness when the focused validation tests, simulator eval CLI, and proof bundle CLI all pass.

FireClaw cannot claim real firefighting robot validation until a ROS1 high-fidelity or hardware proof run produces the required deployment artifacts.
```

Memory should include exact command outputs, pass counts, and any remaining external validation gap.

- [x] **Step 5: Commit**

```bash
git add README.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md
git commit -m "docs: record embodied validation readiness proof"
```

---

## Final Acceptance

Run:

```bash
.venv/bin/python -m pytest -q
rm -rf results/embodied-eval/local-sim results/embodied-proof/local-sim
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
.venv/bin/python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/local-sim \
  --run-id local-sim \
  --mission-trace results/embodied-eval/local-sim/mission-trace.json \
  --mission-events results/embodied-eval/local-sim/mission-events.json \
  --task-flow results/embodied-eval/local-sim/task-flow.json \
  --session-lineage results/embodied-eval/local-sim/session-lineage.json \
  --memory-eval results/embodied-eval/local-sim/memory-eval.json \
  --doctor-report results/embodied-eval/local-sim/doctor-report.json
```

Expected:

- default tests pass;
- simulator eval exits `0` with `status="pass"`;
- proof bundle exits `0`;
- bundle contains mission trace, events, task-flow, lineage, memory eval, and simulator doctor report;
- docs clearly state that real robot validation still requires a ROS1 high-fidelity or hardware artifact run.
