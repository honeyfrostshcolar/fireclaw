# tests/test_task_contract.py
from __future__ import annotations

from fireclaw_core.mission.mission_planner import MissionSubtask
from fireclaw_core.task.task_contract import (
    StructuredRobotTask,
    planning_result_from_structured_task,
    skills_from_capability,
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
    assert task.required_skills == ["search_for_victims", "report_status"]
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


def test_structured_task_from_dict_round_trip():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims"],
        constraints={"execution_group": 0},
        priority="high",
        risk_level="medium",
        operator_id="op-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
    )
    reconstructed = StructuredRobotTask.from_dict(task.to_dict())
    assert reconstructed == task


def test_validate_structured_robot_task_rejects_empty_task_id():
    task = StructuredRobotTask(task_id="", task_type="search", target={}, required_skills=["nav"])
    assert "task_id must not be empty" in validate_structured_robot_task(task)


def test_validate_structured_robot_task_rejects_empty_task_type():
    task = StructuredRobotTask(task_id="t1", task_type="", target={}, required_skills=["nav"])
    assert "task_type must not be empty" in validate_structured_robot_task(task)


def test_validate_structured_robot_task_rejects_invalid_priority():
    task = StructuredRobotTask(task_id="t1", task_type="search", target={}, required_skills=["nav"], priority="urgent")
    errors = validate_structured_robot_task(task)
    assert any("priority" in e for e in errors)


def test_validate_structured_robot_task_rejects_invalid_risk_level():
    task = StructuredRobotTask(task_id="t1", task_type="search", target={}, required_skills=["nav"], risk_level="extreme")
    errors = validate_structured_robot_task(task)
    assert any("risk_level" in e for e in errors)


def test_validate_structured_robot_task_rejects_non_positive_floor():
    for floor in [0, -1]:
        task = StructuredRobotTask(task_id="t1", task_type="search", target={"floor": floor}, required_skills=["nav"])
        errors = validate_structured_robot_task(task)
        assert any("positive integer" in e for e in errors), f"floor={floor} should be rejected"


def test_validate_structured_robot_task_rejects_string_floor():
    task = StructuredRobotTask(task_id="t1", task_type="search", target={"floor": "2"}, required_skills=["nav"])
    errors = validate_structured_robot_task(task)
    assert any("positive integer" in e for e in errors)


def test_planning_result_from_structured_task_returns_clarify_on_invalid():
    task = StructuredRobotTask(task_id="", task_type="search", target={"floor": 2}, required_skills=["nav"])
    result = planning_result_from_structured_task(task)
    assert result.status == "clarify"
    assert result.intent == "search"
    assert "task_id must not be empty" in result.message


def test_skills_from_capability_uses_profile_skill_chain():
    chains = {
        "search_for_victims": ["navigate_to_floor", "search_for_victims", "report_status"],
        "return_to_safe_zone": ["return_to_safe_zone"],
    }

    assert skills_from_capability("search_for_victims", capability_skill_chains=chains) == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]


def test_skills_from_capability_preserves_default_mapping():
    assert skills_from_capability("search_for_victims") == [
        "search_for_victims",
        "report_status",
    ]


def test_point_target_populates_navigation_skill_inputs():
    task = StructuredRobotTask(
        task_id="task-point",
        task_type="navigation",
        target={
            "frame_id": "map",
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.25},
        },
        required_skills=["navigate_to_point"],
    )

    result = planning_result_from_structured_task(task)

    assert result.status == "planned"
    assert result.target_floor is None
    assert result.target_pose == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.25,
        "frame_id": "map",
    }
    assert result.plan.steps[0].inputs == result.target_pose


def test_point_target_prepends_navigation_to_default_capability_chain():
    subtask = MissionSubtask(
        robot_id="robot-1",
        command="去坐标 (2.0, 1.5) 搜索受困人员",
        floor=None,
        capability_required="search_for_victims",
        target={
            "frame_id": "map",
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
        },
    )

    task = structured_task_from_mission_subtask(
        mission_id="mission-1",
        subtask=subtask,
    )

    assert task.required_skills == [
        "navigate_to_point",
        "search_for_victims",
        "report_status",
    ]


def test_point_target_rejects_non_finite_coordinates():
    task = StructuredRobotTask(
        task_id="task-point",
        task_type="navigation",
        target={"pose": {"x": float("nan"), "y": 1.5}},
        required_skills=["navigate_to_point"],
    )

    errors = validate_structured_robot_task(task)

    assert "target.pose requires finite numeric x and y" in errors


def test_structured_task_preserves_allowed_skills():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor", "report_status"],
    })

    assert task.allowed_skills == ["navigate_to_floor", "report_status"]
    assert task.to_dict()["allowed_skills"] == ["navigate_to_floor", "report_status"]


def test_primitive_composition_allows_empty_required_skills_when_allowed_skills_present():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor"],
    })

    assert validate_structured_robot_task(task) == []


def test_primitive_composition_rejects_empty_allowed_skills():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": [],
    })

    errors = validate_structured_robot_task(task)

    assert any("allowed_skills" in error for error in errors)


def test_required_skills_must_be_within_allowed_skills_when_allowlist_is_explicit():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "search",
        "target": {"floor": 2},
        "required_skills": ["search_for_victims"],
        "allowed_skills": ["navigate_to_floor"],
    })

    errors = validate_structured_robot_task(task)

    assert any("search_for_victims" in error and "allowed_skills" in error for error in errors)
