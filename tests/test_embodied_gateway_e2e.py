"""Real gateway-to-gateway embodied e2e proof.

Proves the full MissionGateway -> RobotSubagentClient -> FireClawGateway HTTP
-> robot events chain works without any fake clients.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import request

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.subagent.subagent_client import RobotSubagentClient


class DeterministicPlanner:
    """Planner that always produces a single-subtask plan for the first available robot."""

    def plan(self, command: str, *, context: MissionPlannerContext) -> MissionPlanningResult:
        available = context.available_robots
        if not available:
            return MissionPlanningResult(
                status="no_robots",
                message="No online robots available.",
                intent=None,
                plan=None,
            )
        robot = available[0]
        return MissionPlanningResult(
            status="planned",
            message="Plan created.",
            intent="rescue",
            plan=MissionPlan(
                intent="rescue",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=robot.robot_id,
                        command=command,
                        floor=2,
                        capability_required="search_for_victims",
                        execution_group=0,
                    )
                ],
            ),
        )


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Operator-Scopes": "admin"},
    )
    with request.urlopen(req, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_real_gateway_to_gateway_embodied_e2e(tmp_path: Path):
    """Prove MissionGateway -> RobotSubagentClient -> FireClawGateway -> robot events chain."""
    # 1. Start a real robot-local FireClawGateway with simulator adapter
    robot_gw = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot_memory.jsonl"),
            event_path=str(tmp_path / "robot_events.jsonl"),
            task_queue_path=str(tmp_path / "robot_tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    robot_gw.start()
    try:
        robot_base_url = robot_gw.base_url

        # 2. Write robot registry pointing to the real gateway
        registry_path = tmp_path / "robots.json"
        registry_path.write_text(
            json.dumps({
                "robots": [
                    {
                        "robot_id": "robot-1",
                        "base_url": robot_base_url,
                        "capabilities": ["search_for_victims"],
                    }
                ]
            }),
            encoding="utf-8",
        )

        # 3. Set up all persistent stores
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

        # 4. Build MissionAgent with real RobotSubagentClient (no fake!)
        agent = build_mission_agent_from_paths(
            paths,
            operator_id="op-1",
            role="operator",
            planner=DeterministicPlanner(),
        )

        # Verify the agent is using a real RobotSubagentClient
        assert isinstance(agent.subagent_client, RobotSubagentClient)

        # 5. Create MissionGateway with the real client
        mission_config = MissionGatewayConfig(port=0)
        mission_gw = MissionGateway(
            mission_config,
            mission_agent=agent,
            registry=agent.registry,
            subagent_client=agent.subagent_client,
            task_registry=agent.task_registry,
            subagent_registry=agent.subagent_registry,
            session_lineage_store=agent._session_lineage_store,
        )
        mission_gw.start()
        try:
            base = mission_gw.base_url

            # 6. Submit mission "去二楼救人" through real HTTP
            status, body = _json_request(base, "POST", "/missions", {
                "command": "去二楼救人",
                "session_id": "e2e-gateway-test",
                "use_scheduler": False,
            })
            assert status == 202
            assert body["status"] == "planned"
            mission_id = body["mission_id"]

            # 7. Poll trace until terminal (real gateway needs time to execute)
            deadline = time.monotonic() + 15
            trace = None
            while time.monotonic() < deadline:
                _, trace = _json_request(base, "GET", f"/missions/{mission_id}/trace")
                mission_status = trace.get("status", "unknown")
                if mission_status in {"succeeded", "completed", "failed"}:
                    break
                time.sleep(0.2)

            assert trace is not None
            assert trace["status"] in {"succeeded", "completed"}, f"Trace: {trace}"

            # 8. Verify robot events were generated by the real gateway
            _, events = _json_request(base, "GET", f"/missions/{mission_id}/events")
            assert events["mission_id"] == mission_id

            # 9. Verify task registry has entries
            task_registry = agent.task_registry
            all_tasks = task_registry.list_records()
            mission_tasks = [t for t in all_tasks if t.requester_session_id == mission_id]
            assert len(mission_tasks) > 0, f"Expected task registry entries for {mission_id}"

            # 10. Verify subagent registry has entries (real RobotSubagentClient writes these)
            subagent_registry = agent.subagent_registry
            subagent_records = subagent_registry.list_by_parent_mission(mission_id)
            assert len(subagent_records) > 0, f"Expected subagent registry entries for {mission_id}"

            # 11. Verify session lineage exists for this mission
            lineage_store = agent._session_lineage_store
            assert lineage_store is not None
            lineage = lineage_store.get(mission_id)
            assert lineage is not None, f"Expected session lineage for {mission_id}"

            # 12. Verify task-flow
            task_flow_store = agent._task_flow_store
            assert task_flow_store is not None
            flows = task_flow_store.list_recent(limit=10)
            mission_flows = [f for f in flows if f.mission_id == mission_id]
            assert len(mission_flows) > 0, f"Expected task-flow for {mission_id}"

            # 13. Verify memory store has records
            memory_store = agent.mission_memory
            assert memory_store is not None
            records = memory_store.list_records(mission_id=mission_id)
            assert len(records) > 0, f"Expected memory records for {mission_id}"

            # 14. Verify the robot gateway actually processed the task
            # Check robot-local event ledger
            robot_events = robot_gw.events.list_events()
            task_events = [e for e in robot_events if e.get("type", "").startswith("task.")]
            assert len(task_events) > 0, "Expected robot-local task events from real gateway"

        finally:
            mission_gw.stop()
    finally:
        robot_gw.stop()
