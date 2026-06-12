# tests/test_safety_unknown_state.py
from __future__ import annotations

from fireclaw_core.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.robot import EnvironmentState, RobotState
from fireclaw_core.safety import SafetyGate
from fireclaw_core.skills import Skill, SkillRegistry


def _dry_run_registry() -> SkillRegistry:
    registry = SkillRegistry(skills={})
    registry.register(Skill(name="navigate_to_floor", description="nav", handler=lambda inputs: None))
    return registry


def _real_robot_registry() -> SkillRegistry:
    registry = SkillRegistry(skills={})
    registry.register(Skill(
        name="navigate_to_floor",
        description="nav",
        handler=lambda inputs: None,
        dry_run_only=False,
        allow_real_robot=True,
    ))
    return registry


def _planning() -> PlanningResult:
    return PlanningResult(
        status="planned",
        message="planned",
        intent="search",
        target_floor=2,
        plan=Plan("search", [PlanStep("navigate_to_floor", {"floor": 2})]),
    )


def test_unknown_reachable_floors_warns_in_dry_run():
    decision = SafetyGate().evaluate(
        _planning(),
        _dry_run_registry(),
        dry_run=True,
        robot_state=RobotState("robot-1", "dry_run", True, True, 100.0, 1, [], False),
        environment_state=EnvironmentState(reachable_floors=None),
    )

    assert decision.status == "allow"
    assert "Reachable floors are unknown." in decision.warnings


def test_explicit_empty_reachable_floors_blocks():
    decision = SafetyGate().evaluate(
        _planning(),
        _dry_run_registry(),
        dry_run=True,
        robot_state=RobotState("robot-1", "dry_run", True, True, 100.0, 1, [], False),
        environment_state=EnvironmentState(reachable_floors=[]),
    )

    assert decision.status == "block"
    assert "Target floor is not reachable: 2" in decision.reasons


def test_unknown_battery_requires_confirmation_for_real_robot():
    decision = SafetyGate().evaluate(
        _planning(),
        _real_robot_registry(),
        dry_run=False,
        robot_state=RobotState("robot-1", "ros1", False, True, None, 1, [], True),
        environment_state=EnvironmentState(reachable_floors=[2]),
    )

    assert decision.status == "require_confirmation"
    assert "Robot robot-1 battery state is unknown." in decision.reasons


def test_unknown_sensors_require_confirmation_for_real_robot_skill_with_sensor_requirement() -> None:
    registry = SkillRegistry(skills={})
    registry.register(
        Skill(
            name="search_for_victims",
            description="search",
            handler=lambda inputs: None,
            dry_run_only=False,
            allow_real_robot=True,
            required_sensors=["thermal_camera"],
        )
    )
    planning = PlanningResult(
        status="planned",
        message="planned",
        intent="search",
        target_floor=2,
        plan=Plan("search", [PlanStep("search_for_victims", {"floor": 2})]),
    )
    decision = SafetyGate().evaluate(
        planning,
        registry,
        dry_run=False,
        robot_state=RobotState("robot-1", "ros1", False, True, None, 1, None, True),
        environment_state=EnvironmentState(reachable_floors=[2]),
    )
    assert decision.status == "require_confirmation"
    assert "Robot robot-1 available sensors are unknown." in decision.reasons
