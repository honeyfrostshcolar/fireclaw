# tests/test_task_contract.py
from __future__ import annotations

from fireclaw_core.mission_planner import MissionSubtask
from fireclaw_core.task_contract import (
    StructuredRobotTask,
    planning_result_from_structured_task,
    structured_task_from_mission_subtask,
    validate_structured_robot_task,
)


def test_structured_task_from_mission_subtask_maps_floor_and_skill():
    subtask = MissionSubtask(
        robot_id="robot-1",
        command="去2楼搜索受困人员",
        floor=2,
        capability_required="search_for_victims",
        execution_group=0,
    )

    task = structured_task_from_mission_subtask(
        mission_id="mission-1",
        subtask=subtask,
        operator_id="operator-1",
        task_id="task-1",
    )

    assert task.task_id == "task-1"
    assert task.mission_id == "mission-1"
    assert task.robot_id == "robot-1"
    assert task.task_type == "search"
    assert task.target == {"floor": 2}
    assert task.required_skills == ["navigate_to_floor", "search_for_victims", "report_status"]
    assert task.command == "去2楼搜索受困人员"


def test_validate_structured_robot_task_rejects_missing_required_skills():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=[],
    )

    assert "required_skills must not be empty" in validate_structured_robot_task(task)


def test_planning_result_from_structured_task_uses_required_skill_order():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="rescue_search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims", "report_status"],
    )

    result = planning_result_from_structured_task(task)

    assert result.status == "planned"
    assert result.intent == "rescue_search"
    assert result.target_floor == 2
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]
    assert result.plan.steps[0].inputs == {"floor": 2}
