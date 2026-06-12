from __future__ import annotations

from fireclaw_core.agent.agent import FireClawAgent
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
        target={"floor": 2},
        required_skills=["navigate_to_floor", "report_status"],
        command="去2楼搜索",
    )
    planning_result = PlanningResult(
        status="planned",
        message="precomputed",
        intent="search",
        target_floor=2,
        plan=Plan(
            intent="search",
            steps=[
                PlanStep("report_status", {"floor": 2}),
                PlanStep("navigate_to_floor", {"floor": 2}),
            ],
        ),
    )

    result = agent.run_planning_result(
        command="去2楼搜索",
        structured_task=task,
        planning_result=planning_result,
    )

    assert result["status"] == "succeeded"
    assert result["structured_task"]["task_id"] == "task-1"
    assert [step["skill_name"] for step in result["planning"]["plan"]["steps"]] == [
        "report_status",
        "navigate_to_floor",
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
        target_floor=2,
        plan=Plan(
            intent="search",
            steps=[
                PlanStep("report_status", {"floor": 2}),
            ],
        ),
    )

    result = agent.run_planning_result(
        command="去2楼搜索",
        planning_result=planning_result,
    )

    assert result["status"] == "succeeded"
    assert result["structured_task"] is None
