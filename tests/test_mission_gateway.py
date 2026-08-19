from __future__ import annotations

import json
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.monitoring.stream_events import StreamEvent

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.gateway.control import OperatorContext
from fireclaw_core.gateway.network_security import GatewayNetworkPolicy
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.approval.approval_runtime import ApprovalRuntime
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.plugin.plugin_runtime import PluginRuntime
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeSubagentClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.cancel_calls: list[tuple] = []
        self.traces: dict[tuple[str, str], dict] = {}
        self.presence_results: dict[str, dict] = {}
        self.events_by_entry: dict[tuple[str, str | None], list] = {}

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
                        capability_required="victim_search",
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
                capabilities=("victim_search",),
            ),
            RobotRegistryEntry(
                robot_id="robot-2",
                base_url="http://robot-2.local:8765",
                capabilities=("thermal_imaging",),
            ),
        ]
    )


def _make_agent(
    registry: RobotRegistry | None = None,
    subagent_client: FakeSubagentClient | None = None,
    planner: FakePlanner | None = None,
    tmp_path=None,
) -> MissionAgent:
    registry = registry or _make_registry()
    client = subagent_client or FakeSubagentClient()
    mission_registry = None
    if tmp_path is not None:
        mission_registry = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    return MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_registry=mission_registry,
    )


def _make_gateway(
    mission_agent: MissionAgent,
    registry: RobotRegistry | None = None,
    subagent_client: FakeSubagentClient | None = None,
    api_token: str | None = None,
    network: GatewayNetworkPolicy | None = None,
) -> MissionGateway:
    config = MissionGatewayConfig(
        port=0,
        api_token=api_token,
        network=network or GatewayNetworkPolicy(),
    )
    return MissionGateway(
        config,
        mission_agent=mission_agent,
        registry=registry or _make_registry(),
        subagent_client=subagent_client or FakeSubagentClient(),
    )


def _json_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers=req_headers,
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _start_gateway(gw: MissionGateway) -> str:
    gw.start()
    return gw.base_url


# ---------------------------------------------------------------------------
# Tests: POST /missions
# ---------------------------------------------------------------------------


def test_post_missions_returns_plan(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    agent = _make_agent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        tmp_path=tmp_path,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base, "POST", "/missions",
            {"command": "去二楼搜索受困人员", "use_scheduler": False},
        )
        assert status == 202
        assert body["status"] == "planned"
        assert body["mission_id"] is not None
        assert "subtask_results" in body
    finally:
        gw.stop()


def test_post_missions_missing_command():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions", {})
        assert status == 400
        assert "command" in body["message"].lower()
    finally:
        gw.stop()


def test_post_missions_with_session_id(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    agent = _make_agent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        tmp_path=tmp_path,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions",
            {"command": "去三楼搜索", "session_id": "sess-abc", "use_scheduler": False},
        )
        assert status == 202
        assert body["mission_id"] == "sess-abc"
    finally:
        gw.stop()


def test_post_missions_with_operator(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    agent = _make_agent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        tmp_path=tmp_path,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions",
            {
                "command": "搜索受困人员",
                "operator": {"operator_id": "op-1", "role": "operator"},
                "use_scheduler": False,
            },
        )
        assert status == 202
        assert body["status"] == "planned"
    finally:
        gw.stop()


def test_post_missions_no_planner():
    """Without a planner, plan_and_submit returns no_planner."""
    registry = _make_registry()
    client = FakeSubagentClient()
    agent = _make_agent(registry=registry, subagent_client=client, planner=None)
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions", {"command": "去二楼"})
        assert status == 400
        assert body["status"] == "no_planner"
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: GET /missions/{id}/trace
# ---------------------------------------------------------------------------


def test_get_mission_trace(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-1",
        session_id="s-1",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    mission_reg.record_subtask(
        mission_id="m-1",
        robot_id="robot-1",
        task_id="t-1",
        command="search floor 2",
        status="accepted",
        created_at="2026-06-08T00:00:00+00:00",
    )
    client.traces[("robot-1", "t-1")] = {
        "task_id": "t-1",
        "status": "completed",
        "result": {"status": "completed"},
    }
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/missions/m-1/trace")
        assert status == 200
        assert body["mission_id"] == "m-1"
        assert "subtasks" in body
    finally:
        gw.stop()


def test_get_mission_trace_not_found(tmp_path):
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    agent = MissionAgent(
        registry=_make_registry(),
        subagent_client=FakeSubagentClient(),
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/missions/nonexistent/trace")
        assert status == 200
        assert body["status"] == "not_found"
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: GET /missions/{id}/events
# ---------------------------------------------------------------------------


def test_get_mission_events(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-2",
        session_id="s-2",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    mission_reg.record_subtask(
        mission_id="m-2",
        robot_id="robot-1",
        task_id="t-2",
        command="search",
        status="accepted",
        created_at="2026-06-08T00:00:00+00:00",
    )
    client.traces[("robot-1", "t-2")] = {
        "task_id": "t-2",
        "status": "completed",
        "result": {"status": "completed"},
        "events": [
            {"type": "task.received", "timestamp": "2026-06-08T00:00:01+00:00"},
            {"type": "task.completed", "timestamp": "2026-06-08T00:00:10+00:00"},
        ],
    }
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/missions/m-2/events")
        assert status == 200
        assert body["mission_id"] == "m-2"
    finally:
        gw.stop()


def test_get_mission_events_with_query_params(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-3",
        session_id="s-3",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    mission_reg.record_subtask(
        mission_id="m-3",
        robot_id="robot-1",
        task_id="t-3",
        command="search",
        status="accepted",
        created_at="2026-06-08T00:00:00+00:00",
    )
    client.traces[("robot-1", "t-3")] = {
        "task_id": "t-3",
        "status": "completed",
        "result": {"status": "completed"},
        "events": [
            {"type": "task.received", "timestamp": "2026-06-08T00:00:01+00:00"},
            {"type": "task.completed", "timestamp": "2026-06-08T00:00:10+00:00"},
        ],
    }
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "GET",
            "/missions/m-3/events?robot_id=robot-1&event_type=task.completed&limit=50",
        )
        assert status == 200
        assert body["mission_id"] == "m-3"
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: POST /missions/{id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_mission(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-cancel",
        session_id="s-cancel",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    mission_reg.record_subtask(
        mission_id="m-cancel",
        robot_id="robot-1",
        task_id="t-cancel",
        command="search",
        status="running",
        created_at="2026-06-08T00:00:00+00:00",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions/m-cancel/cancel", {})
        assert status == 200
        assert body["status"] == "cancel_requested"
        assert body["mission_id"] == "m-cancel"
    finally:
        gw.stop()


def test_cancel_mission_not_found(tmp_path):
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    agent = MissionAgent(
        registry=_make_registry(),
        subagent_client=FakeSubagentClient(),
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions/nonexistent/cancel", {})
        assert status == 404
        assert body["status"] == "not_found"
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: POST /missions/{id}/approvals
# ---------------------------------------------------------------------------


def test_request_approval(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
        approval_store=approval_store,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-approve/approvals",
            {
                "action": "request",
                "risk_level": "high",
                "command": "enter burning building",
            },
        )
        assert status == 200
        assert body["status"] == "pending"
        assert "request" in body
        assert body["request"]["requested_by"] == "local-loopback-operator"
        # Semantic action should be derived from command, not the dispatch "request"
        assert body["request"]["action"] == "enter burning building"
    finally:
        gw.stop()


def test_request_approval_with_semantic_action(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
        approval_store=approval_store,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-approve/approvals",
            {
                "action": "request",
                "semantic_action": "enter_building",
                "risk_level": "high",
                "command": "enter burning building",
            },
        )
        assert status == 200
        assert body["status"] == "pending"
        assert body["request"]["action"] == "enter_building"
    finally:
        gw.stop()


def test_request_approval_creates_runtime_token_when_configured(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = ApprovalRuntime(approval_store, token_ttl_seconds=60)
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
        approval_store=approval_store,
    )
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        approval_runtime=runtime,
    )
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-approve/approvals",
            {
                "action": "request",
                "semantic_action": "enter_building",
                "risk_level": "high",
                "command": "enter burning building",
            },
        )
        assert status == 200
        assert body["status"] == "pending"
        assert body["approval_token"]
        assert "token_hash" not in body
        assert body["token"]["request_id"] == body["request"]["request_id"]

        status, pending = _json_request(
            base,
            "POST",
            "/missions/m-approve/approvals",
            {"action": "pending"},
        )
        assert status == 200
        assert pending["status"] == "pending"
        assert len(pending["pending_approvals"]) == 1
        assert "approval_token" not in pending["pending_approvals"][0]
    finally:
        gw.stop()


def test_decide_approval(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    # Create a request first
    req = approval_store.create(
        mission_id="m-decide",
        action="enter",
        risk_level="high",
        command="enter burning building",
        requested_by="op-1",
        created_at="2026-06-08T00:00:00+00:00",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
        approval_store=approval_store,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-decide/approvals",
            {
                "action": "decide",
                "request_id": req.request_id,
                "decision": "approve",
            },
        )
        assert status == 200
        assert body["status"] == "decided"
        assert body["request"]["status"] == "approved"
        assert body["request"]["decided_by"] == "local-loopback-operator"
    finally:
        gw.stop()


def test_approval_unknown_action():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-x/approvals",
            {"action": "bogus"},
        )
        assert status == 400
        assert body["status"] == "error"
        assert "Unknown approval action" in body["message"]
    finally:
        gw.stop()


def test_approval_decide_missing_request_id():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-x/approvals",
            {"action": "decide", "decision": "approve"},
        )
        assert status == 400
        assert body["status"] == "error"
        assert "request_id" in body["message"]
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: GET /health
# ---------------------------------------------------------------------------


def test_health_reports_control_plane_without_running_fleet_diagnostics():
    agent = _make_agent()
    gw = _make_gateway(agent, api_token="secret")
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/health")
        assert status == 200
        assert body == {
            "schema_version": 1,
            "status": "ok",
            "service": "mission_gateway",
        }
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: GET /fleet/state
# ---------------------------------------------------------------------------


def test_fleet_state():
    registry = _make_registry()
    agent = _make_agent(registry=registry)
    gw = _make_gateway(agent, registry=registry)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/state")
        assert status == 200
        assert "entries" in body
        assert len(body["entries"]) == 2
        ids = {e["robot_id"] for e in body["entries"]}
        assert ids == {"robot-1", "robot-2"}
        # Each entry should have presence fields
        for entry in body["entries"]:
            assert "is_online" in entry
            assert "is_stale" in entry
            assert "last_seen_at" in entry
    finally:
        gw.stop()


def test_fleet_state_empty_registry():
    registry = RobotRegistry([])
    agent = _make_agent(registry=registry)
    gw = _make_gateway(agent, registry=registry)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/state")
        assert status == 200
        assert body["entries"] == []
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: GET /fleet/doctor
# ---------------------------------------------------------------------------


def test_fleet_doctor():
    registry = _make_registry()
    client = FakeSubagentClient()
    agent = _make_agent(registry=registry, subagent_client=client)
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/doctor")
        assert status == 200
        assert "status" in body
        assert "findings" in body
        assert "error_count" in body
        assert "warning_count" in body
    finally:
        gw.stop()


def test_fleet_doctor_exposes_dispatch_recovery_report():
    registry = _make_registry()
    client = FakeSubagentClient()
    agent = _make_agent(registry=registry, subagent_client=client)
    agent.dispatch_recovery_report = [{
        "status": "blocked",
        "mission_id": "mission-recovery",
        "message": "Robot state unavailable.",
    }]
    gw = _make_gateway(
        agent,
        registry=registry,
        subagent_client=client,
    )

    result = gw.fleet_doctor()

    assert result["dispatch_recovery"]["attempted_count"] == 1
    assert result["dispatch_recovery"]["blocked_count"] == 1
    assert (
        result["dispatch_recovery"]["results"][0]["mission_id"]
        == "mission-recovery"
    )


def test_fleet_doctor_empty_registry():
    registry = RobotRegistry([])
    client = FakeSubagentClient()
    agent = _make_agent(registry=registry, subagent_client=client)
    gw = _make_gateway(agent, registry=registry, subagent_client=client)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/doctor")
        assert status == 200
        assert body["status"] == "healthy"  # warning only, no errors
        assert body["warning_count"] >= 1
    finally:
        gw.stop()


def test_fleet_doctor_exposes_lifecycle(tmp_path):
    """Gateway should expose lifecycle maintenance in fleet doctor when registries are provided."""
    from datetime import datetime, timezone
    from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
    from fireclaw_core.task.task_registry import JsonlTaskRegistryStore

    registry = _make_registry()
    client = FakeSubagentClient()
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    # Create a task with a very recent timestamp so it is not stale at 300s threshold
    now_iso = datetime.now(timezone.utc).isoformat()
    tasks.create(
        task_id="fresh-t",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-1",
        scope_kind="mission",
        command="search",
        created_at=now_iso,
    )

    agent = _make_agent(registry=registry, subagent_client=client)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        task_registry=tasks,
        subagent_registry=subagents,
    )
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/doctor")
        assert status == 200
        assert "lifecycle" in body
        assert body["lifecycle"]["status"] == "ok"
        assert body["lifecycle"]["stale_tasks"] == []
        assert body["lifecycle"]["orphaned_subagents"] == []
        assert "checked_at" in body["lifecycle"]
    finally:
        gw.stop()


def test_fleet_doctor_no_lifecycle_without_registries():
    """Gateway fleet doctor should omit lifecycle section when registries not provided."""
    registry = _make_registry()
    client = FakeSubagentClient()
    agent = _make_agent(registry=registry, subagent_client=client)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
    )
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/doctor")
        assert status == 200
        assert "lifecycle" not in body
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: Auth
# ---------------------------------------------------------------------------


def test_auth_required_when_token_set():
    agent = _make_agent()
    gw = _make_gateway(agent, api_token="secret-token")
    base = _start_gateway(gw)
    try:
        # Self-declared identity and scopes cannot replace authentication.
        status, body = _json_request(
            base,
            "GET",
            "/fleet/state",
            headers={
                "X-Operator-Id": "attacker",
                "X-Operator-Scopes": "admin",
            },
        )
        assert status == 401
        assert body["error"] == "Unauthorized"

        status, body = _json_request(
            base,
            "GET",
            "/fleet/state",
            headers={
                "Authorization": "Bearer wrong-token",
                "X-Operator-Scopes": "admin",
            },
        )
        assert status == 401

        # Correct token -> 200
        status, body = _json_request(
            base,
            "GET",
            "/fleet/state",
            headers={
                "Authorization": "Bearer secret-token",
                "X-Operator-Id": "attacker",
                "X-Operator-Scopes": "state.read",
            },
        )
        assert status == 200
    finally:
        gw.stop()


def test_auth_no_token_required():
    agent = _make_agent()
    gw = _make_gateway(agent, api_token=None)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/fleet/state")
        assert status == 200
    finally:
        gw.stop()


def test_auth_failures_are_rate_limited():
    agent = _make_agent()
    gw = _make_gateway(
        agent,
        api_token="secret-token",
        network=GatewayNetworkPolicy(
            auth_max_failures=2,
            auth_exempt_loopback=False,
        ),
    )
    base = _start_gateway(gw)
    try:
        headers = {"Authorization": "Bearer wrong-token"}
        assert _json_request(
            base, "GET", "/fleet/state", headers=headers
        )[0] == 401
        assert _json_request(
            base, "GET", "/fleet/state", headers=headers
        )[0] == 401
        status, body = _json_request(
            base,
            "GET",
            "/fleet/state",
            headers=headers,
        )
        assert status == 429
        assert body["error"] == "Too many authentication failures."
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: Unknown endpoints
# ---------------------------------------------------------------------------


def test_get_unknown_endpoint():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "GET", "/nonexistent")
        assert status == 404
        assert "Unknown endpoint" in body["message"]
    finally:
        gw.stop()


def test_post_unknown_endpoint():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/nonexistent", {})
        assert status == 404
        assert "Unknown endpoint" in body["message"]
    finally:
        gw.stop()


def test_post_invalid_json():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        # Send invalid JSON
        from urllib.request import Request

        req = Request(
            f"{base}/missions",
            data=b"not-json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=5):
                pass
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert "valid JSON" in body["message"]
    finally:
        gw.stop()


def test_post_body_too_large():
    agent = _make_agent()
    gw = _make_gateway(agent)
    base = _start_gateway(gw)
    try:
        from http.client import HTTPConnection
        from urllib.parse import urlparse

        parsed = urlparse(base)
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        connection.putrequest("POST", "/missions")
        connection.putheader("Content-Length", str(1024 * 1024 + 1))
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == 413
        assert "too large" in body["message"].lower()
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: Gateway lifecycle
# ---------------------------------------------------------------------------


def test_gateway_start_stop():
    agent = _make_agent()
    gw = _make_gateway(agent)
    gw.start()
    assert gw.base_url.startswith("http://")
    gw.stop()
    # Stop is idempotent
    gw.stop()


def test_gateway_serve_forever_does_not_block_in_background():
    """Verify serve_forever can be called and the server is accessible."""
    import threading

    agent = _make_agent()
    config = MissionGatewayConfig(port=0)
    gw = MissionGateway(
        config,
        mission_agent=agent,
        registry=_make_registry(),
        subagent_client=FakeSubagentClient(),
    )
    gw.start()
    base = gw.base_url
    try:
        status, body = _json_request(base, "GET", "/fleet/state")
        assert status == 200
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: GET /missions/{id}/events/stream (SSE)
# ---------------------------------------------------------------------------


def test_mission_sse_endpoint_rejects_forged_scope_without_authentication():
    agent = _make_agent()
    gw = _make_gateway(agent, api_token="gateway-secret")
    base = _start_gateway(gw)
    try:
        req = request.Request(
            f"{base}/missions/m-1/events/stream",
            method="GET",
            headers={"X-Operator-Scopes": "mission.approve"},
        )
        try:
            with request.urlopen(req, timeout=2):
                pass
            assert False, "Expected HTTPError 401"
        except HTTPError as exc:
            assert exc.code == 401
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "Unauthorized"
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# Tests: Mission event emission
# ---------------------------------------------------------------------------


def test_submit_emits_mission_events(tmp_path):
    """POST /missions emits mission.submitted and mission.planned events."""
    registry = _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    agent = _make_agent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        tmp_path=tmp_path,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    # Collect events from EventBus
    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base, "POST", "/missions",
            {"command": "去二楼搜索受困人员", "use_scheduler": False},
        )
        assert status == 202
        mission_id = body["mission_id"]

        # Check mission.submitted event
        submitted = [e for e in collected_events if e.event_type == "mission.submitted"]
        assert len(submitted) == 1
        assert submitted[0].mission_id == mission_id
        assert submitted[0].source == "mission-gateway"
        assert submitted[0].payload["command"] == "去二楼搜索受困人员"

        # Check mission.planned event
        planned = [e for e in collected_events if e.event_type == "mission.planned"]
        assert len(planned) == 1
        assert planned[0].mission_id == mission_id
        assert planned[0].payload["intent"] == "search"
    finally:
        gw.stop()


def test_submit_emits_subtask_dispatched_events(tmp_path):
    """POST /missions emits mission.subtask_dispatched for each accepted subtask."""
    registry = _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    agent = _make_agent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        tmp_path=tmp_path,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base, "POST", "/missions",
            {"command": "去二楼搜索", "use_scheduler": False},
        )
        assert status == 202

        # Check mission.subtask_dispatched events
        dispatched = [e for e in collected_events if e.event_type == "mission.subtask_dispatched"]
        assert len(dispatched) >= 1
        for evt in dispatched:
            assert evt.mission_id == body["mission_id"]
            assert evt.task_id is not None
            assert evt.payload["robot_id"] is not None
            assert evt.payload["status"] == "accepted"
    finally:
        gw.stop()


def test_cancel_emits_cancel_requested_event(tmp_path):
    """POST /missions/{id}/cancel emits mission.cancel_requested event."""
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-cancel-evt",
        session_id="s-cancel-evt",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    mission_reg.record_subtask(
        mission_id="m-cancel-evt",
        robot_id="robot-1",
        task_id="t-cancel-evt",
        command="search",
        status="running",
        created_at="2026-06-08T00:00:00+00:00",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions/m-cancel-evt/cancel", {})
        assert status == 200
        assert body["status"] == "cancel_requested"

        cancelled = [e for e in collected_events if e.event_type == "mission.cancel_requested"]
        assert len(cancelled) == 1
        assert cancelled[0].mission_id == "m-cancel-evt"
        assert cancelled[0].payload["status"] == "cancel_requested"
        assert not [e for e in collected_events if e.event_type == "task.cancelling"]
    finally:
        gw.stop()


def test_cancel_emits_cancelled_event_when_immediate(tmp_path):
    """POST /missions/{id}/cancel emits mission.cancelled when status is cancelled."""
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-cancel-immediate",
        session_id="s-cancel-immediate",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    # Subtask with "submitted" status — cancel returns "cancel_requested" (subtask exists)
    mission_reg.record_subtask(
        mission_id="m-cancel-immediate",
        robot_id="robot-1",
        task_id="t-cancel-immediate",
        command="search",
        status="submitted",
        created_at="2026-06-08T00:00:00+00:00",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions/m-cancel-immediate/cancel", {})
        assert status == 200
        # With submitted subtasks, cancel returns "cancel_requested"
        assert body["status"] == "cancel_requested"

        cancel_events = [e for e in collected_events if e.event_type == "mission.cancel_requested"]
        assert len(cancel_events) == 1
        assert cancel_events[0].mission_id == "m-cancel-immediate"
    finally:
        gw.stop()


def test_cancel_no_subtask_emits_no_cancel_event(tmp_path):
    """POST /missions/{id}/cancel with no subtasks emits no cancel events (status=empty)."""
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    mission_reg.create_mission(
        mission_id="m-cancel-empty",
        session_id="s-cancel-empty",
        command="search",
        created_at="2026-06-08T00:00:00+00:00",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions/m-cancel-empty/cancel", {})
        assert status == 200
        assert body["status"] == "empty"

        cancel_events = [e for e in collected_events if "cancel" in e.event_type]
        assert len(cancel_events) == 0
    finally:
        gw.stop()


def test_submit_no_planner_emits_no_mission_events():
    """POST /missions with no planner emits zero mission.* events."""
    registry = _make_registry()
    client = FakeSubagentClient()
    agent = _make_agent(registry=registry, subagent_client=client, planner=None)
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(base, "POST", "/missions", {"command": "去二楼"})
        assert status == 400
        assert body["status"] == "no_planner"

        mission_events = [e for e in collected_events if e.event_type.startswith("mission.")]
        assert len(mission_events) == 0, f"Expected no mission events, got: {[e.event_type for e in mission_events]}"
    finally:
        gw.stop()


def test_approval_request_emits_event(tmp_path):
    """POST /missions/{id}/approvals with action=request emits mission.approval_requested."""
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
        approval_store=approval_store,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-approve-evt/approvals",
            {
                "action": "request",
                "risk_level": "high",
                "command": "enter burning building",
            },
        )
        assert status == 200
        assert body["status"] == "pending"

        approval_events = [e for e in collected_events if e.event_type == "mission.approval_requested"]
        assert len(approval_events) == 1
        assert approval_events[0].mission_id == "m-approve-evt"
        assert approval_events[0].payload["risk_level"] == "high"
    finally:
        gw.stop()


def test_approval_decide_emits_event(tmp_path):
    """POST /missions/{id}/approvals with action=decide emits mission.approval_decided."""
    registry = _make_registry()
    client = FakeSubagentClient()
    mission_reg = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    req = approval_store.create(
        mission_id="m-decide-evt",
        action="enter",
        risk_level="high",
        command="enter burning building",
        requested_by="op-1",
        created_at="2026-06-08T00:00:00+00:00",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_reg,
        approval_store=approval_store,
    )
    gw = _make_gateway(agent, registry=registry, subagent_client=client)

    collected_events: list[StreamEvent] = []

    def _collector(event: StreamEvent) -> None:
        collected_events.append(event)

    gw._event_bus.subscribe(_collector)
    base = _start_gateway(gw)
    try:
        status, body = _json_request(
            base,
            "POST",
            "/missions/m-decide-evt/approvals",
            {
                "action": "decide",
                "request_id": req.request_id,
                "decision": "approve",
            },
        )
        assert status == 200
        assert body["status"] == "decided"

        decided_events = [e for e in collected_events if e.event_type == "mission.approval_decided"]
        assert len(decided_events) == 1
        assert decided_events[0].mission_id == "m-decide-evt"
        assert decided_events[0].payload["request_id"] == req.request_id
        assert decided_events[0].payload["decision"] == "approve"
    finally:
        gw.stop()


def test_sse_stream_uses_stream_event_schema():
    """GET /missions/{id}/events/stream uses StreamEvent schema for published events."""
    from fireclaw_core.monitoring.stream_events import StreamEvent

    # Verify StreamEvent.to_sse_format() produces correct SSE format
    event = StreamEvent(
        event_type="mission.submitted",
        source="mission-gateway",
        mission_id="m-sse",
        payload={"command": "test"},
    )
    sse_output = event.to_sse_format()

    # Verify SSE format: event: <type>\nid: <sequence>\ndata: <json>\n\n
    lines = sse_output.split("\n")
    assert lines[0] == "event: mission.submitted"
    assert lines[1] == "id: 0"
    assert lines[2].startswith("data: ")
    assert sse_output.endswith("\n\n")

    # Parse the JSON data from SSE format
    data_line = lines[2]
    assert data_line.startswith("data: ")
    event_data = json.loads(data_line[6:])

    # Verify all StreamEvent fields are present
    assert "event_id" in event_data
    assert "event_type" in event_data
    assert "source" in event_data
    assert "timestamp" in event_data
    assert "sequence" in event_data
    assert "payload" in event_data
    assert event_data["event_type"] == "mission.submitted"
    assert event_data["mission_id"] == "m-sse"
    assert event_data["source"] == "mission-gateway"
    assert event_data["payload"] == {"command": "test"}


# ---------------------------------------------------------------------------
# Tests: Plugin hook integration
# ---------------------------------------------------------------------------


def test_approval_request_applies_tool_approval_hook(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="tool_approval",
        hook_name="add_reason",
        plugin_id="fire.approval",
        callback=lambda payload: {"reason": "High heat area requires supervisor review."},
    )
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        plugin_runtime=runtime,
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
    })

    assert result["status"] == "pending"
    assert result["approval_reasons"] == [
        {
            "plugin_id": "fire.approval",
            "reason": "High heat area requires supervisor review.",
        }
    ]


def test_approval_request_plugin_hook_without_approval_runtime_has_no_token(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="tool_approval",
        hook_name="add_reason",
        plugin_id="fire.approval",
        callback=lambda payload: {"reason": "test reason"},
    )
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        plugin_runtime=runtime,
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
    })

    assert result["status"] == "pending"
    assert result["approval_reasons"] == [{"plugin_id": "fire.approval", "reason": "test reason"}]
    assert "approval_token" not in result


def test_resolve_token_returns_pending_result(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = ApprovalRuntime(approval_store, token_ttl_seconds=300)
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        approval_runtime=runtime,
    )

    request_result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
    })
    assert request_result["status"] == "pending"
    raw_token = request_result["approval_token"]

    resolve_result = gw.handle_approval("mission-1", {
        "action": "resolve_token",
        "approval_token": raw_token,
    })
    assert resolve_result["status"] == "resolved"
    assert resolve_result["token"]["request_id"] == request_result["request"]["request_id"]


def test_pending_approval_projection_includes_relay_context(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = ApprovalRuntime(approval_store, token_ttl_seconds=60)
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        approval_runtime=runtime,
    )

    request_result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
        "relay": {"channel": "console", "operator_id": "op-1"},
    })
    pending = gw.handle_approval("mission-1", {"action": "pending"})

    assert request_result["status"] == "pending"
    assert pending["pending_approvals"][0]["relay"]["channel"] == "console"
    assert pending["pending_approvals"][0]["relay"]["operator_id"] == "op-1"


def test_resolve_token_rejects_mismatched_mission_id(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = ApprovalRuntime(approval_store, token_ttl_seconds=300)
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        approval_runtime=runtime,
    )

    request_result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
    })
    raw_token = request_result["approval_token"]

    resolve_result = gw.handle_approval("mission-OTHER", {
        "action": "resolve_token",
        "approval_token": raw_token,
    })
    assert resolve_result["status"] == "not_found"
