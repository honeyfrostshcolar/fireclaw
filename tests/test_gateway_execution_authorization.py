from fireclaw_core.approval.execution_authorization import (
    ExecutionAuthorization,
    authorized_action,
    execution_scope_hash,
)
from fireclaw_core.gateway.control import OperatorContext
from fireclaw_core.gateway.gateway import (
    FireClawGateway,
    GatewayConfig,
    resolve_gateway_storage_namespace,
)


def _config(tmp_path):
    return GatewayConfig(
        adapter="simulator",
        robot_id="robot-a",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        task_queue_path=str(tmp_path / "tasks.jsonl"),
        runtime_state_path=str(tmp_path / "runtime.sqlite3"),
        dry_run=True,
    )


def _requester():
    return OperatorContext(
        operator_id="operator-1",
        role="operator",
        control_scopes={"task.submit", "task.confirm"},
    )


def _supervisor():
    return OperatorContext(
        operator_id="supervisor-1",
        role="supervisor",
        control_scopes={"task.confirm", "safety.override"},
    )


def _awaiting_confirmation_result():
    return {
        "status": "awaiting_confirmation",
        "confirmation": {
            "reasons": ["Real robot execution requires confirmation."]
        },
        "structured_task": {
            "mission_id": "mission-1",
            "task_id": "plan-node-1",
            "robot_id": "robot-a",
            "task_type": "navigation",
            "target": {"x": 4.0, "y": 2.0},
            "required_skills": ["navigate_to"],
            "risk_level": "high",
            "command": "navigate to the east corridor",
        },
        "planning": {
            "plan": {
                "intent": "navigation",
                "steps": [
                    {
                        "skill_name": "navigate_to",
                        "inputs": {"target": {"x": 4.0, "y": 2.0}},
                    }
                ],
            }
        },
    }


def test_custom_memory_path_defines_implicit_gateway_storage_namespace(
    tmp_path,
):
    resolved = resolve_gateway_storage_namespace(
        GatewayConfig(memory_path=str(tmp_path / "robot-a.jsonl"))
    )

    assert resolved.event_path == str(tmp_path / "robot-a-events.jsonl")
    assert resolved.task_queue_path == str(tmp_path / "robot-a-tasks.jsonl")
    assert resolved.runtime_state_path == str(
        tmp_path / "robot-a-runtime.sqlite3"
    )


def test_gateway_persists_exact_approval_and_revalidates_after_restart(tmp_path):
    gateway = FireClawGateway(_config(tmp_path))
    result = _awaiting_confirmation_result()
    gateway.task_queue.create(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        created_at="2026-07-29T10:00:00+00:00",
    )
    gateway.task_queue.update(
        "runtime-task-1",
        status="running",
        started_at="2026-07-29T10:00:01+00:00",
    )
    gateway._record_authorization_request_if_needed(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        result=result,
        operator=_requester(),
    )
    gateway._record_result_events(
        "runtime-task-1",
        "mission-1",
        result,
    )

    waiting_record = gateway.task_queue.get("runtime-task-1")
    waiting_trace = gateway.task_trace("runtime-task-1")
    assert waiting_record is not None
    assert waiting_record.status == "awaiting_confirmation"
    assert waiting_record.ended_at is None
    assert waiting_trace["status"] == "awaiting_confirmation"
    assert waiting_trace["result"]["status"] == "awaiting_confirmation"
    assert not any(
        event["type"].startswith("task.")
        and event["type"] in {
            "task.completed",
            "task.blocked",
            "task.escalated",
            "task.failed",
            "task.timed_out",
            "task.cancelled",
            "task.lost",
        }
        for event in waiting_trace["events"]
    )

    request = gateway.runtime_state.pending_authorization_request("mission-1")
    assert request is not None
    assert request["robot_id"] == "robot-a"
    assert len(request["authorized_actions"]) == 1

    approved, payload = gateway._authorize_confirmation(
        session_id="mission-1",
        operator=_supervisor(),
    )
    approved_again, repeated = gateway._authorize_confirmation(
        session_id="mission-1",
        operator=_supervisor(),
    )

    assert approved is True
    assert approved_again is False
    assert repeated["status"] == "denied"

    authorization = ExecutionAuthorization.from_dict(
        payload["execution_authorization"]
    )
    restarted = FireClawGateway(_config(tmp_path))
    action = authorized_action(
        "navigate_to",
        {"target": {"x": 4.0, "y": 2.0}},
    )
    exact_scope = execution_scope_hash(
        command="navigate to the east corridor",
        structured_task=result["structured_task"],
        actions=[action],
    )

    verified = restarted.execution_authorization_authority.verify(
        authorization,
        robot_id="robot-a",
        scope_hash=exact_scope,
        action_hashes={action["action_hash"]},
    )
    changed_scope = restarted.execution_authorization_authority.verify(
        authorization,
        robot_id="robot-a",
        scope_hash=execution_scope_hash(
            command="navigate to the west corridor",
            structured_task=result["structured_task"],
            actions=[action],
        ),
        action_hashes={action["action_hash"]},
    )

    assert verified.verified is True
    assert changed_scope.error_code == "authorization_scope_mismatch"


def test_gateway_restart_preserves_pending_confirmation_task(tmp_path):
    gateway = FireClawGateway(_config(tmp_path))
    result = _awaiting_confirmation_result()
    gateway.task_queue.create(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        created_at="2026-08-10T00:00:00+00:00",
    )
    gateway.task_queue.update(
        "runtime-task-1",
        status="running",
        started_at="2026-08-10T00:00:01+00:00",
    )
    gateway._record_authorization_request_if_needed(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        result=result,
        operator=_requester(),
    )
    gateway._record_result_events(
        "runtime-task-1",
        "mission-1",
        result,
    )

    restarted = FireClawGateway(_config(tmp_path))
    trace = restarted.task_trace("runtime-task-1")

    assert trace["status"] == "awaiting_confirmation"
    assert trace["queue_record"]["ended_at"] is None
    assert trace["result"]["status"] == "awaiting_confirmation"
    assert "task.lost" not in [
        event["type"] for event in trace["events"]
    ]


def test_confirm_resumes_original_task_as_original_requester(
    tmp_path,
    monkeypatch,
):
    gateway = FireClawGateway(_config(tmp_path))
    result = _awaiting_confirmation_result()
    gateway.task_queue.create(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        created_at="2026-08-10T00:00:00+00:00",
    )
    gateway.task_queue.update(
        "runtime-task-1",
        status="running",
        started_at="2026-08-10T00:00:01+00:00",
    )
    gateway._record_authorization_request_if_needed(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        result=result,
        operator=_requester(),
    )
    gateway._record_result_events(
        "runtime-task-1",
        "mission-1",
        result,
    )
    started = {}

    def capture_worker(control, operator, *, resumed=False):
        started.update(
            {
                "control": control,
                "operator": operator,
                "resumed": resumed,
            }
        )

    monkeypatch.setattr(gateway, "_start_task_worker", capture_worker)

    confirmed = gateway.confirm_task(
        session_id="mission-1",
        operator=_supervisor(),
    )

    assert confirmed["status"] == "accepted"
    assert confirmed["task_id"] == "runtime-task-1"
    assert started["control"].task_id == "runtime-task-1"
    assert started["operator"].operator_id == "operator-1"
    assert started["resumed"] is True
    queue_record = gateway.task_queue.get("runtime-task-1")
    assert queue_record is not None
    assert queue_record.status == "accepted"
    events = gateway.events.events_for_task("runtime-task-1")
    approval = next(
        event for event in events if event["type"] == "authorization.approved"
    )
    scheduled = next(
        event for event in events if event["type"] == "task.resume_scheduled"
    )
    assert approval["payload"]["approved_by"]["operator_id"] == "supervisor-1"
    assert scheduled["payload"]["requested_by"]["operator_id"] == "operator-1"
    assert scheduled["payload"]["approved_by"]["operator_id"] == "supervisor-1"


def test_physical_authorization_persists_exact_resume_material_and_skips_planner(
    tmp_path,
    monkeypatch,
):
    gateway = FireClawGateway(_config(tmp_path))
    result = _awaiting_confirmation_result()
    result["structured_task"] = {
        **result["structured_task"],
        "required_skills": ["navigate_to"],
    }
    result["planning"] = {
        **result["planning"],
        "plan": {
            "intent": "navigation",
            "steps": [
                {
                    "skill_name": "navigate_to",
                    "inputs": {"target": {"x": 4.0, "y": 2.0}},
                }
            ],
        },
    }
    result["memory_snapshot"] = {
        "evidence_event_ids": ["snapshot-1", "snapshot-2"],
        "recorded": True,
    }
    gateway.task_queue.create(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        created_at="2026-08-10T00:00:00+00:00",
    )
    gateway.task_queue.update(
        "runtime-task-1",
        status="running",
        started_at="2026-08-10T00:00:01+00:00",
    )
    gateway._record_authorization_request_if_needed(
        task_id="runtime-task-1",
        session_id="mission-1",
        command="navigate to the east corridor",
        result=result,
        operator=_requester(),
    )
    gateway._record_result_events(
        "runtime-task-1",
        "mission-1",
        result,
    )
    request = gateway.runtime_state.pending_authorization_request("mission-1")
    assert request is not None
    assert request["pending_execution"] == {
        "version": 1,
        "planning": result["planning"],
        "memory_snapshot": {
            "evidence_event_ids": ["snapshot-1", "snapshot-2"],
        },
    }

    class FakeAgent:
        def __init__(self):
            self.calls = []

        def execute_authorized_pending_step(
            self,
            *,
            command,
            structured_task,
            pending_execution,
        ):
            self.calls.append(
                {
                    "command": command,
                    "task_id": structured_task.task_id,
                    "pending_execution": pending_execution,
                }
            )
            return {
                "status": "succeeded",
                "message": "resumed",
                "execution": {"status": "succeeded", "steps": []},
                "planning": pending_execution["planning"],
                "safety": {"status": "allow"},
            }

    fake_agent = FakeAgent()
    monkeypatch.setattr(
        gateway,
        "_create_agent",
        lambda **kwargs: fake_agent,
    )
    monkeypatch.setattr(gateway, "_record_result_events", lambda *args: None)

    request_object = gateway.runtime_state.pending_authorization_request(
        "mission-1"
    )
    assert request_object is not None
    from fireclaw_core.gateway.control import AuthorizationRequest

    authorization_request = AuthorizationRequest.from_dict(request_object)
    approved, approval_payload = gateway._authorize_confirmation(
        session_id="mission-1",
        operator=_supervisor(),
    )
    assert approved is True
    from fireclaw_core.approval.execution_authorization import ExecutionAuthorization

    execution_authorization = ExecutionAuthorization.from_dict(
        approval_payload["execution_authorization"]
    )
    structured_task = dict(authorization_request.structured_task or {})
    structured_task["execution_authorization"] = execution_authorization.to_dict()
    resumed = gateway._execute_agent_task(
        command=authorization_request.command,
        session_id=authorization_request.session_id,
        task_id=authorization_request.task_id,
        record_received=False,
        structured_task=structured_task,
        execution_authorization=execution_authorization,
        pending_execution=authorization_request.pending_execution,
    )

    assert resumed["status"] == "succeeded"
    assert len(fake_agent.calls) == 1
    assert fake_agent.calls[0]["pending_execution"]["version"] == 1


def test_confirm_task_id_does_not_approve_another_task_in_same_session(
    tmp_path,
    monkeypatch,
):
    gateway = FireClawGateway(
        GatewayConfig(
            **{
                **_config(tmp_path).__dict__,
                "max_active_execution_tasks": 2,
            }
        )
    )
    for index in (1, 2):
        runtime_task_id = f"runtime-task-{index}"
        result = _awaiting_confirmation_result()
        result["structured_task"] = {
            **result["structured_task"],
            "task_id": f"plan-node-{index}",
        }
        gateway.task_queue.create(
            task_id=runtime_task_id,
            session_id="mission-1",
            command=f"navigate task {index}",
            created_at=f"2026-08-10T00:00:0{index}+00:00",
        )
        gateway.task_queue.update(
            runtime_task_id,
            status="running",
            started_at=f"2026-08-10T00:00:1{index}+00:00",
        )
        gateway._record_authorization_request_if_needed(
            task_id=runtime_task_id,
            session_id="mission-1",
            command=f"navigate task {index}",
            result=result,
            operator=_requester(),
        )
        gateway._record_result_events(
            runtime_task_id,
            "mission-1",
            result,
        )

    started = {}

    def capture_worker(control, operator, *, resumed=False):
        started["task_id"] = control.task_id

    monkeypatch.setattr(gateway, "_start_task_worker", capture_worker)

    confirmed = gateway.confirm_task(
        session_id="mission-1",
        task_id="runtime-task-1",
        operator=_supervisor(),
    )

    assert confirmed["status"] == "accepted"
    assert confirmed["task_id"] == "runtime-task-1"
    assert started["task_id"] == "runtime-task-1"
    assert (
        gateway.runtime_state.pending_authorization_request_for_task(
            "runtime-task-1"
        )
        is None
    )
    assert (
        gateway.runtime_state.pending_authorization_request_for_task(
            "runtime-task-2"
        )
        is not None
    )
