from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.execution.skill_plugin import PhysicalSkillCatalog
from fireclaw_core.mission.mission_planner import MissionSubtask
from fireclaw_core.task.task_contract import (
    StructuredRobotTask,
    planning_result_from_structured_task,
    skills_from_capability,
    structured_task_from_mission_subtask,
    validate_structured_robot_task,
)


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"
POINT_TARGET = {
    "frame_id": "map",
    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.25},
}
POINT_INPUTS = {
    "x": 2.0,
    "y": 1.5,
    "yaw": 0.25,
    "frame_id": "map",
}


def _navigation_catalog() -> PhysicalSkillCatalog:
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    )
    return PhysicalSkillCatalog(agent.plugin_host)


def test_structured_task_from_mission_subtask_uses_profile_skill_chain() -> None:
    subtask = MissionSubtask(
        robot_id="robot-1",
        command="导航到指定 map 目标点",
        floor=None,
        capability_required="navigation",
        execution_group=0,
        task_type="navigate",
        target=POINT_TARGET,
    )

    task = structured_task_from_mission_subtask(
        mission_id="mission-1",
        subtask=subtask,
        operator_id="operator-1",
        task_id="task-1",
        capability_skill_chains={"navigation": ["navigate_to_point"]},
    )

    assert task.task_id == "task-1"
    assert task.mission_id == "mission-1"
    assert task.robot_id == "robot-1"
    assert task.task_type == "navigate"
    assert task.target == POINT_TARGET
    assert task.required_skills == ["navigate_to_point"]
    assert task.command == "导航到指定 map 目标点"


def test_validate_structured_robot_task_rejects_missing_required_skills() -> None:
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=[],
    )

    assert "required_skills must not be empty" in (
        validate_structured_robot_task(task)
    )


def test_planning_result_projects_inputs_through_plugin_catalog() -> None:
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
    )

    result = planning_result_from_structured_task(
        task,
        skill_catalog=_navigation_catalog(),
    )

    assert result.status == "planned"
    assert result.intent == "navigate"
    assert result.target_floor is None
    assert result.target_pose == POINT_INPUTS
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_point"
    ]
    assert result.plan.steps[0].inputs == POINT_INPUTS


def test_planning_result_without_plugin_catalog_does_not_invent_inputs() -> None:
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="inspect",
        target={"zone_id": "alpha"},
        required_skills=["inspect_local_hazard"],
    )

    result = planning_result_from_structured_task(task)

    assert result.status == "planned"
    assert result.plan.steps[0].inputs == {}


def test_structured_task_from_dict_round_trip() -> None:
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        constraints={"execution_group": 0},
        priority="high",
        risk_level="medium",
        operator_id="op-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="导航到指定 map 目标点",
    )

    assert StructuredRobotTask.from_dict(task.to_dict()) == task


def test_validate_rejects_empty_task_id() -> None:
    task = StructuredRobotTask(
        task_id="",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
    )
    assert "task_id must not be empty" in validate_structured_robot_task(task)


def test_validate_rejects_empty_task_type() -> None:
    task = StructuredRobotTask(
        task_id="t1",
        task_type="",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
    )
    assert "task_type must not be empty" in validate_structured_robot_task(task)


def test_validate_rejects_invalid_priority() -> None:
    task = StructuredRobotTask(
        task_id="t1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        priority="urgent",
    )
    assert any(
        "priority" in error for error in validate_structured_robot_task(task)
    )


def test_validate_rejects_invalid_risk_level() -> None:
    task = StructuredRobotTask(
        task_id="t1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        risk_level="extreme",
    )
    assert any(
        "risk_level" in error
        for error in validate_structured_robot_task(task)
    )


def test_planning_result_returns_clarify_on_invalid_contract() -> None:
    task = StructuredRobotTask(
        task_id="",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
    )

    result = planning_result_from_structured_task(task)

    assert result.status == "clarify"
    assert result.intent == "navigate"
    assert "task_id must not be empty" in result.message


def test_skills_from_capability_uses_profile_owned_chain() -> None:
    chains = {
        "navigation_acceptance": [
            "navigate_to_point",
            "verify_navigation_evidence",
        ]
    }

    assert skills_from_capability(
        "navigation_acceptance",
        capability_skill_chains=chains,
    ) == ["navigate_to_point", "verify_navigation_evidence"]


def test_skills_from_capability_has_no_core_domain_mapping() -> None:
    assert skills_from_capability("plugin_defined_capability") == [
        "plugin_defined_capability"
    ]


def test_plugin_catalog_can_auto_include_navigation_for_point_target() -> None:
    subtask = MissionSubtask(
        robot_id="robot-1",
        command="检查目标点并抵达",
        floor=None,
        capability_required="inspect_local_hazard",
        task_type="inspect",
        target=POINT_TARGET,
    )

    task = structured_task_from_mission_subtask(
        mission_id="mission-1",
        subtask=subtask,
        capability_skill_chains={
            "inspect_local_hazard": ["inspect_local_hazard"]
        },
        skill_catalog=_navigation_catalog(),
    )

    assert task.required_skills == [
        "navigate_to_point",
        "inspect_local_hazard",
    ]


def test_point_target_rejects_non_finite_coordinates() -> None:
    task = StructuredRobotTask(
        task_id="task-point",
        task_type="navigate",
        target={"pose": {"x": float("nan"), "y": 1.5}},
        required_skills=["navigate_to_point"],
    )

    assert "target.pose requires finite numeric x and y" in (
        validate_structured_robot_task(task)
    )


def test_structured_task_preserves_explicit_allowed_tools() -> None:
    task = StructuredRobotTask.from_dict(
        {
            "task_id": "t1",
            "task_type": "primitive_composition",
            "target": POINT_TARGET,
            "required_skills": [],
            "allowed_skills": [
                "navigate_to_point",
                "inspect_local_hazard",
            ],
        }
    )

    assert task.allowed_skills == [
        "navigate_to_point",
        "inspect_local_hazard",
    ]
    assert task.to_dict()["allowed_skills"] == task.allowed_skills


def test_primitive_composition_allows_empty_required_with_allowlist() -> None:
    task = StructuredRobotTask.from_dict(
        {
            "task_id": "t1",
            "task_type": "primitive_composition",
            "target": POINT_TARGET,
            "required_skills": [],
            "allowed_skills": ["navigate_to_point"],
        }
    )

    assert validate_structured_robot_task(task) == []


def test_primitive_composition_rejects_empty_allowlist() -> None:
    task = StructuredRobotTask.from_dict(
        {
            "task_id": "t1",
            "task_type": "primitive_composition",
            "target": POINT_TARGET,
            "required_skills": [],
            "allowed_skills": [],
        }
    )

    assert any(
        "allowed_skills" in error
        for error in validate_structured_robot_task(task)
    )


def test_required_tools_must_be_inside_explicit_allowlist() -> None:
    task = StructuredRobotTask.from_dict(
        {
            "task_id": "t1",
            "task_type": "navigate",
            "target": POINT_TARGET,
            "required_skills": ["navigate_to_point"],
            "allowed_skills": ["inspect_local_hazard"],
        }
    )

    assert any(
        "navigate_to_point" in error and "allowed_skills" in error
        for error in validate_structured_robot_task(task)
    )
