from pathlib import Path

from fireclaw_core.agent.computer_tools import ComputerSandbox
from fireclaw_core.agent.robot_deliberation import (
    RobotAgentDecision,
    RobotAgentDeliberationRuntime,
)
from fireclaw_core.approval.execution_authorization import (
    ExecutionAuthorization,
)
from fireclaw_core.gateway.control import OperatorContext
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.policy.deployment import (
    DeploymentProfile,
    SandboxProfile,
)


_IMAGE_ID = "sha256:" + ("b" * 64)


class _WriteThenCompletePolicy:
    def decide(self, request):
        if not request.observations:
            return RobotAgentDecision(
                operation="execute_agent_tool",
                tool_name="computer_write_file",
                inputs={
                    "path": "approved.txt",
                    "content": "approved once",
                },
                message="Write the approved simulation artifact.",
            )
        if request.observations[-1].status == "executed":
            return RobotAgentDecision(
                operation="complete",
                message="The approved Tool completed.",
            )
        return RobotAgentDecision(
            operation="escalate",
            message="The Tool did not execute.",
            reason_code="tool_not_executed",
        )


def _gateway(tmp_path: Path) -> FireClawGateway:
    workspace = tmp_path / "workspace"
    profile = DeploymentProfile(
        mode="real",
        role="robot_agent",
        sandbox=SandboxProfile(
            enabled=True,
            workspace_root=workspace,
            allowed_workspace_roots=(workspace,),
            image="fireclaw-agent:test",
            image_digest=_IMAGE_ID,
        ),
    )
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="dry-run",
            robot_id="robot-a",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            runtime_state_path=str(tmp_path / "runtime.sqlite3"),
            dry_run=True,
            robot_agent_enabled=True,
            deployment_profile=profile,
        ),
        computer_sandbox=ComputerSandbox(profile.sandbox),
    )
    gateway.robot_agent_runtime = RobotAgentDeliberationRuntime(
        policy=_WriteThenCompletePolicy(),
        checkpoint_store=gateway.agent_loop_checkpoints,
    )
    return gateway


def _operator() -> OperatorContext:
    return OperatorContext(
        operator_id="operator-1",
        role="operator",
        control_scopes={"task.submit", "task.confirm"},
    )


def _task() -> dict:
    return {
        "mission_id": "mission-1",
        "task_id": "node-1",
        "robot_id": "robot-a",
        "task_type": "diagnostic_artifact",
        "target": {},
        "required_skills": [],
        "allowed_skills": [],
        "risk_level": "low",
        "command": "write an approved diagnostic artifact",
    }


def _prepare_task(gateway: FireClawGateway, task_id: str) -> None:
    gateway.task_queue.create(
        task_id=task_id,
        session_id="mission-1",
        command="write an approved diagnostic artifact",
        created_at="2026-07-30T00:00:00+00:00",
    )
    gateway.task_queue.update(
        task_id,
        status="running",
        started_at="2026-07-30T00:00:01+00:00",
    )


def test_gateway_resumes_agent_tool_with_one_time_exact_authorization(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)
    first_task_id = "runtime-task-1"
    _prepare_task(gateway, first_task_id)

    pending_result = gateway._execute_agent_task(
        command="write an approved diagnostic artifact",
        session_id="mission-1",
        task_id=first_task_id,
        record_received=False,
        operator=_operator(),
        structured_task=_task(),
    )
    request = gateway.runtime_state.pending_authorization_request(
        "mission-1"
    )

    assert pending_result["status"] == "awaiting_confirmation"
    assert request is not None
    assert request["authorization_kind"] == "agent_tool"
    assert request["required_scope"] == "task.confirm"

    approved, payload = gateway._authorize_confirmation(
        session_id="mission-1",
        operator=_operator(),
    )
    assert approved is True
    authorization = ExecutionAuthorization.from_dict(
        payload["execution_authorization"]
    )

    resumed_task = _task()
    resumed_task["execution_authorization"] = authorization.to_dict()
    completed = gateway._execute_agent_task(
        command="write an approved diagnostic artifact",
        session_id="mission-1",
        task_id=first_task_id,
        record_received=False,
        operator=_operator(),
        structured_task=resumed_task,
        execution_authorization=authorization,
    )

    assert completed["status"] == "completed"
    assert completed["task_id"] == first_task_id
    assert [
        record.task_id for record in gateway.task_queue.list_records()
    ] == [first_task_id]
    event_types = [
        event["type"]
        for event in gateway.events.events_for_task(first_task_id)
    ]
    assert "task.escalated" not in event_types
    assert event_types.count("task.completed") == 1
    assert (
        gateway.computer_sandbox.root / "approved.txt"
    ).read_text(encoding="utf-8") == "approved once"
    uses = gateway.runtime_state.read(
        """
        SELECT authorization_id, operation_id
        FROM authorization_uses
        WHERE authorization_id = ?
        """,
        (authorization.authorization_id,),
    )
    assert len(uses) == 1
