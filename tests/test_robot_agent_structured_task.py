# tests/test_robot_agent_structured_task.py
from __future__ import annotations

from fireclaw_core.agent import FireClawAgent
from fireclaw_core.robot import DryRunRobotAdapter
from fireclaw_core.task_contract import StructuredRobotTask


def test_robot_agent_runs_structured_task_without_natural_language_planner():
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, workspace_skills_dir=None)
    task = StructuredRobotTask(
        task_id="task-structured",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims", "report_status"],
        mission_id="mission-1",
        robot_id="robot-1",
        command="human readable only",
    )

    result = agent.run_structured_task(task)

    assert result["status"] == "succeeded"
    assert result["structured_task"]["task_id"] == "task-structured"
    assert [action["action"] for action in robot.actions] == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]


def test_robot_agent_rejects_invalid_structured_task():
    agent = FireClawAgent(workspace_skills_dir=None)
    task = StructuredRobotTask(
        task_id="task-invalid",
        task_type="search",
        target={"floor": 2},
        required_skills=[],
    )

    result = agent.run_structured_task(task)

    assert result["status"] in {"clarify", "blocked"}
    assert "required_skills must not be empty" in result["message"]
