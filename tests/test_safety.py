from fireclaw_core.planner import RuleBasedPlanner
from fireclaw_core.robot import DryRunRobotAdapter, EnvironmentState, RobotState
from fireclaw_core.safety import SafetyGate
from fireclaw_core.robot import RobotActionResult
from fireclaw_core.skills import Skill, SkillRegistry, create_default_skill_registry


def _successful_result():
    return RobotActionResult(
        ok=True,
        status="succeeded",
        robot_id="external",
        mode="test",
        action="test_skill",
        dry_run=True,
        data={},
        timestamp="2026-06-01T00:00:00+00:00",
    )


def test_safety_allows_valid_dry_run_plan():
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = create_default_skill_registry(robot)

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        available_sensors=set(robot.available_sensors),
    )

    assert decision.status == "allow"
    assert decision.reasons == []


def test_safety_blocks_missing_skill_before_execution():
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    registry = SkillRegistry(skills={})

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "block"
    assert "Missing skill: navigate_to_floor" in decision.reasons


def test_safety_blocks_skill_when_required_sensor_is_missing():
    planning_result = RuleBasedPlanner().plan("运行 thermal_search")
    registry = SkillRegistry(
        skills={
            "thermal_search": Skill(
                name="thermal_search",
                description="Thermal victim search.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["thermal_camera"],
            )
        }
    )

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "block"
    assert decision.reasons == ["Skill thermal_search requires unavailable sensor: thermal_camera"]


def test_safety_allows_skill_when_required_sensor_is_available():
    planning_result = RuleBasedPlanner().plan("运行 thermal_search")
    registry = SkillRegistry(
        skills={
            "thermal_search": Skill(
                name="thermal_search",
                description="Thermal victim search.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["thermal_camera"],
            )
        }
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        available_sensors={"thermal_camera"},
    )

    assert decision.status == "allow"
    assert decision.reasons == []


def test_safety_clarifies_unparsed_command():
    planning_result = RuleBasedPlanner().plan("随便看看")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="robot-1"))

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "clarify"
    assert "请明确" in decision.reasons[0]


def test_safety_blocks_non_dry_run_mode():
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = create_default_skill_registry(robot)

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        operator_confirmed=True,
        available_sensors=set(robot.available_sensors),
    )

    assert decision.status == "block"
    # Default skills have dry_run_only=True, so they're blocked for real robot
    assert any("not allowed for real robot" in r for r in decision.reasons)


def test_safety_blocks_non_dry_run_direct_skill_invocation():
    planning_result = RuleBasedPlanner().plan("运行 navigate_to_floor")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="robot-1"))

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        operator_confirmed=True,
    )

    assert decision.status == "block"
    assert decision.reasons == ["Skill is not allowed for real robot execution: navigate_to_floor"]


def test_safety_blocks_retryable_skill_without_idempotency():
    planning_result = RuleBasedPlanner().plan("运行 flaky_policy")
    registry = SkillRegistry(
        skills={
            "flaky_policy": Skill(
                name="flaky_policy",
                description="Unsafe retry policy.",
                handler=lambda inputs: _successful_result(),
                max_attempts=2,
                idempotent=False,
            )
        }
    )

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "block"
    assert decision.reasons == ["Skill flaky_policy has max_attempts > 1 but is not idempotent."]


def test_safety_blocks_not_dry_run_only_skill_in_dry_run_mode():
    planning_result = RuleBasedPlanner().plan("运行 real_robot_policy")
    registry = SkillRegistry(
        skills={
            "real_robot_policy": Skill(
                name="real_robot_policy",
                description="Real robot policy.",
                handler=lambda inputs: _successful_result(),
                dry_run_only=False,
                allow_real_robot=True,
            )
        }
    )

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "block"
    assert decision.reasons == ["Skill is not dry-run only: real_robot_policy"]


def test_safety_blocks_non_dry_run_skill_without_real_robot_allowance():
    planning_result = RuleBasedPlanner().plan("运行 unsafe_real_policy")
    registry = SkillRegistry(
        skills={
            "unsafe_real_policy": Skill(
                name="unsafe_real_policy",
                description="Missing real robot allowance.",
                handler=lambda inputs: _successful_result(),
                dry_run_only=False,
                allow_real_robot=False,
            )
        }
    )

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=False)

    assert decision.status == "block"
    assert decision.reasons == ["Skill is not allowed for real robot execution: unsafe_real_policy"]


def test_safety_allows_non_dry_run_direct_skill_with_real_robot_allowance():
    planning_result = RuleBasedPlanner().plan("运行 real_robot_policy")
    registry = SkillRegistry(
        skills={
            "real_robot_policy": Skill(
                name="real_robot_policy",
                description="Explicitly allowed real robot policy.",
                handler=lambda inputs: _successful_result(),
                dry_run_only=False,
                allow_real_robot=True,
            )
        }
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        operator_confirmed=True,
    )

    assert decision.status == "allow"
    assert decision.reasons == []


def test_safety_requires_confirmation_for_real_robot_allowed_skill():
    planning_result = RuleBasedPlanner().plan("运行 real_robot_policy")
    registry = SkillRegistry(
        skills={
            "real_robot_policy": Skill(
                name="real_robot_policy",
                description="Explicitly allowed real robot policy.",
                handler=lambda inputs: _successful_result(),
                dry_run_only=False,
                allow_real_robot=True,
            )
        }
    )

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=False)

    assert decision.status == "require_confirmation"
    assert decision.reasons == [
        "Real robot execution requires operator confirmation: real_robot_policy"
    ]


def test_safety_requires_confirmation_for_high_risk_skill():
    planning_result = RuleBasedPlanner().plan("运行 smoke_entry")
    registry = SkillRegistry(
        skills={
            "smoke_entry": Skill(
                name="smoke_entry",
                description="High-risk smoke entry.",
                handler=lambda inputs: _successful_result(),
                risk_level="high",
            )
        }
    )

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "require_confirmation"
    assert decision.reasons == ["Skill smoke_entry has high risk level: high"]


def test_safety_allows_confirmed_real_robot_skill():
    planning_result = RuleBasedPlanner().plan("运行 real_robot_policy")
    registry = SkillRegistry(
        skills={
            "real_robot_policy": Skill(
                name="real_robot_policy",
                description="Explicitly allowed real robot policy.",
                handler=lambda inputs: _successful_result(),
                dry_run_only=False,
                allow_real_robot=True,
            )
        }
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        operator_confirmed=True,
    )

    assert decision.status == "allow"
    assert decision.reasons == []


def test_safety_blocks_when_robot_is_offline():
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="robot-1"))
    robot_state = RobotState(
        robot_id="robot-1",
        mode="simulator",
        dry_run=True,
        online=False,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=[],
        supports_real_execution=False,
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        robot_state=robot_state,
    )

    assert decision.status == "block"
    assert decision.reasons == ["Robot robot-1 is offline."]


def test_safety_blocks_when_robot_battery_is_too_low():
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="robot-1"))
    robot_state = RobotState(
        robot_id="robot-1",
        mode="simulator",
        dry_run=True,
        online=True,
        battery_percent=9.0,
        current_floor=1,
        available_sensors=[],
        supports_real_execution=False,
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        robot_state=robot_state,
    )

    assert decision.status == "block"
    assert decision.reasons == ["Robot robot-1 battery is too low: 9.0%."]


def test_safety_blocks_unreachable_target_floor_from_environment_state():
    planning_result = RuleBasedPlanner().plan("去五楼救人")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="robot-1"))
    environment_state = EnvironmentState(reachable_floors=[1, 2, 3], victims_by_floor={2: 1})

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        environment_state=environment_state,
    )

    assert decision.status == "block"
    assert decision.reasons == ["Target floor is not reachable: 5"]
