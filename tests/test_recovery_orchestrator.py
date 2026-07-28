from __future__ import annotations

from fireclaw_core.mission.completion_contract import (
    CompletionContractCompiler,
    ExecutionEvidenceValidator,
)
from fireclaw_core.mission.recovery_orchestrator import (
    CompletionRecoveryOrchestrator,
)
from fireclaw_core.mission.revision_dispatcher import MissionNodeExecution
from fireclaw_core.mission.task_graph import MissionTarget, MissionTaskNode


def _node(*, recovery_policy: str = "reassign") -> MissionTaskNode:
    contract = CompletionContractCompiler().compile(
        task_type="victim_search",
        capability_required="search_for_victims",
        target=MissionTarget(frame_id="building", floor=2),
        completion_goal="Search floor 2.",
    )
    return MissionTaskNode(
        node_id="search-floor-2",
        robot_id="robot-a",
        command="去二楼搜索受困人员",
        capability_required="search_for_victims",
        target=MissionTarget(frame_id="building", floor=2),
        task_type="victim_search",
        completion_goal=contract.completion_goal,
        success_evidence=contract.success_evidence,
        recovery_policy=recovery_policy,
    )


def _semantic_rejection(node: MissionTaskNode):
    terminal = {
        "status": "succeeded",
        "reported_status": "succeeded",
        "robot_trace": {
            "result": {
                "status": "succeeded",
                "execution": {
                    "steps": [
                        {
                            "skill_name": "search_for_victims",
                            "status": "succeeded",
                            "output": {
                                "data": {"floor": 2},
                                "timestamp": "2026-07-28T01:00:00+00:00",
                            },
                        }
                    ]
                },
            }
        },
    }
    validation = ExecutionEvidenceValidator().validate(
        requirements=node.success_evidence,
        terminal_state=terminal,
    )
    assert validation.satisfied is False
    return terminal, validation


def test_semantic_completion_rejection_is_returned_to_planner() -> None:
    node = _node(recovery_policy="reassign")
    terminal, validation = _semantic_rejection(node)

    decision = CompletionRecoveryOrchestrator().decide(
        mission_id="mission-1",
        node=node,
        robot_id="robot-a",
        task_id="task-a",
        terminal_state=terminal,
        validation=validation,
    )

    assert decision.action == "replan"
    assert decision.suggested_action == "reassign"
    assert decision.event is not None
    assert decision.event.event_type == "completion_evidence_rejected"
    assert decision.event.source_type == "execution_monitor"
    assert decision.event.details["failed_requirement_kinds"] == [
        "victim_search_result"
    ]


def test_missing_structured_result_gets_one_bounded_result_recheck() -> None:
    node = _node()
    terminal = {"status": "succeeded", "reported_status": "succeeded"}
    validation = ExecutionEvidenceValidator().validate(
        requirements=node.success_evidence,
        terminal_state=terminal,
    )
    orchestrator = CompletionRecoveryOrchestrator(max_local_retries=1)

    first = orchestrator.decide(
        mission_id="mission-1",
        node=node,
        robot_id="robot-a",
        task_id="task-a",
        terminal_state=terminal,
        validation=validation,
        recovery_attempt=0,
    )
    exhausted = orchestrator.decide(
        mission_id="mission-1",
        node=node,
        robot_id="robot-a",
        task_id="task-b",
        terminal_state=terminal,
        validation=validation,
        recovery_attempt=1,
    )

    assert first.action == "recheck"
    assert first.suggested_action == "recheck"
    assert first.event is not None
    assert first.event.event_type == "transient_failure"
    assert exhausted.action == "replan"
    assert exhausted.event is not None
    assert exhausted.event.event_type == "completion_evidence_rejected"


def test_recovery_attempt_round_trips_in_node_checkpoint_state() -> None:
    execution = MissionNodeExecution(
        plan_id="mission-1:plan:1",
        node_id="search-floor-2",
        robot_id="robot-a",
        task_id="task-a",
        status="pending",
        recovery_attempt=1,
        recovery_action="recheck",
    )

    restored = MissionNodeExecution.from_dict(execution.to_dict())

    assert restored == execution


def test_escalation_policy_never_invokes_planner_event() -> None:
    node = _node(recovery_policy="escalate")
    terminal, validation = _semantic_rejection(node)

    decision = CompletionRecoveryOrchestrator().decide(
        mission_id="mission-1",
        node=node,
        robot_id="robot-a",
        task_id="task-a",
        terminal_state=terminal,
        validation=validation,
    )

    assert decision.action == "escalate"
    assert decision.event is None
