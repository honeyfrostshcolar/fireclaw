from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.agent.loop_checkpoint import (
    AgentLoopCheckpoint,
    AgentLoopPendingOperation,
    JsonlAgentLoopCheckpointStore,
)
from fireclaw_core.agent.robot_deliberation import (
    RobotAgentDecision,
    RobotAgentDeliberationRuntime,
    _task_contract_hash,
)
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.monitoring.event_ledger import EventLedger
from fireclaw_core.monitoring.stream_events import StreamEvent
from fireclaw_core.task.task_contract import StructuredRobotTask
from fireclaw_core.task.task_queue import JsonlTaskQueue


def _json_request_with_headers(base_url: str, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
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


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers=req_headers,
    )
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_error_request(base_url: str, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    try:
        return 200, _json_request(base_url, method, path, payload, headers=headers)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _write_high_risk_skill(skills_dir: Path) -> None:
    skills_dir.mkdir()
    (skills_dir / "smoke_entry.py").write_text(
        "import json\nprint(json.dumps({'ok': True, 'data': {'confirmed': True}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "smoke_entry.skill.json").write_text(
        json.dumps(
            {
                "name": "smoke_entry",
                "description": "High-risk dry-run skill.",
                "runtime": "subprocess",
                "command": [sys.executable, "smoke_entry.py"],
                "dry_run_only": True,
                "risk_level": "high",
            }
        ),
        encoding="utf-8",
    )


def _write_slow_policy_skill(skills_dir: Path) -> None:
    skills_dir.mkdir()
    (skills_dir / "slow_policy.py").write_text(
        "import json, time\n"
        "time.sleep(1)\n"
        "print(json.dumps({'ok': True, 'data': {'policy': 'slow'}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "slow_policy.skill.json").write_text(
        json.dumps(
            {
                "name": "slow_policy",
                "description": "Slow policy skill used to test cooperative cancellation.",
                "runtime": "subprocess",
                "command": [sys.executable, "slow_policy.py"],
                "timeout_seconds": 2,
                "dry_run_only": True,
                "risk_level": "low",
                "input_schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
            }
        ),
        encoding="utf-8",
    )


def _wait_for_task_result(gateway: FireClawGateway, task_id: str, timeout_seconds: float = 15.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        trace = gateway.task_trace(task_id)
        result = trace.get("result")
        if isinstance(result, dict):
            return result
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not finish before timeout.")


def _wait_for_event_type(gateway: FireClawGateway, task_id: str, event_type: str, timeout_seconds: float = 2.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for event in gateway.events.events_for_task(task_id):
            if event.get("type") == event_type:
                return event
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not emit {event_type} before timeout.")


def test_gateway_returns_health_and_state(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        health = _json_request(gateway.base_url, "GET", "/health")
        state = _json_request(gateway.base_url, "GET", "/state")
    finally:
        gateway.stop()

    assert health["status"] == "ok"
    assert health["robot_id"] == "robot-gateway"
    assert health["adapter"] == "simulator"
    assert state["robot_state"]["mode"] == "simulator"
    assert state["environment_state"]["reachable_floors"] == [1]
    assert state["runtime_paths"]["process_working_directory"] == str(
        Path.cwd().resolve()
    )
    assert state["runtime_paths"]["agent_workspace"] == str(
        gateway.config.deployment_profile.sandbox.workspace_root
    )
    assert state["runtime_paths"]["allowed_workspace_roots"]


def test_gateway_accepts_ros1_config_path_for_real_adapter_skeleton(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: gateway-ros1
remap:
  navigate_to_point:
    profile: move_base
    name: /move_base
""".lstrip(),
        encoding="utf-8",
    )

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="ros1",
            robot_id="ignored",
            ros1_config_path=str(config_path),
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
        )
    )

    assert gateway.state()["robot_state"]["mode"] == "ros1"
    assert gateway.state()["robot_state"]["robot_id"] == "gateway-ros1"


def test_gateway_runs_task_and_returns_recent_memory(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a"},
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        recent = _json_request(
            gateway.base_url,
            "GET",
            "/memory/recent?session_id=operator-a&limit=3",
        )
        task = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}")
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
        recent_events = _json_request(
            gateway.base_url,
            "GET",
            "/events/recent?session_id=operator-a&limit=20",
        )
    finally:
        gateway.stop()

    assert accepted["status"] == "accepted"
    assert accepted["task_id"].startswith("task-")
    assert accepted["session_id"] == "operator-a"
    assert result["status"] == "completed"
    assert result["task_id"] == accepted["task_id"]
    assert result["session"]["session_id"] == "operator-a"
    assert result["execution"]["steps"][0]["output"]["mode"] == "simulator"
    assert recent["records"][0]["command"] == "去坐标 (2.0, 1.5) 救人"
    assert task["result"]["task_id"] == result["task_id"]
    assert task["state"]["task"]["status"] == "completed"
    assert task["state"]["task"]["skill_count"] == 5
    assert task["state"]["task"]["action_count"] == 5
    assert task["state"]["skills"][0]["skill_name"] == "navigate_to_point"
    assert task["state"]["skills"][0]["action_ids"][0].startswith("action-")
    assert task["state"]["actions"][0]["action_type"] == "navigate_to_point"
    assert task["state"]["actions"][0]["status"] == "succeeded"
    event_types = [event["type"] for event in events["events"]]
    assert event_types[:8] == [
        "task.received",
        "operator.identified",
        "control.decision",
        "task.planned",
        "safety.decided",
        "capability.policy_preflight",
        "capability.policy_decided",
        "skill.started",
    ]
    assert event_types[-1] == "task.completed"
    assert event_types.count("resource.acquired") == 5
    assert event_types.count("resource.released") == 5
    assert event_types.count("action.requested") == 5
    assert event_types.count("skill.succeeded") == 5
    assert event_types.count("capability.policy_decided") == 5
    first_action = next(event for event in events["events"] if event["type"] == "action.requested")
    assert first_action["payload"]["task_id"] == accepted["task_id"]
    assert first_action["payload"]["skill_name"] == "navigate_to_point"
    assert recent_events["events"][0]["type"] == "task.completed"
    assert events["events"][6]["payload"]["skill_name"] == "navigate_to_point"
    assert events["events"][6]["payload"]["status"] == "allow"
    first_attempt = next(
        event
        for event in events["events"]
        if event["type"] == "skill.attempted"
    )
    assert first_attempt["payload"]["attempt_number"] == 1


def test_gateway_persists_task_queue_lifecycle(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
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
            {"command": "去坐标 (2.0, 1.5) 救人"},
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        trace = gateway.task_trace(accepted["task_id"])
        records = gateway.task_queue.list_records()
    finally:
        gateway.stop()

    assert result["status"] == "completed"
    assert [record.task_id for record in records] == [accepted["task_id"]]
    assert records[0].status == "completed"
    assert records[0].started_at is not None
    assert records[0].ended_at is not None
    assert records[0].result["status"] == "succeeded"
    assert trace["queue_record"]["task_id"] == accepted["task_id"]
    assert trace["queue_record"]["status"] == "completed"


def test_gateway_returns_existing_task_for_duplicate_dedupe_key(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        first = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "slow_policy", "dedupe_key": "operator-retry-1"},
        )
        second = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "slow_policy", "dedupe_key": "operator-retry-1"},
        )
    finally:
        gateway.stop()

    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["task_id"] == first["task_id"]
    assert second["dedupe_key"] == "operator-retry-1"
    assert [record.task_id for record in gateway.task_queue.list_records()] == [first["task_id"]]


def test_gateway_cancel_updates_task_queue_state(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        accepted = _json_request(gateway.base_url, "POST", "/tasks", {"command": "slow_policy"})
        cancel = _json_request(
            gateway.base_url,
            "POST",
            f"/tasks/{accepted['task_id']}/cancel",
            {"operator": {"operator_id": "operator-a", "role": "operator", "scopes": ["task.cancel"]}},
        )
        record = gateway.task_queue.get(accepted["task_id"])
    finally:
        gateway.stop()

    assert cancel["status"] == "cancel_requested"
    assert record.status in {"cancel_requested", "cancelled"}


def test_gateway_result_recording_preserves_prior_cancel_request(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    task_id = "task-cancel-race"
    session_id = "operator-a"
    gateway.task_queue.create(
        task_id=task_id,
        session_id=session_id,
        command="slow_policy",
        created_at="2026-06-09T00:00:00+00:00",
    )
    gateway.task_queue.update(task_id, status="cancel_requested")
    gateway._append_event(
        task_id=task_id,
        session_id=session_id,
        type="task.cancel_requested",
        payload={"status": "cancel_requested", "task_id": task_id},
    )

    gateway._record_result_events(
        task_id,
        session_id,
        {"status": "succeeded", "message": "worker completed after cancellation"},
    )

    record = gateway.task_queue.get(task_id)
    trace = gateway.task_trace(task_id)
    event_types = [event["type"] for event in trace["events"]]

    assert record.status == "cancelled"
    assert trace["status"] == "cancelled"
    assert "task.cancelled" in event_types
    assert "task.completed" not in event_types


def test_gateway_marks_stale_non_terminal_queue_records_lost_on_startup(tmp_path):
    queue_path = tmp_path / "tasks.jsonl"
    event_path = tmp_path / "events.jsonl"
    queue = JsonlTaskQueue(queue_path)
    queue.create(
        task_id="task-stale",
        session_id="session-1",
        command="去二楼救人",
        created_at="2026-06-08T01:00:00+00:00",
    )
    queue.update("task-stale", status="running", started_at="2026-06-08T01:00:01+00:00")

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(event_path),
            task_queue_path=str(queue_path),
            workspace_skills_dir=None,
        )
    )

    trace = gateway.task_trace("task-stale")
    state = gateway.state()

    assert gateway.task_queue.get("task-stale").status == "lost"
    assert trace["status"] == "lost"
    assert trace["queue_record"]["status"] == "lost"
    assert trace["events"][0]["type"] == "task.lost"
    assert state["task_queue"]["terminal_task_count"] == 1


def test_gateway_schedules_recoverable_robot_loop_on_startup(
    tmp_path,
    monkeypatch,
):
    queue_path = tmp_path / "tasks.jsonl"
    event_path = tmp_path / "events.jsonl"
    checkpoint_path = tmp_path / "agent-loops.jsonl"
    queue = JsonlTaskQueue(queue_path)
    queue.create(
        task_id="gateway-task-1",
        session_id="mission-1",
        command="去二楼",
        created_at="2026-07-29T01:00:00+00:00",
    )
    queue.update(
        "gateway-task-1",
        status="running",
        started_at="2026-07-29T01:00:01+00:00",
    )
    task = StructuredRobotTask(
        task_id="structured-1",
        mission_id="mission-1",
        robot_id="robot-gateway",
        task_type="navigate",
        command="去二楼",
        target={"floor": 2},
        required_skills=["navigate_to_floor"],
    )
    ledger = EventLedger(event_path)
    ledger.append(
        task_id="gateway-task-1",
        session_id="mission-1",
        type="task.structured_received",
        payload=task.to_dict(),
    )
    ledger.append(
        task_id="gateway-task-1",
        session_id="mission-1",
        type="operator.identified",
        payload={
            "operator_id": "operator-1",
            "role": "operator",
            "control_scopes": ["task.submit"],
        },
    )
    checkpoint_key = "robot:robot-gateway:task:structured-1"
    pending = AgentLoopPendingOperation(
        operation_id=f"{checkpoint_key}:operation:1",
        iteration=1,
        operation="execute_skill",
        decision={
            "operation": "execute_skill",
            "message": "navigate",
            "tool_name": "navigate_to_floor",
            "inputs": {"floor": 2},
        },
        prepared_at="2026-07-29T01:00:02+00:00",
    )
    JsonlAgentLoopCheckpointStore(checkpoint_path).append(
        AgentLoopCheckpoint(
            checkpoint_key=checkpoint_key,
            role="robot_agent",
            run_id=checkpoint_key,
            status="running",
            next_iteration=1,
            started_at="2026-07-29T01:00:02+00:00",
            elapsed_seconds=0.1,
            attempts=(),
            observations=(),
            pending_operation=pending,
            adapter_state={
                "task_contract_hash": _task_contract_hash(task),
                "task_id": task.task_id,
                "mission_id": task.mission_id,
                "robot_id": task.robot_id,
                "succeeded_skills": [],
                "skill_execution_count": 0,
                "context_query_count": 0,
            },
            updated_at="2026-07-29T01:00:02+00:00",
        )
    )

    class NeverPolicy:
        def decide(self, request):
            raise AssertionError("startup scheduling must not call policy")

    def build_runtime(gateway):
        return RobotAgentDeliberationRuntime(
            policy=NeverPolicy(),
            checkpoint_store=gateway.agent_loop_checkpoints,
        )

    scheduled = []

    def capture_worker(gateway, control, operator, *, resumed=False):
        scheduled.append((control, operator, resumed))

    monkeypatch.setattr(
        FireClawGateway,
        "_build_robot_agent_runtime",
        build_runtime,
    )
    monkeypatch.setattr(
        FireClawGateway,
        "_start_task_worker",
        capture_worker,
    )

    gateway = FireClawGateway(
        GatewayConfig(
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(event_path),
            task_queue_path=str(queue_path),
            robot_agent_checkpoint_path=str(checkpoint_path),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="llm",
        )
    )

    assert len(scheduled) == 1
    control, operator, resumed = scheduled[0]
    assert control.task_id == "gateway-task-1"
    assert control.structured_task == task.to_dict()
    assert operator.operator_id == "operator-1"
    assert resumed is True
    assert gateway.task_queue.get("gateway-task-1").status == "running"
    assert gateway.events.events_for_task("gateway-task-1")[-1]["type"] == (
        "task.resume_scheduled"
    )


def test_gateway_persists_physical_dispatch_boundaries(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            robot_agent_checkpoint_path=str(
                tmp_path / "agent-loops.jsonl"
            ),
            workspace_skills_dir=None,
        )
    )

    class SequencePolicy:
        def __init__(self):
            self.decisions = [
                    RobotAgentDecision(
                        operation="execute_skill",
                        message="report",
                        tool_name="report_status",
                        inputs={},
                ),
                RobotAgentDecision(
                    operation="complete",
                    message="done",
                ),
            ]

        def decide(self, request):
            return self.decisions.pop(0)

    gateway.robot_agent_runtime = RobotAgentDeliberationRuntime(
        policy=SequencePolicy(),
        checkpoint_store=gateway.agent_loop_checkpoints,
    )
    task = StructuredRobotTask(
        task_id="structured-audit",
        mission_id="mission-1",
            robot_id="robot-gateway",
            task_type="report",
            command="上报当前区域状态",
            target={
                "pose": {
                    "x": 2.0,
                    "y": 1.5,
                    "frame_id": "map",
                }
            },
        required_skills=["report_status"],
    )
    agent = gateway._create_agent(
        task_id="gateway-task-audit",
        session_id="mission-1",
    )

    result = gateway._run_robot_agent_structured_task(
        agent=agent,
        task_object=task,
        session_id="mission-1",
        task_id="gateway-task-audit",
    )

    assert result["status"] == "completed"
    events = gateway.events.events_for_task("gateway-task-audit")
    started = next(
        event
        for event in events
        if event["type"] == "robot_agent.skill_dispatch_started"
    )
    finished = next(
        event
        for event in events
        if event["type"] == "robot_agent.skill_dispatch_finished"
    )
    operation_id = started["payload"]["operation_id"]
    assert operation_id
    assert finished["payload"]["operation_id"] == operation_id
    assert finished["payload"]["output"]["execution"]["status"] == (
        "succeeded"
    )


def test_gateway_lists_skills(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        result = _json_request(gateway.base_url, "GET", "/skills")
    finally:
        gateway.stop()

    assert result["status"] == "skills"
    assert "navigate_to_point" in [skill["name"] for skill in result["skills"]]


def test_gateway_records_operator_and_control_decision_for_task_submission(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
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
                "command": "去坐标 (2.0, 1.5) 救人",
                "session_id": "operator-a",
                "operator": {
                    "operator_id": "op-1",
                    "display_name": "Operator One",
                    "role": "operator",
                },
            },
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
    finally:
        gateway.stop()

    assert result["status"] == "completed"
    event_types = [event["type"] for event in events["events"]]
    assert event_types[:3] == ["task.received", "operator.identified", "control.decision"]
    operator_event = events["events"][1]
    decision_event = events["events"][2]
    assert operator_event["payload"]["operator_id"] == "local-loopback-operator"
    assert operator_event["payload"]["role"] == "admin"
    assert operator_event["payload"]["source"] == "gateway_auth:loopback"
    assert decision_event["payload"]["status"] == "allow"
    assert decision_event["payload"]["action"] == "task.submit"


def test_gateway_sync_run_agent_still_returns_completed_result(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
        )
    )

    result = gateway.run_agent(
        "去坐标 (2.0, 1.5) 救人",
        session_id="operator-a",
    )

    assert result["status"] == "succeeded"
    assert result["task_id"].startswith("task-")
    trace = gateway.task_trace(result["task_id"])
    assert trace["status"] == "completed"
    assert trace["result"]["status"] == "completed"
    assert trace["result"]["raw_status"] == "succeeded"
    assert trace["queue_record"]["status"] == "completed"
    assert trace["events"][-1]["type"] == "task.completed"


def test_gateway_confirms_pending_high_risk_skill(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_high_risk_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        pending = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "运行 smoke_entry", "session_id": "operator-a"},
        )
        pending_result = _wait_for_task_result(gateway, pending["task_id"])
        confirmed = _json_request(
            gateway.base_url,
            "POST",
            "/confirm",
            {"session_id": "operator-a", "operator": {"operator_id": "supervisor-1", "role": "supervisor"}},
        )
        confirmed_result = _wait_for_task_result(gateway, confirmed["task_id"])
        pending_events = _json_request(gateway.base_url, "GET", f"/tasks/{pending['task_id']}/events")
        confirmed_events = _json_request(
            gateway.base_url,
            "GET",
            f"/tasks/{confirmed['task_id']}/events",
        )
    finally:
        gateway.stop()

    assert pending["status"] == "accepted"
    assert pending["task_id"].startswith("task-")
    assert pending_result["status"] == "escalated"
    assert pending_result["raw_status"] == "awaiting_confirmation"
    assert pending_result["execution"] is None
    assert confirmed["status"] == "accepted"
    assert confirmed["task_id"].startswith("task-")
    assert confirmed_result["status"] == "completed"
    assert confirmed_result["confirmation"]["status"] == "confirmed"
    assert confirmed_result["execution"]["steps"][0]["skill_name"] == "smoke_entry"
    assert "confirmation.pending" in [event["type"] for event in pending_events["events"]]
    assert "confirmation.confirmed" in [event["type"] for event in confirmed_events["events"]]
    assert "authorization.requested" in [event["type"] for event in pending_events["events"]]
    assert "authorization.approved" in [event["type"] for event in confirmed_events["events"]]


def test_gateway_ignores_payload_operator_role_during_confirmation(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_high_risk_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        pending = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "运行 smoke_entry", "session_id": "operator-a", "operator": {"operator_id": "op-1", "role": "operator"}},
        )
        pending_result = _wait_for_task_result(gateway, pending["task_id"])
        status_code, confirmed = _json_error_request(
            gateway.base_url,
            "POST",
            "/confirm",
            {"session_id": "operator-a", "operator": {"operator_id": "op-1", "role": "operator"}},
        )
        confirmed_result = _wait_for_task_result(gateway, confirmed["task_id"])
        confirmed_events = _json_request(
            gateway.base_url,
            "GET",
            f"/tasks/{confirmed['task_id']}/events",
        )
    finally:
        gateway.stop()

    assert pending_result["status"] == "escalated"
    assert pending_result["raw_status"] == "awaiting_confirmation"
    assert status_code == 200
    assert confirmed_result["status"] == "completed"
    assert "authorization.approved" in [
        event["type"] for event in confirmed_events["events"]
    ]


def test_gateway_expires_pending_high_risk_authorization_before_confirm(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_high_risk_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            authorization_expiry_seconds=0,
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        pending = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "运行 smoke_entry", "session_id": "operator-a"},
        )
        _wait_for_task_result(gateway, pending["task_id"])
        status_code, expired = _json_error_request(
            gateway.base_url,
            "POST",
            "/confirm",
            {"session_id": "operator-a", "operator": {"operator_id": "supervisor-1", "role": "supervisor"}},
        )
        pending_events = _json_request(gateway.base_url, "GET", f"/tasks/{pending['task_id']}/events")
    finally:
        gateway.stop()

    assert status_code == 403
    assert expired["status"] == "expired"
    assert "authorization.expired" in [event["type"] for event in pending_events["events"]]


def test_gateway_cancels_active_task_between_skills(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人 使用 slow_policy",
                "session_id": "operator-a",
            },
        )
        _wait_for_event_type(gateway, accepted["task_id"], "skill.started")
        cancel = _json_request(gateway.base_url, "POST", f"/tasks/{accepted['task_id']}/cancel")
        result = _wait_for_task_result(gateway, accepted["task_id"])
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
    finally:
        gateway.stop()

    event_types = [event["type"] for event in events["events"]]
    skill_names = [
        event["payload"].get("skill_name")
        for event in events["events"]
        if event["type"] == "skill.started"
    ]
    assert cancel["status"] == "cancel_requested"
    assert cancel["task_id"] == accepted["task_id"]
    assert result["status"] == "cancelled"
    assert result["message"] == "任务已取消。"
    assert "task.cancel_requested" in event_types
    assert "task.cancelled" in event_types
    assert skill_names == ["slow_policy"]


def test_gateway_ignores_payload_observer_role_when_cancelling(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人 使用 slow_policy",
                "session_id": "operator-a",
            },
        )
        _wait_for_event_type(gateway, accepted["task_id"], "skill.started")
        status_code, cancelled = _json_error_request(
            gateway.base_url,
            "POST",
            f"/tasks/{accepted['task_id']}/cancel",
            {"operator": {"operator_id": "observer-1", "role": "observer"}},
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
    finally:
        gateway.stop()

    assert status_code == 200
    assert cancelled["status"] == "cancel_requested"
    assert result["status"] == "cancelled"
    assert "task.cancel_requested" in [event["type"] for event in events["events"]]


def test_gateway_rejects_second_execution_task_when_robot_is_busy(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        first = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人 使用 slow_policy",
                "session_id": "operator-a",
            },
        )
        _wait_for_event_type(gateway, first["task_id"], "skill.started")
        status_code, busy = _json_error_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去坐标 (3.0, 2.0) 救人", "session_id": "operator-b"},
        )
        state = _json_request(gateway.base_url, "GET", "/state")
        gateway.cancel_task(first["task_id"])
        _wait_for_task_result(gateway, first["task_id"])
    finally:
        gateway.stop()

    assert status_code == 409
    assert busy["status"] == "busy"
    assert busy["active_task_id"] == first["task_id"]
    assert busy["capacity"]["max_active_execution_tasks"] == 1
    assert state["task_capacity"]["active_execution_tasks"] == 1
    assert state["task_capacity"]["max_active_execution_tasks"] == 1
    assert state["active_tasks"][0]["task_id"] == first["task_id"]


def test_gateway_admin_emergency_stop_cancels_active_task_and_records_audit_events(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="mock-ros1",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        active = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人 使用 slow_policy",
                "session_id": "operator-a",
            },
        )
        _wait_for_event_type(gateway, active["task_id"], "skill.started")
        stopped = _json_request(
            gateway.base_url,
            "POST",
            "/emergency-stop",
            {
                "session_id": "operator-a",
                "reason": "smoke flashover risk",
                "operator": {"operator_id": "admin-1", "role": "admin"},
            },
        )
        result = _wait_for_task_result(gateway, active["task_id"])
        state = _json_request(gateway.base_url, "GET", "/state")
        recent_events = _json_request(gateway.base_url, "GET", "/events/recent?session_id=operator-a&limit=20")
        task_events = _json_request(gateway.base_url, "GET", f"/tasks/{active['task_id']}/events")
    finally:
        gateway.stop()

    assert stopped["status"] == "emergency_stopped"
    assert stopped["reason"] == "smoke flashover risk"
    assert stopped["cancelled_task_ids"] == [active["task_id"]]
    assert stopped["robot_result"]["status"] == "emergency_stopped"
    assert result["status"] == "cancelled"
    assert state["emergency_stop"]["active"] is True
    assert state["emergency_stop"]["reason"] == "smoke flashover risk"
    assert state["robot_state"]["online"] is False
    recent_types = [event["type"] for event in recent_events["events"]]
    task_types = [event["type"] for event in task_events["events"]]
    assert "emergency_stop.requested" in recent_types
    assert "emergency_stop.activated" in recent_types
    assert "task.cancel_requested" in task_types
    assert "task.cancelled" in task_types


def test_gateway_ignores_payload_operator_role_for_emergency_stop(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="mock-ros1",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
    )
    gateway.start()
    try:
        active = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人 使用 slow_policy",
                "session_id": "operator-a",
            },
        )
        _wait_for_event_type(gateway, active["task_id"], "skill.started")
        status_code, stopped = _json_error_request(
            gateway.base_url,
            "POST",
            "/emergency-stop",
            {
                "session_id": "operator-a",
                "reason": "operator attempted stop",
                "operator": {"operator_id": "op-1", "role": "operator"},
            },
        )
        result = _wait_for_task_result(gateway, active["task_id"])
        state = _json_request(gateway.base_url, "GET", "/state")
        recent_events = _json_request(gateway.base_url, "GET", "/events/recent?session_id=operator-a&limit=20")
    finally:
        gateway.stop()

    assert status_code == 200
    assert stopped["status"] == "emergency_stopped"
    assert result["status"] == "cancelled"
    assert state["emergency_stop"]["active"] is True
    assert "emergency_stop.activated" in [
        event["type"] for event in recent_events["events"]
    ]


def test_gateway_events_endpoint_returns_recent_events(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a"},
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        events_response = _json_request(gateway.base_url, "GET", "/events")
    finally:
        gateway.stop()

    assert result["status"] == "completed"
    assert "events" in events_response
    assert isinstance(events_response["events"], list)
    assert len(events_response["events"]) > 0
    # latest events are returned in reverse order
    assert events_response["events"][0]["type"] == "task.completed"
    assert events_response["events"][0]["task_id"] == accepted["task_id"]


def test_gateway_events_endpoint_filters_by_task_id(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
            max_active_execution_tasks=2,
        )
    )
    gateway.start()
    try:
        first = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a"},
        )
        second = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去坐标 (3.0, 2.0) 搜索", "session_id": "operator-b"},
        )
        _wait_for_task_result(gateway, first["task_id"])
        _wait_for_task_result(gateway, second["task_id"])
        filtered = _json_request(
            gateway.base_url,
            "GET",
            f"/events?task_id={first['task_id']}",
        )
    finally:
        gateway.stop()

    assert "events" in filtered
    assert len(filtered["events"]) > 0
    # all events should belong to the first task only
    for event in filtered["events"]:
        assert event["task_id"] == first["task_id"]


def test_gateway_events_endpoint_respects_limit(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a"},
        )
        _wait_for_task_result(gateway, accepted["task_id"])
        limited = _json_request(gateway.base_url, "GET", "/events?limit=3")
    finally:
        gateway.stop()

    assert "events" in limited
    assert len(limited["events"]) <= 3


def test_gateway_returns_401_without_token_when_api_token_set(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
            api_token="secret-token",
        )
    )
    gateway.start()
    try:
        forged_headers = {
            "X-Operator-Id": "attacker",
            "X-Operator-Scopes": "admin",
        }
        status_code, body = _json_request_with_headers(
            gateway.base_url,
            "GET",
            "/state",
            headers=forged_headers,
        )
        post_status, post_body = _json_request_with_headers(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人",
                "operator": {"operator_id": "attacker", "role": "admin"},
            },
            headers=forged_headers,
        )
    finally:
        gateway.stop()

    assert status_code == 401
    assert body == {"error": "Unauthorized"}
    assert post_status == 401
    assert post_body == {"error": "Unauthorized"}


def test_gateway_returns_200_with_correct_token(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
            api_token="secret-token",
        )
    )
    gateway.start()
    try:
        status_code, body = _json_request_with_headers(
            gateway.base_url, "GET", "/state", headers={"Authorization": "Bearer secret-token"}
        )
    finally:
        gateway.stop()

    assert status_code == 200
    assert body["robot_state"]["robot_id"] == "robot-gateway"


def test_gateway_shared_token_uses_server_owned_principal(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
            api_token="secret-token",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去坐标 (2.0, 1.5) 救人",
                "session_id": "operator-a",
                "operator": {"operator_id": "attacker", "role": "admin"},
            },
            headers={
                "Authorization": "Bearer secret-token",
                "X-Operator-Id": "attacker",
                "X-Operator-Scopes": "admin",
            },
        )
        _wait_for_task_result(gateway, accepted["task_id"])
        events = _json_request(
            gateway.base_url,
            "GET",
            f"/tasks/{accepted['task_id']}/events",
            headers={"Authorization": "Bearer secret-token"},
        )
    finally:
        gateway.stop()

    operator_event = next(
        event for event in events["events"] if event["type"] == "operator.identified"
    )
    assert operator_event["payload"]["operator_id"] == "gateway-shared-token"
    assert operator_event["payload"]["role"] == "admin"
    assert operator_event["payload"]["source"] == "gateway_auth:shared_token"


def test_gateway_health_endpoint_bypasses_auth(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
            api_token="secret-token",
        )
    )
    gateway.start()
    try:
        status_code, body = _json_request_with_headers(gateway.base_url, "GET", "/health")
    finally:
        gateway.stop()

    assert status_code == 200
    assert body["status"] == "ok"
    assert body["robot_id"] == "robot-gateway"


# ---------------------------------------------------------------------------
# Tests: GET /events/stream (SSE)
# ---------------------------------------------------------------------------


class TestGatewaySSEStream:
    def test_sse_endpoint_returns_event_stream_content_type(self, tmp_path):
        gateway = FireClawGateway(
            GatewayConfig(
                host="127.0.0.1",
                port=0,
                adapter="simulator",
                robot_id="robot-gateway",
                memory_path=str(tmp_path / "memory.jsonl"),
                event_path=str(tmp_path / "events.jsonl"),
                workspace_skills_dir=None,
            )
        )
        gateway.start()
        try:
            req = request.Request(
                f"{gateway.base_url}/events/stream",
                method="GET",
                headers={"X-Operator-Scopes": "state.read"},
            )
            # Use a short timeout — SSE will not close on its own
            response = request.urlopen(req, timeout=2)
            try:
                assert response.status == 200
                assert "text/event-stream" in response.headers.get("Content-Type", "")
                assert response.headers.get("Cache-Control") == "no-cache"
                assert response.headers.get("Connection") == "keep-alive"
            finally:
                response.close()
        except Exception:
            # Timeout is expected for a long-lived SSE connection
            pass
        finally:
            gateway.stop()

    def test_sse_endpoint_rejects_forged_scope_without_authentication(self, tmp_path):
        gateway = FireClawGateway(
            GatewayConfig(
                host="127.0.0.1",
                port=0,
                adapter="simulator",
                robot_id="robot-gateway",
                memory_path=str(tmp_path / "memory.jsonl"),
                workspace_skills_dir=None,
                api_token="gateway-secret",
            )
        )
        gateway.start()
        try:
            req = request.Request(
                f"{gateway.base_url}/events/stream",
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
            gateway.stop()

    def test_sse_receives_task_lifecycle_events(self, tmp_path):
        """Subscribe to EventBus before submitting a task and verify lifecycle events."""
        gateway = FireClawGateway(
            GatewayConfig(
                host="127.0.0.1",
                port=0,
                adapter="simulator",
                robot_id="robot-gateway",
                memory_path=str(tmp_path / "memory.jsonl"),
                event_path=str(tmp_path / "events.jsonl"),
                workspace_skills_dir=None,
            )
        )
        collected: list[StreamEvent] = []

        def _capture(event: StreamEvent) -> None:
            collected.append(event)

        gateway.start()
        try:
            token = gateway._event_bus.subscribe(_capture)
            accepted = _json_request(
                gateway.base_url,
                "POST",
                "/tasks",
                {
                    "command": "去坐标 (2.0, 1.5) 救人",
                    "session_id": "operator-a",
                },
            )
            result = _wait_for_task_result(gateway, accepted["task_id"])
        finally:
            gateway._event_bus.unsubscribe(token)
            gateway.stop()

        assert result["status"] == "completed"
        event_types = [e.event_type for e in collected]
        # Must include the key lifecycle events
        assert "task.received" in event_types, f"Expected task.received in {event_types}"
        assert "task.running" in event_types, f"Expected task.running in {event_types}"
        assert "task.completed" in event_types, f"Expected task.completed in {event_types}"
        # task.running should come after task.received
        received_idx = event_types.index("task.received")
        running_idx = event_types.index("task.running")
        completed_idx = event_types.index("task.completed")
        assert received_idx < running_idx < completed_idx
        # All lifecycle events should be for the accepted task
        lifecycle_events = [
            e for e in collected
            if e.event_type in ("task.received", "task.running", "task.completed")
        ]
        for event in lifecycle_events:
            assert event.task_id == accepted["task_id"]
        # task.running is published via _publish_stream_event and sets robot_id
        running_event = collected[running_idx]
        assert running_event.robot_id == "robot-gateway"
        assert running_event.source == "robot-gateway"
