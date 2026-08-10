from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter, RobotActionResult
from fireclaw_core.execution.execution_event_producer import (
    RobotExecutionEventProducer,
)
from fireclaw_core.execution.executor import (
    ExecutionResult,
    StepExecutionResult,
)
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile
from fireclaw_core.safety.local_failure import FailureCategory
from fireclaw_core.task.task_contract import StructuredRobotTask


def _failed_execution(
    *,
    skill_name: str = "navigate_to_point",
    action: str = "navigate_to_point",
    failure_category: str | FailureCategory | None = None,
    error: str = "No path to target",
    attempt_count: int = 3,
    skill_domain: str = "navigation",
    safety_class: str | None = "motion",
) -> ExecutionResult:
    output = {
        "status": "failed",
        "robot_id": "robot-a",
        "action": action,
        "timestamp": "2026-07-28T10:00:00+00:00",
        "action_id": "action-1",
    }
    if failure_category is not None:
        output["failure_category"] = failure_category
    return ExecutionResult(
        status="failed",
        steps=[
            StepExecutionResult(
                skill_name=skill_name,
                inputs={"x": 2.0, "y": 1.5, "frame_id": "map"},
                status="failed",
                output=output,
                error=error,
                attempt_count=attempt_count,
                skill_domain=skill_domain,
                safety_class=safety_class,
                action_binding=action,
            )
        ],
    )


def _produce(execution_result: ExecutionResult):
    return RobotExecutionEventProducer().produce(
        execution_result=execution_result,
        mission_id="mission-1",
        robot_id="robot-a",
        runtime_task_id="runtime-task-9",
        plan_node_id="task-1",
    )


def test_navigation_target_unreachable_produces_route_blocked_event() -> None:
    execution = _failed_execution(
        failure_category=FailureCategory.TARGET_UNREACHABLE,
    )

    first = _produce(execution)
    second = _produce(execution)

    assert first is not None
    assert first == second
    assert first.event_type == "route_blocked"
    assert first.evidence_id == "action-result:action-1"
    assert first.source_type == "execution_monitor"
    assert first.task_id == "runtime-task-9"
    assert first.node_id == "task-1"
    assert first.details["classification_basis"] == (
        "adapter_failure_category"
    )


def test_no_path_text_is_only_a_fallback_for_navigation() -> None:
    navigation = _produce(_failed_execution())
    search = _produce(_failed_execution(
        skill_name="victim_search",
        action="victim_search",
        skill_domain="perception",
        safety_class="victim_perception",
    ))

    assert navigation is not None
    assert navigation.event_type == "route_blocked"
    assert navigation.details["classification_basis"] == (
        "local_failure_inference"
    )
    assert search is not None
    assert search.event_type == "transient_failure"


def test_retry_count_does_not_turn_timeout_into_route_blocked() -> None:
    event = _produce(_failed_execution(
        failure_category="timeout",
        error="Navigation timed out",
        attempt_count=99,
    ))

    assert event is not None
    assert event.event_type == "transient_failure"
    assert event.details["attempt_count"] == 99
    assert event.details["failure_reason"]["retryable"] is True


def test_non_failure_and_unprojectable_failure_do_not_emit_event() -> None:
    producer = RobotExecutionEventProducer()
    success = ExecutionResult(status="succeeded", steps=[])
    cancelled = _failed_execution(failure_category="cancelled")

    assert producer.produce(
        execution_result=success,
        mission_id="mission-1",
        robot_id="robot-a",
        runtime_task_id="runtime-task-9",
        plan_node_id="task-1",
    ) is None
    assert _produce(cancelled) is None


class _BlockedRobot(DryRunRobotAdapter):
    def navigate_to_point(self, **_kwargs):
        raise AssertionError("RobotAdapter domain fallback must never run")


class _BlockedNavigationBackend:
    def navigate_to_point(
        self,
        x: float,
        y: float,
        *,
        yaw: float = 0.0,
        frame_id: str = "map",
        **_kwargs,
    ) -> dict:
        return {
            "status": "failed",
            "x": x,
            "y": y,
            "yaw": yaw,
            "frame_id": frame_id,
            "failure_category": "target_unreachable",
            "error": "Planner confirmed no path to target",
        }


def _simulation_profile() -> DeploymentProfile:
    return DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=SandboxProfile(),
    )


def _structured_task() -> StructuredRobotTask:
    return StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"pose": {"x": 2.0, "y": 1.5, "frame_id": "map"}},
        required_skills=["navigate_to_point"],
        mission_id="mission-1",
        robot_id="robot-a",
        command="去坐标 (2.0, 1.5) 搜索受困人员",
    )


def test_robot_agent_emits_plan_invalidation_after_final_failure() -> None:
    events = []
    agent = FireClawAgent(
        robot=_BlockedRobot(robot_id="robot-a"),
        session_id="mission-1",
        task_id="runtime-task-9",
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
        extension_paths=(Path(__file__).resolve().parents[1] / "extensions",),
        plugin_services={
            "adapter": "dry-run",
            "fireclaw.navigation.move-base.backend": _BlockedNavigationBackend(),
        },
        deployment_profile=_simulation_profile(),
    )

    result = agent.run_structured_task(_structured_task())

    assert result["status"] == "failed"
    event = result["invalidation_event"]
    assert event["event_type"] == "route_blocked"
    assert event["node_id"] == "task-1"
    assert event["task_id"] == "runtime-task-9"
    emitted = [
        payload
        for event_type, payload in events
        if event_type == "mission.plan_invalidated"
    ]
    assert emitted == [{"invalidation_event": event}]
    assert event["details"]["attempt_count"] == 1


def test_gateway_task_trace_exposes_produced_invalidation_event(
    tmp_path,
) -> None:
    gateway = FireClawGateway(GatewayConfig(
        robot_id="robot-a",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        task_queue_path=str(tmp_path / "tasks.jsonl"),
        deployment_profile=_simulation_profile(),
        ),
        plugin_services={
            "fireclaw.navigation.move-base.backend": _BlockedNavigationBackend(),
        },
    )
    gateway.robot = _BlockedRobot(robot_id="robot-a")

    accepted = gateway.submit_agent(
        "去坐标 (2.0, 1.5) 搜索受困人员",
        session_id="mission-1",
        structured_task=_structured_task().to_dict(),
    )
    gateway._wait_until_task_inactive(
        accepted["task_id"],
        timeout_seconds=2.0,
    )
    trace = gateway.task_trace(accepted["task_id"])

    invalidation_events = [
        item
        for item in trace["events"]
        if item["type"] == "mission.plan_invalidated"
    ]
    assert len(invalidation_events) == 1
    event = invalidation_events[0]["payload"]["invalidation_event"]
    assert event["mission_id"] == "mission-1"
    assert event["event_type"] == "route_blocked"
    assert event["node_id"] == "task-1"
    assert event["task_id"] == accepted["task_id"]
    assert trace["result"]["invalidation_event"] == event
