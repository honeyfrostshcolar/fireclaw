from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import (
    DeliberatedStepExecution,
    FireClawAgent,
)
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.bounded_loop import AgentLoopResult
from fireclaw_core.agent.robot_deliberation import (
    RobotAgentExecutionObservation,
)
from fireclaw_core.execution.executor import (
    ExecutionResult,
    StepExecutionResult,
)
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.memory.robot_memory import RobotMemorySnapshot
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.task.task_contract import StructuredRobotTask


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"
POINT_TARGET = {
    "frame_id": "map",
    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
}
POINT_INPUTS = {
    "x": 2.0,
    "y": 1.5,
    "yaw": 0.0,
    "frame_id": "map",
}


def _navigation_agent(
    memory,
    *,
    session_id: str,
    task_id: str | None = None,
    event_sink=None,
):
    return FireClawAgent(
        memory=memory,
        dry_run=True,
        session_id=session_id,
        task_id=task_id,
        event_sink=event_sink,
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    )


def test_fireclaw_agent_can_execute_precomputed_structured_planning_result(
    tmp_path,
):
    agent = _navigation_agent(
        JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="session-1",
    )
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        command="导航到 map 坐标 (2.0, 1.5)",
    )
    planning_result = PlanningResult(
        status="planned",
        message="precomputed",
        intent="navigate",
        target_pose=POINT_INPUTS,
        plan=Plan(
            intent="navigate",
            steps=[PlanStep("navigate_to_point", POINT_INPUTS)],
        ),
    )

    result = agent.run_planning_result(
        command="导航到 map 坐标 (2.0, 1.5)",
        structured_task=task,
        planning_result=planning_result,
    )

    assert result["status"] == "succeeded"
    assert result["structured_task"]["task_id"] == "task-1"
    assert [
        step["skill_name"] for step in result["planning"]["plan"]["steps"]
    ] == ["navigate_to_point"]
    assert not hasattr(agent.robot, "navigate_to_point")


def test_fireclaw_agent_run_planning_result_without_structured_task(tmp_path):
    agent = _navigation_agent(
        JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="session-2",
    )
    planning_result = PlanningResult(
        status="planned",
        message="precomputed",
        intent="navigate",
        target_pose=POINT_INPUTS,
        plan=Plan(
            intent="navigate",
            steps=[PlanStep("navigate_to_point", POINT_INPUTS)],
        ),
    )

    result = agent.run_planning_result(
        command="导航到 map 坐标 (2.0, 1.5)",
        planning_result=planning_result,
    )

    assert result["status"] == "succeeded"
    assert result["structured_task"] is None


def test_authorized_pending_step_reuses_original_snapshot(tmp_path):
    class CountingSnapshotRecorder:
        def __init__(self):
            self.calls = 0

        def record_snapshot(self, **kwargs):
            self.calls += 1
            return RobotMemorySnapshot(
                body_state_event_id=f"snapshot-{self.calls}"
            )

    recorder = CountingSnapshotRecorder()
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        dry_run=True,
        session_id="mission-1",
        task_id="task-1",
        robot_memory_recorder=recorder,
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    )
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        command="导航到 map 坐标 (2.0, 1.5)",
    )

    first = agent.run_structured_task(task)
    assert recorder.calls == 1
    pending_execution = {
        "planning": first["planning"],
        "memory_snapshot": first["memory_snapshot"],
    }

    resumed = agent.execute_authorized_pending_step(
        command=task.command or "navigate",
        structured_task=task,
        pending_execution=pending_execution,
    )

    assert resumed["status"] == "succeeded"
    assert recorder.calls == 1
    assert resumed["memory_snapshot"] == {
        "evidence_event_ids": ["snapshot-1"],
        "reused": True,
        "recorded": False,
    }
    assert resumed["authorization_resume"]["robot_agent_reinvoked"] is False
    assert resumed["authorization_resume"]["llm_reinvoked"] is False


def test_recovered_local_failure_does_not_invalidate_mission_plan(tmp_path):
    events = []
    agent = _navigation_agent(
        JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="mission-1",
        task_id="task-1",
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        command="导航到 map 坐标 (2.0, 1.5)",
    )
    failed = DeliberatedStepExecution(
        payload={"safety": {"status": "allow"}},
        execution_result=ExecutionResult(
            status="failed",
            steps=[
                StepExecutionResult(
                    skill_name="navigate_to_point",
                    inputs=POINT_INPUTS,
                    status="failed",
                    output={
                        "status": "failed",
                        "action": "navigate_to_point",
                        "failure_category": "target_unreachable",
                    },
                    error="route blocked",
                    failure_category="target_unreachable",
                )
            ],
        ),
    )
    recovered = DeliberatedStepExecution(
        payload={"safety": {"status": "allow"}},
        execution_result=ExecutionResult(
            status="succeeded",
            steps=[
                StepExecutionResult(
                    skill_name="navigate_to_point",
                    inputs=POINT_INPUTS,
                    status="succeeded",
                )
            ],
        ),
    )
    loop_result = AgentLoopResult(
        run_id="run-1",
        status="completed",
        message="recovered",
        reason_code="completed",
        started_at="2026-07-29T00:00:00+00:00",
        completed_at="2026-07-29T00:00:01+00:00",
        attempts=(),
        observations=(),
        result={"status": "completed"},
    )

    result = agent.finalize_deliberated_task(
        command="导航到 map 坐标 (2.0, 1.5)",
        structured_task=task,
        loop_result=loop_result,
        step_executions=[failed, recovered],
    )

    assert result["status"] == "completed"
    assert result["message"] == "任务已完成。"
    assert result["message_locale"] == "zh-CN"
    assert result["robot_agent_deliberation"]["message"] == "任务已完成。"
    assert "invalidation_event" not in result
    assert not any(
        event_type == "mission.plan_invalidated"
        for event_type, _ in events
    )


def test_deliberated_confirmation_preserves_exact_physical_plan(tmp_path):
    agent = _navigation_agent(
        JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="mission-confirm",
        task_id="task-confirm",
    )
    task = StructuredRobotTask(
        task_id="task-confirm",
        mission_id="mission-confirm",
        robot_id="robot-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        command="导航到 map 坐标 (2.0, 1.5)",
    )
    pending_step = DeliberatedStepExecution(
        payload={
            "status": "awaiting_confirmation",
            "planning": {
                "status": "planned",
                "plan": {
                    "intent": "navigate",
                    "steps": [
                        {
                            "skill_name": "navigate_to_point",
                            "inputs": POINT_INPUTS,
                        }
                    ],
                },
            },
            "safety": {"status": "require_confirmation"},
            "confirmation": {"status": "pending", "reasons": []},
        },
        execution_result=ExecutionResult(status="failed", steps=[]),
    )
    observation = RobotAgentExecutionObservation(
        iteration=1,
        operation="execute_skill",
        status="approval_required",
        message="confirmation required",
        tool_name="navigate_to_point",
        inputs=POINT_INPUTS,
        output=pending_step.payload,
    )
    loop_result = AgentLoopResult(
        run_id="robot:robot-1:task:task-confirm",
        status="escalated",
        message="confirmation required",
        reason_code="safety_gate_terminal",
        started_at="2026-08-10T00:00:00+00:00",
        completed_at="2026-08-10T00:00:01+00:00",
        attempts=(),
        observations=(observation,),
        result=pending_step.payload,
    )

    result = agent.finalize_deliberated_task(
        command=task.command,
        structured_task=task,
        loop_result=loop_result,
        step_executions=[pending_step],
    )

    assert result["status"] == "awaiting_confirmation"
    assert result["planning"]["plan"] == pending_step.payload["planning"][
        "plan"
    ]
