# tests/test_gateway_structured_task.py
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_task_done(base_url: str, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        trace = _json_request(base_url, "GET", f"/tasks/{task_id}")
        if trace.get("result") is not None:
            return trace
        time.sleep(0.05)
    return _json_request(base_url, "GET", f"/tasks/{task_id}")


def _events_for_task(base_url: str, task_id: str) -> list[dict]:
    body = _json_request(base_url, "GET", f"/events?task_id={task_id}")
    return body.get("events", [])


def test_gateway_accepts_structured_task_payload(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "search_for_victims", "report_status"],
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])

        assert trace["structured_task"]["task_id"] == "structured-1"
        assert trace["result"]["structured_task"]["task_type"] == "search"
    finally:
        gateway.stop()


def test_gateway_rejects_invalid_structured_task_payload(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        try:
            _json_request(
                gateway.base_url,
                "POST",
                "/tasks",
                {
                    "command": "去2楼搜索受困人员",
                    "structured_task": {
                        "task_id": "bad-structured-task",
                        "task_type": "search",
                        "target": {"floor": 2},
                        "required_skills": [],
                    },
                },
            )
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert body["status"] == "error"
            assert "required_skills" in body["message"].lower()
        else:
            raise AssertionError("Expected HTTP 400 for invalid structured_task")
    finally:
        gateway.stop()


def test_gateway_accepts_primitive_composition_allowed_skills(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="debug-robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "导航到 x=2 y=0",
                "structured_task": {
                    "task_id": "primitive-allowed-1",
                    "task_type": "primitive_composition",
                    "target": {},
                    "required_skills": [],
                    "allowed_skills": ["navigate_to_floor", "report_status"],
                    "robot_id": "debug-robot-1",
                },
            },
        )
        trace = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}")

        assert accepted["status"] == "accepted"
        assert trace["structured_task"]["allowed_skills"] == ["navigate_to_floor", "report_status"]
    finally:
        gateway.stop()


def test_gateway_rejects_non_list_allowed_skills(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="debug-robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        try:
            _json_request(
                gateway.base_url,
                "POST",
                "/tasks",
                {
                    "command": "导航到 x=2 y=0",
                    "structured_task": {
                        "task_id": "primitive-allowed-bad",
                        "task_type": "primitive_composition",
                        "target": {},
                        "required_skills": [],
                        "allowed_skills": "navigate_to_floor",
                        "robot_id": "debug-robot-1",
                    },
                },
            )
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert body["status"] == "error"
            assert "allowed_skills must be a list" in body["message"]
        else:
            raise AssertionError("Expected HTTP 400 for non-list allowed_skills")
    finally:
        gateway.stop()


def test_gateway_robot_agent_mode_emits_robot_agent_events(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 搜索受困人员",
                "structured_task": {
                    "task_id": "structured-robot-agent-1",
                    "task_type": "search",
                    "target": {
                        "pose": {
                            "x": 2.0,
                            "y": 1.5,
                            "frame_id": "map",
                        }
                    },
                    "required_skills": ["navigate_to_point", "report_status"],
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])
        events = _events_for_task(gateway.base_url, accepted["task_id"])

        assert trace["result"]["status"] in {"completed", "succeeded"}
        assert any(event.get("type") == "robot_agent.plan_requested" for event in events)
        assert any(event.get("type") == "robot_agent.plan_accepted" for event in events)
    finally:
        gateway.stop()


def test_gateway_robot_agent_deliberation_executes_one_skill_per_turn(
    tmp_path,
):
    from fireclaw_core.agent.robot_deliberation import (
        RobotAgentDecision,
        RobotAgentDeliberationRuntime,
    )

    class Policy:
        def __init__(self):
            self.requests = []

        def decide(self, request):
            self.requests.append(request)
            if not request.observations:
                return RobotAgentDecision(
                    operation="execute_skill",
                    message="navigate",
                    tool_name="navigate_to_point",
                    inputs={
                        "x": 2.0,
                        "y": 1.5,
                        "frame_id": "map",
                    },
                )
            if len(request.observations) == 1:
                return RobotAgentDecision(
                    operation="execute_skill",
                    message="search",
                    tool_name="search_for_victims",
                    inputs={},
                )
            return RobotAgentDecision(
                operation="complete",
                message="search complete",
            )

    policy = Policy()
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="debug-robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.robot_agent_runtime = RobotAgentDeliberationRuntime(
        policy=policy,
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 搜索受困人员",
                "structured_task": {
                    "task_id": "robot-deliberation-1",
                    "task_type": "search",
                    "target": {
                        "pose": {
                            "x": 2.0,
                            "y": 1.5,
                            "frame_id": "map",
                        }
                    },
                    "required_skills": [
                        "navigate_to_point",
                        "search_for_victims",
                    ],
                },
            },
        )
        trace = _wait_for_task_done(
            gateway.base_url,
            accepted["task_id"],
        )
        events = _events_for_task(
            gateway.base_url,
            accepted["task_id"],
        )
    finally:
        gateway.stop()

    result = trace["result"]
    assert result["status"] == "completed"
    assert result["execution"]["status"] == "succeeded"
    assert [
        item["skill_name"] for item in result["execution"]["steps"]
    ] == ["navigate_to_point", "search_for_victims"]
    assert len(policy.requests) == 3
    assert (
        policy.requests[1].observations[0].tool_name
        == "navigate_to_point"
    )
    event_types = [event["type"] for event in events]
    assert "robot_agent.deliberation_started" in event_types
    assert "robot_agent.observation" in event_types
    assert "robot_agent.deliberation_finished" in event_types


def test_gateway_robot_agent_context_includes_skill_tools(tmp_path):
    captured_context = {}

    class CapturingPlanner:
        def plan(self, envelope, *, context, cancellation_requested=None):
            captured_context.update(context)
            from fireclaw_core.agent.robot_agent import RobotLocalPlan, RobotLocalPlanStep
            return RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
                    RobotLocalPlanStep("report_status", {"floor": 2}),
                ],
            )

    from fireclaw_core.agent.robot_agent import RobotAgentRuntime

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="debug-robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.robot_agent_runtime = RobotAgentRuntime(planner=CapturingPlanner())
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去二楼救人",
                "structured_task": {
                    "task_id": "task-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                },
            },
        )
        _wait_for_task_done(gateway.base_url, accepted["task_id"])
    finally:
        gateway.stop()

    names = [tool["function"]["name"] for tool in captured_context["skill_tools"]]
    assert "navigate_to_floor" in names
    assert "report_status" in names
    assert "return_to_safe_zone" in names
    metadata_names = [m["name"] for m in captured_context["skill_metadata"]]
    assert "navigate_to_floor" in metadata_names
    policy_manifest = captured_context["capability_policy"]
    assert policy_manifest["policy_id"] == "fireclaw.capability-policy:v1"
    assert "navigate_to_floor" in policy_manifest["after"]
    assert "report_status" in policy_manifest["after"]
    assert "assess_victim" in policy_manifest["before"]
    assert "assess_victim" not in policy_manifest["after"]


def test_gateway_robot_agent_mode_falls_back_for_high_risk_task(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-robot-agent-high-risk",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                    "risk_level": "high",
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])
        events = _events_for_task(gateway.base_url, accepted["task_id"])

        assert trace["result"]["status"] == "escalated"
        assert any(event.get("type") == "robot_agent.policy_rejected" for event in events)
    finally:
        gateway.stop()


def test_gateway_robot_agent_respects_profile_exposed_skills(tmp_path):
    """When a profile is loaded, only profile's llm_exposed_skills are exposed to LLM."""
    captured_context = {}

    class CapturingPlanner:
        def plan(self, envelope, *, context, cancellation_requested=None):
            captured_context.update(context)
            from fireclaw_core.agent.robot_agent import RobotLocalPlan, RobotLocalPlanStep
            return RobotLocalPlan(
                intent="search",
                steps=[RobotLocalPlanStep("navigate_to_floor", {"floor": 2})],
            )

    from fireclaw_core.agent.robot_agent import RobotAgentRuntime
    from fireclaw_core.agent.robot_profile import RobotCapabilityProfile

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="debug-robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    # Set a profile that only exposes navigate_to_floor
    gateway.robot_profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "report_status"),
        llm_exposed_skills=("navigate_to_floor",),  # Only navigate_to_floor exposed
    )
    gateway.robot_agent_runtime = RobotAgentRuntime(planner=CapturingPlanner())
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去二楼救人",
                "structured_task": {
                    "task_id": "task-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                },
            },
        )
        _wait_for_task_done(gateway.base_url, accepted["task_id"])
    finally:
        gateway.stop()

    # report_status is not in profile's llm_exposed_skills, so it should not appear
    names = [tool["function"]["name"] for tool in captured_context["skill_tools"]]
    assert "navigate_to_floor" in names
    assert "report_status" not in names  # Constrained by profile
    excluded = {
        decision["skill_name"]: decision
        for decision in captured_context["capability_policy"]["excluded"]
    }
    assert excluded["report_status"]["reason_code"] == (
        "skill_not_exposed_to_llm"
    )


def test_robot_agent_context_uses_runtime_verified_sensors(tmp_path):
    gateway = FireClawGateway(GatewayConfig(
        port=0,
        robot_agent_enabled=True,
        memory_path=str(tmp_path / "memory.jsonl"),
        workspace_skills_dir=None,
    ))
    gateway.robot.available_sensors = []

    original_get_state = gateway.robot.get_robot_state

    def get_state_with_runtime_sensor():
        state = original_get_state()
        state.available_sensors = ["rgb_camera"]
        return state

    gateway.robot.get_robot_state = get_state_with_runtime_sensor

    captured = {}

    class CapturingPlanner:
        def plan(self, envelope, *, context, **kwargs):
            captured["available_sensors"] = context["available_sensors"]
            from fireclaw_core.agent.robot_agent import RobotLocalPlan
            return RobotLocalPlan(
                intent="search",
                steps=[],
                confidence=1.0,
            )

    from fireclaw_core.agent.robot_agent import RobotAgentRuntime
    gateway.robot_agent_runtime = RobotAgentRuntime(planner=CapturingPlanner())
    agent = gateway._create_agent(task_id="task-1", session_id="session-1")
    from fireclaw_core.task.task_contract import StructuredRobotTask
    task = StructuredRobotTask(
        task_id="task-1",
        robot_id=gateway.config.robot_id,
        task_type="search",
        command="search",
        target={"floor": 2},
        required_skills=[],
    )

    gateway._run_robot_agent_structured_task(
        agent=agent,
        task_object=task,
        session_id="session-1",
        task_id="task-1",
    )

    assert captured["available_sensors"] == ["rgb_camera"]
