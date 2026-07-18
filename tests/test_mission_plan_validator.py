# tests/test_mission_plan_validator.py
from __future__ import annotations

from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


def test_validator_allows_valid_plan():
    registry = RobotRegistry([
        RobotRegistryEntry("robot-1", "http://robot-1", capabilities=["search_for_victims"])
    ])
    plan = MissionPlan(
        intent="search",
        command="去2楼搜索",
        subtasks=[MissionSubtask("robot-1", "去2楼搜索", 2, "search_for_victims")],
    )

    assert MissionPlanValidator().validate(plan, registry) == []


def test_validator_rejects_missing_robot_and_capability_mismatch():
    registry = RobotRegistry([
        RobotRegistryEntry("robot-1", "http://robot-1", capabilities=["patrol"])
    ])
    plan = MissionPlan(
        intent="search",
        command="去2楼搜索",
        subtasks=[
            MissionSubtask("robot-1", "去2楼搜索", 2, "search_for_victims"),
            MissionSubtask("missing", "去3楼搜索", 3, "search_for_victims"),
        ],
    )

    errors = MissionPlanValidator().validate(plan, registry)

    assert "Robot robot-1 lacks required capability search_for_victims." in errors
    assert "Robot missing is not registered." in errors
