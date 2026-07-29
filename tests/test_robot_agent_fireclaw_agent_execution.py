from __future__ import annotations

from fireclaw_core.agent.agent import (
    DeliberatedStepExecution,
    FireClawAgent,
)
from fireclaw_core.agent.bounded_loop import AgentLoopResult
from fireclaw_core.execution.executor import (
    ExecutionResult,
    StepExecutionResult,
)
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.task.task_contract import StructuredRobotTask


def test_fireclaw_agent_can_execute_precomputed_structured_planning_result(tmp_path):
    agent = FireClawAgent(
        memory=None,
        workspace_skills_dir=None,
        dry_run=True,
        session_id="session-1",
    )
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={
            "frame_id": "map",
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
        },
        required_skills=["navigate_to_point", "report_status"],
        command="去坐标 (2.0, 1.5) 搜索",
    )
    planning_result = PlanningResult(
        status="planned",
        message="precomputed",
        intent="search",
        target_pose={
            "x": 2.0,
            "y": 1.5,
            "yaw": 0.0,
            "frame_id": "map",
        },
        plan=Plan(
            intent="search",
            steps=[
                PlanStep("report_status", {}),
                PlanStep(
                    "navigate_to_point",
                    {
                        "x": 2.0,
                        "y": 1.5,
                        "yaw": 0.0,
                        "frame_id": "map",
                    },
                ),
            ],
        ),
    )

    result = agent.run_planning_result(
        command="去坐标 (2.0, 1.5) 搜索",
        structured_task=task,
        planning_result=planning_result,
    )

    assert result["status"] == "succeeded"
    assert result["structured_task"]["task_id"] == "task-1"
    assert [step["skill_name"] for step in result["planning"]["plan"]["steps"]] == [
        "report_status",
        "navigate_to_point",
    ]


def test_fireclaw_agent_run_planning_result_without_structured_task():
    agent = FireClawAgent(
        memory=None,
        workspace_skills_dir=None,
        dry_run=True,
        session_id="session-2",
    )
    planning_result = PlanningResult(
        status="planned",
        message="precomputed",
        intent="search",
        target_pose={
            "x": 2.0,
            "y": 1.5,
            "yaw": 0.0,
            "frame_id": "map",
        },
        plan=Plan(
            intent="search",
            steps=[
                PlanStep("report_status", {}),
            ],
        ),
    )

    result = agent.run_planning_result(
        command="上报当前区域搜索状态",
        planning_result=planning_result,
    )

    assert result["status"] == "succeeded"
    assert result["structured_task"] is None


def test_recovered_local_failure_does_not_invalidate_mission_plan(tmp_path):
    events = []
    agent = FireClawAgent(
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        workspace_skills_dir=None,
        dry_run=True,
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
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor"],
        command="去二楼搜索",
    )
    failed = DeliberatedStepExecution(
        payload={"safety": {"status": "allow"}},
        execution_result=ExecutionResult(
            status="failed",
            steps=[
                StepExecutionResult(
                    skill_name="navigate_to_floor",
                    inputs={"floor": 2},
                    status="failed",
                    output={
                        "status": "failed",
                        "action": "navigate_to_floor",
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
                    skill_name="navigate_to_floor",
                    inputs={"floor": 2},
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
        command="去二楼搜索",
        structured_task=task,
        loop_result=loop_result,
        step_executions=[failed, recovered],
    )

    assert result["status"] == "completed"
    assert "invalidation_event" not in result
    assert not any(
        event_type == "mission.plan_invalidated"
        for event_type, _ in events
    )
