# Mission Planning Audit Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist mission planning audit records to an append-only JSONL log so LLM mission planning guard decisions can be reconstructed after a run.

**Architecture:** Build on the current mission-level guard/audit implementation. `LLMMissionPlanner` and `MissionAgent` already produce and finalize `MissionPlanningAuditRecord`; this plan adds a JSONL sink, wires it through `MissionRuntimePaths`, and makes `serve` create a default `mission-planning-audit.jsonl` under `data_dir`.

**Tech Stack:** Python dataclasses/protocols, append-only JSONL stores, existing `MissionAgent` runtime wiring, pytest.

**Commit Policy:** Do not commit during execution unless the user explicitly asks. The current working tree already contains uncommitted mission planning guard/audit implementation; execute this plan on top of that state.

---

## File Structure

- Modify: `src/fireclaw_core/mission/mission_planning_audit.py`
  - Add `JsonlMissionPlanningAuditSink`, JSONL append logic, and a small read helper for tests/replay.
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
  - Add `mission_planning_audit` path to `MissionRuntimePaths`.
  - Construct `JsonlMissionPlanningAuditSink` and inject it into `MissionAgent`.
- Modify: `src/fireclaw_core/gateway/serve.py`
  - Set the default serve audit path to `data_dir / "mission-planning-audit.jsonl"`.
- Modify: `src/fireclaw_core/mission/mission_cli.py`
  - Add `--mission-planning-audit-path` to runtime path options.
  - Pass it into `MissionRuntimePaths` for direct CLI mission planning commands.
- Test: `tests/test_mission_planning_audit.py`
  - Cover JSONL append/read behavior.
- Test: `tests/test_mission_runtime.py`
  - Cover runtime path wiring and real `plan_and_submit()` audit persistence through a fake subagent.
- Test: `tests/test_serve.py`
  - Cover serve default audit sink path under `data_dir`.
- Test: `tests/test_mission_cli.py`
  - Cover CLI runtime path parsing into `MissionRuntimePaths`.

## Scope Notes

This phase is intentionally about **mission planning audit persistence**, not the full safety case.

In scope:
- Append-only JSONL persistence for finalized mission planning audit records.
- Runtime wiring for `MissionAgent`.
- Default path under `serve --data-dir`.
- CLI path option for direct `plan-mission` workflows.

Out of scope:
- Robot-local planning audit.
- SafetyGate decision audit.
- ROS endpoint/payload execution audit.
- A public HTTP endpoint for audit records.
- Indexing audit records into mission memory search. Raw tool calls may contain operator instructions and should not enter the FTS memory index by default.

## Task 1: JSONL Audit Sink

**Files:**
- Modify: `src/fireclaw_core/mission/mission_planning_audit.py`
- Test: `tests/test_mission_planning_audit.py`

- [ ] **Step 1: Write failing JSONL sink tests**

Append these tests to `tests/test_mission_planning_audit.py`:

```python
import json

from fireclaw_core.mission.mission_planning_audit import JsonlMissionPlanningAuditSink


def test_jsonl_mission_planning_audit_sink_appends_records(tmp_path):
    path = tmp_path / "audit" / "mission-planning-audit.jsonl"
    sink = JsonlMissionPlanningAuditSink(path)
    record = MissionPlanningAuditRecord(
        command="去二楼搜索",
        available_robots=[{"robot_id": "r1", "capabilities": ["search_for_victims"], "enabled": True, "zone": None}],
        tool_schema={"type": "function"},
        llm_tool_call={"id": "call-1", "name": "create_mission_plan", "arguments": {"intent": "search"}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-05T00:00:00+00:00",
        mission_id="mission-1",
    )

    sink.record(record)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["mission_id"] == "mission-1"
    assert data["command"] == "去二楼搜索"
    assert data["decisions"][0]["reason"] == "mission_plan_parsed"


def test_jsonl_mission_planning_audit_sink_reads_records_with_filters(tmp_path):
    path = tmp_path / "mission-planning-audit.jsonl"
    sink = JsonlMissionPlanningAuditSink(path)
    first = MissionPlanningAuditRecord(
        command="cmd-1",
        available_robots=[],
        tool_schema=None,
        llm_tool_call=None,
        decisions=[],
        final_status="error",
        final_message="blocked",
        created_at="2026-07-05T00:00:00+00:00",
        mission_id="mission-1",
    )
    second = MissionPlanningAuditRecord(
        command="cmd-2",
        available_robots=[],
        tool_schema=None,
        llm_tool_call=None,
        decisions=[
            GuardDecision(
                layer="validator",
                status="allow",
                reason="mission_plan_valid",
                message="Mission plan passed deterministic validation.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-05T00:00:01+00:00",
        mission_id="mission-2",
    )
    sink.record(first)
    sink.record(second)

    records = sink.list_records(mission_id="mission-2")

    assert len(records) == 1
    assert records[0].mission_id == "mission-2"
    assert records[0].decisions[0].reason == "mission_plan_valid"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_planning_audit.py::test_jsonl_mission_planning_audit_sink_appends_records tests/test_mission_planning_audit.py::test_jsonl_mission_planning_audit_sink_reads_records_with_filters -q
```

Expected: FAIL with import error for `JsonlMissionPlanningAuditSink`.

- [ ] **Step 3: Add JSONL sink implementation**

In `src/fireclaw_core/mission/mission_planning_audit.py`, add imports:

```python
import json
from pathlib import Path
```

Add this class and helpers after `MissionPlanningAuditSink`:

```python
class JsonlMissionPlanningAuditSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, record: MissionPlanningAuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def list_records(
        self,
        *,
        mission_id: str | None = None,
        limit: int | None = None,
    ) -> list[MissionPlanningAuditRecord]:
        if limit is not None and limit <= 0:
            return []
        records = self._read_all()
        if mission_id is not None:
            records = [record for record in records if record.mission_id == mission_id]
        if limit is not None:
            records = records[-limit:]
        return records

    def _read_all(self) -> list[MissionPlanningAuditRecord]:
        if not self.path.exists():
            return []
        records: list[MissionPlanningAuditRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                value = json.loads(stripped)
                if isinstance(value, dict):
                    records.append(_audit_record_from_dict(value))
        return records


def _audit_record_from_dict(value: dict[str, Any]) -> MissionPlanningAuditRecord:
    decisions_value = value.get("decisions")
    decisions = [
        _guard_decision_from_dict(item)
        for item in decisions_value
        if isinstance(item, dict)
    ] if isinstance(decisions_value, list) else []
    available_robots = value.get("available_robots")
    return MissionPlanningAuditRecord(
        command=str(value.get("command") or ""),
        available_robots=[dict(item) for item in available_robots if isinstance(item, dict)] if isinstance(available_robots, list) else [],
        tool_schema=value.get("tool_schema") if isinstance(value.get("tool_schema"), dict) else None,
        llm_tool_call=value.get("llm_tool_call") if isinstance(value.get("llm_tool_call"), dict) else None,
        decisions=decisions,
        final_status=str(value.get("final_status") or ""),
        final_message=str(value.get("final_message") or ""),
        created_at=str(value.get("created_at") or ""),
        mission_id=value.get("mission_id") if isinstance(value.get("mission_id"), str) else None,
    )


def _guard_decision_from_dict(value: dict[str, Any]) -> GuardDecision:
    details = value.get("details")
    return GuardDecision(
        layer=str(value.get("layer") or ""),
        status=str(value.get("status") or ""),
        reason=str(value.get("reason") or ""),
        message=str(value.get("message") or ""),
        details=dict(details) if isinstance(details, dict) else {},
    )
```

- [ ] **Step 4: Run JSONL sink tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_planning_audit.py -q
```

Expected: PASS.

## Task 2: Runtime Path Wiring

**Files:**
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Test: `tests/test_mission_runtime.py`

- [ ] **Step 1: Write failing runtime wiring tests**

Add imports to `tests/test_mission_runtime.py`:

```python
from unittest.mock import MagicMock

from fireclaw_core.mission.mission_planner import MissionPlan, MissionPlanningResult, MissionSubtask
from fireclaw_core.mission.mission_planning_audit import GuardDecision, JsonlMissionPlanningAuditSink, MissionPlanningAuditRecord
```

Append these helper/test blocks:

```python
class FakeRuntimeSubagentClient:
    def __init__(self):
        self.calls = []

    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-07-05T00:00:00+00:00",
            "state": {},
        }

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {
            "status": "accepted",
            "task_id": f"task-{entry.robot_id}",
            "session_id": kwargs.get("session_id"),
            "robot_id": entry.robot_id,
        }


def _mission_planning_audit_record(command="去二楼搜索"):
    return MissionPlanningAuditRecord(
        command=command,
        available_robots=[
            {"robot_id": "r1", "capabilities": ["search_for_victims"], "enabled": True, "zone": None}
        ],
        tool_schema={"type": "function"},
        llm_tool_call={"id": "call-1", "name": "create_mission_plan", "arguments": {"intent": "search"}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-05T00:00:00+00:00",
    )


def test_build_mission_agent_wires_mission_planning_audit_sink(tmp_path: Path):
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(registry_path, [
        {"robot_id": "r1", "base_url": "http://r1:8765", "capabilities": ["search_for_victims"]},
    ])
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_planning_audit=tmp_path / "mission-planning-audit.jsonl",
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op-a", role="operator")

    assert isinstance(agent.mission_planning_audit_sink, JsonlMissionPlanningAuditSink)
    assert agent.mission_planning_audit_sink.path == tmp_path / "mission-planning-audit.jsonl"


def test_runtime_mission_agent_persists_planning_audit_on_plan_and_submit(tmp_path: Path):
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(registry_path, [
        {"robot_id": "r1", "base_url": "http://r1:8765", "capabilities": ["search_for_victims"]},
    ])
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索",
            subtasks=[
                MissionSubtask(
                    robot_id="r1",
                    command="去2楼搜索受困人员",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=_mission_planning_audit_record(),
    )
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_planning_audit=tmp_path / "mission-planning-audit.jsonl",
    )
    agent = build_mission_agent_from_paths(paths, operator_id="op-a", role="operator", planner=planner)
    agent.subagent_client = FakeRuntimeSubagentClient()

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    records = JsonlMissionPlanningAuditSink(tmp_path / "mission-planning-audit.jsonl").list_records()
    assert len(records) == 1
    assert records[0].mission_id == "mission-1"
    assert records[0].decisions[-1].reason == "mission_plan_valid"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_runtime.py::test_build_mission_agent_wires_mission_planning_audit_sink tests/test_mission_runtime.py::test_runtime_mission_agent_persists_planning_audit_on_plan_and_submit -q
```

Expected: FAIL because `MissionRuntimePaths` has no `mission_planning_audit` field.

- [ ] **Step 3: Implement runtime path wiring**

In `src/fireclaw_core/mission/mission_runtime.py`, add import:

```python
from fireclaw_core.mission.mission_planning_audit import JsonlMissionPlanningAuditSink
```

Add the path field to `MissionRuntimePaths`:

```python
    mission_planning_audit: Path | None = None
```

Create the sink in `build_mission_agent_from_paths()`:

```python
    mission_planning_audit_sink = (
        JsonlMissionPlanningAuditSink(paths.mission_planning_audit)
        if paths.mission_planning_audit
        else None
    )
```

Pass it into `MissionAgent(...)`:

```python
        mission_planning_audit_sink=mission_planning_audit_sink,
```

- [ ] **Step 4: Run runtime wiring tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_runtime.py -q
```

Expected: PASS.

## Task 3: Serve Default Audit Path

**Files:**
- Modify: `src/fireclaw_core/gateway/serve.py`
- Test: `tests/test_serve.py`

- [ ] **Step 1: Write failing serve wiring test**

Add import to `tests/test_serve.py`:

```python
from fireclaw_core.mission.mission_planning_audit import JsonlMissionPlanningAuditSink
```

Append:

```python
def test_start_server_wires_default_mission_planning_audit_sink(tmp_path: Path):
    data_dir = tmp_path / "data"
    gw = start_server(data_dir=data_dir, port=0, planner_type="deterministic")
    try:
        sink = gw.mission_agent.mission_planning_audit_sink
        assert isinstance(sink, JsonlMissionPlanningAuditSink)
        assert sink.path == data_dir / "mission-planning-audit.jsonl"
    finally:
        gw.stop()
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_serve.py::test_start_server_wires_default_mission_planning_audit_sink -q
```

Expected: FAIL because `start_server()` does not set `mission_planning_audit`.

- [ ] **Step 3: Add default serve path**

In `src/fireclaw_core/gateway/serve.py`, update `MissionRuntimePaths(...)`:

```python
        mission_planning_audit=data_dir / "mission-planning-audit.jsonl",
```

- [ ] **Step 4: Run serve tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_serve.py -q
```

Expected: PASS.

## Task 4: Mission CLI Runtime Path Option

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_mission_cli.py`

- [ ] **Step 1: Write failing CLI path test**

Add this test to `tests/test_mission_cli.py`:

```python
def test_build_mission_runtime_paths_includes_mission_planning_audit_path(tmp_path):
    from argparse import Namespace
    from fireclaw_core.mission.mission_cli import _build_mission_runtime_paths

    robot_registry = tmp_path / "robots.json"
    mission_registry = tmp_path / "missions.jsonl"
    audit_path = tmp_path / "mission-planning-audit.jsonl"
    args = Namespace(
        robot_profile=None,
        robot_registry=str(robot_registry),
        mission_registry=str(mission_registry),
        memory_path=None,
        memory_index=None,
        task_registry=None,
        subagent_registry=None,
        session_lineage=None,
        task_flow=None,
        approval_path=None,
        mission_planning_audit_path=str(audit_path),
    )

    paths = _build_mission_runtime_paths(args)

    assert paths.mission_planning_audit == audit_path
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_build_mission_runtime_paths_includes_mission_planning_audit_path -q
```

Expected: FAIL because `MissionRuntimePaths` does not yet receive the audit path.

- [ ] **Step 3: Add CLI runtime option**

In `src/fireclaw_core/mission/mission_cli.py`, add to `_add_runtime_paths()`:

```python
    parser.add_argument("--mission-planning-audit-path", default=None, help="Path to mission planning audit JSONL.")
```

Update `_build_mission_runtime_paths()`:

```python
        mission_planning_audit=getattr(args, "mission_planning_audit_path", None),
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py -q
```

Expected: PASS.

## Task 5: Regression Sweep

**Files:**
- No new production files beyond Tasks 1-4.

- [ ] **Step 1: Run direct audit/runtime/serve/CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_planning_audit.py tests/test_mission_runtime.py tests/test_serve.py tests/test_mission_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run mission planning guard/audit focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py tests/test_mission_agent.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS with the existing skipped tests unchanged.

- [ ] **Step 4: Update memory record**

Append a dated implementation note to:

```text
memory/2026-07-05/fireclaw-mission-planning-audit-persistence.md
```

Include:
- files modified;
- exact verification commands and results;
- whether `data/` remained untracked;
- next recommended phase.

## Research Impact

This phase improves the evidence chain for FireClaw. The first guard/audit slice currently proves decisions at runtime, but without a default persistent sink the evidence can disappear after process exit. Persisting records as append-only JSONL supports later incident replay, ablation analysis, and safety-case documentation.

This still does not justify a broad physical safety claim. The audit only covers mission planning. Publication-level safety claims will require extending the same evidence model to robot-local planning, SafetyGate decisions, ROS action/service/topic payloads, cancellation, timeout, and emergency-stop state.

## Self-Review

- Spec coverage:
  - Persistent append-only audit storage is covered by Task 1.
  - Runtime injection through `MissionRuntimePaths` is covered by Task 2.
  - Default `serve --data-dir` path is covered by Task 3.
  - Direct mission CLI path support is covered by Task 4.
  - Focused and full verification are covered by Task 5.
- Placeholder scan:
  - No unfinished placeholder steps are present.
- Type consistency:
  - The path field is consistently named `mission_planning_audit`.
  - The CLI argument is consistently named `--mission-planning-audit-path`.
  - The concrete sink is consistently named `JsonlMissionPlanningAuditSink`.
