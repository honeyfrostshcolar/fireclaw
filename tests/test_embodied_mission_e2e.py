"""End-to-end embodied mission scenario gate.

Exercises the full pipeline:
  operator command -> MissionGateway.submit_mission -> MissionAgent.plan_and_submit
  -> Robot Agent dispatch -> events -> task-flow -> lineage -> memory
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib import request

from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_memory import MissionMemoryStore
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.infra.session_lineage import JsonlSessionLineageStore
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_flow_registry import JsonlTaskFlowRegistryStore
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore


# ---------------------------------------------------------------------------
# Test doubles (same pattern as test_mission_gateway.py)
# ---------------------------------------------------------------------------


class FakeSubagentClient:
    def __init__(self):
        self.calls = []
        self.cancel_calls = []
        self.traces = {}
        self.presence_results = {}
        self.events_by_entry = {}

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {
            "status": "accepted",
            "task_id": f"task-{entry.robot_id}",
            "session_id": kwargs.get("session_id"),
            "robot_id": entry.robot_id,
        }

    def get_task_trace(self, entry, task_id):
        return self.traces.get((entry.robot_id, task_id), {"status": "unknown"})

    def cancel_task(self, entry, task_id, *, operator=None):
        self.cancel_calls.append((entry, task_id, operator))
        return {
            "status": "cancel_requested",
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def get_events(self, entry, task_id=None, limit=100):
        key = (entry.robot_id, task_id)
        return self.events_by_entry.get(key, [])

    def check_presence(self, entry):
        if entry.robot_id in self.presence_results:
            return self.presence_results[entry.robot_id]
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-06-08T00:00:00+00:00",
            "state": {},
        }


class FakePlanner:
    def plan(self, command: str, *, context: MissionPlannerContext) -> MissionPlanningResult:
        available_ids = [e.robot_id for e in context.available_robots]
        if not available_ids:
            return MissionPlanningResult(
                status="no_robots",
                message="No online robots available.",
                intent=None,
                plan=None,
            )
        return MissionPlanningResult(
            status="planned",
            message="Plan created.",
            intent="search",
            plan=MissionPlan(
                intent="search",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=available_ids[0],
                        command=command,
                        floor=2,
                        capability_required="search_for_victims",
                        execution_group=0,
                    )
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry() -> RobotRegistry:
    return RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search_for_victims",),
            ),
        ]
    )


def _json_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


def test_embodied_mission_e2e_full_pipeline(tmp_path: Path):
    """End-to-end: operator command -> mission -> robot dispatch -> stores populated."""
    # 1. Set up all persistent stores
    mission_registry = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    task_registry = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagent_registry = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    lineage_store = JsonlSessionLineageStore(str(tmp_path / "lineage.jsonl"))
    task_flow_store = JsonlTaskFlowRegistryStore(tmp_path / "flows.jsonl")

    # 2. Create agent with ALL stores wired
    registry = _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_registry=mission_registry,
        mission_memory=memory_store,
        task_registry=task_registry,
        subagent_registry=subagent_registry,
        session_lineage_store=lineage_store,
        task_flow_store=task_flow_store,
    )

    # 3. Create MissionGateway with lifecycle stores
    config = MissionGatewayConfig(port=0)
    gw = MissionGateway(
        config,
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        task_registry=task_registry,
        subagent_registry=subagent_registry,
        session_lineage_store=lineage_store,
    )
    gw.start()
    try:
        base = gw.base_url

        # 4. Submit mission "去二楼救人"
        status, body = _json_request(base, "POST", "/missions", {
            "command": "去二楼救人",
            "session_id": "mission-e2e",
            "use_scheduler": False,
        })
        assert status == 202
        assert body["status"] == "planned"
        mission_id = body["mission_id"]
        assert mission_id is not None

        # 5. Verify mission trace
        status, trace = _json_request(base, "GET", f"/missions/{mission_id}/trace")
        assert status == 200
        assert trace["mission_id"] == mission_id

        # 6. Verify mission events
        status, events = _json_request(base, "GET", f"/missions/{mission_id}/events")
        assert status == 200
        assert events["mission_id"] == mission_id

        # 7. Verify task registry has entries (plan_and_submit + submit_subtask both write)
        all_tasks = task_registry.list_records()
        mission_tasks = [t for t in all_tasks if t.requester_session_id == mission_id]
        assert len(mission_tasks) > 0, (
            f"Expected task registry entries for mission {mission_id}, "
            f"got {len(all_tasks)} total records"
        )

        # 8. Verify task-flow store has a flow record for this mission
        flows = task_flow_store.list_recent(limit=10)
        mission_flows = [f for f in flows if f.mission_id == mission_id]
        assert len(mission_flows) > 0, (
            f"Expected task-flow record for mission {mission_id}, "
            f"got {len(flows)} recent flows"
        )
        assert mission_flows[0].command == "去二楼救人"

        # 9. Verify memory store has records
        records = memory_store.list_records(mission_id=mission_id)
        assert len(records) > 0

        # 10. Verify fleet doctor includes lifecycle when registries are provided
        status, doctor = _json_request(base, "GET", "/fleet/doctor")
        assert status == 200
        assert "lifecycle" in doctor

    finally:
        gw.stop()
