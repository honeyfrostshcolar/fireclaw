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
        workspace_skills_dir=None,
        dry_run=False,
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
