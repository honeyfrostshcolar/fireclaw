from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
)
from fireclaw_core.planner.planner import RuleBasedPlanner
from fireclaw_core.agent.robot import DryRunRobotAdapter, RobotState
from fireclaw_core.safety.safety import SafetyGate
from fireclaw_core.agent.robot import RobotActionResult
from fireclaw_core.execution.skills import Skill, SkillRegistry


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _navigation_registry(robot_id: str = "robot-1"):
    return FireClawAgent(
        robot=DryRunRobotAdapter(robot_id=robot_id),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    ).registry


def _verified_authorization() -> VerifiedExecutionAuthorization:
    return VerifiedExecutionAuthorization(
        authorization_id="exec-auth-test",
        request_id="approval-test",
        operator_id="supervisor-test",
        scope_hash="scope-test",
        authorized_action_hashes=frozenset({"action-test"}),
        expires_at="2099-01-01T00:00:00+00:00",
    )


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
    planning_result = RuleBasedPlanner().plan("导航到坐标 (2.0, 1.5)")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = _navigation_registry()

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        available_sensors=set(robot.available_sensors),
    )

    assert decision.status == "allow"
    assert decision.reasons == []


def test_safety_blocks_missing_skill_before_execution():
    planning_result = RuleBasedPlanner().plan("导航到坐标 (2.0, 1.5)")
    registry = SkillRegistry(skills={})

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "block"
    assert "Missing skill: navigate_to_point" in decision.reasons


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
    registry = SkillRegistry(skills={})

    decision = SafetyGate().evaluate(planning_result, registry, dry_run=True)

    assert decision.status == "clarify"
    assert "请明确" in decision.reasons[0]


def test_safety_blocks_non_dry_run_mode():
    planning_result = RuleBasedPlanner().plan("运行 simulation_only_motion")
    registry = SkillRegistry(
        skills={
            "simulation_only_motion": Skill(
                name="simulation_only_motion",
                description="Simulation-only motion fixture.",
                handler=lambda inputs: _successful_result(),
            )
        }
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        execution_authorization=_verified_authorization(),
    )

    assert decision.status == "block"
    assert decision.reasons == [
        "Skill is not allowed for real robot execution: "
        "simulation_only_motion"
    ]


def test_safety_blocks_non_dry_run_direct_skill_invocation():
    planning_result = RuleBasedPlanner().plan("运行 simulation_only_inspection")
    registry = SkillRegistry(
        skills={
            "simulation_only_inspection": Skill(
                name="simulation_only_inspection",
                description="Simulation-only inspection fixture.",
                handler=lambda inputs: _successful_result(),
            )
        }
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        execution_authorization=_verified_authorization(),
        available_sensors={"rgb_camera", "thermal_camera", "lidar"},
    )

    assert decision.status == "block"
    assert decision.reasons == [
        "Skill is not allowed for real robot execution: "
        "simulation_only_inspection"
    ]


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
        execution_authorization=_verified_authorization(),
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
        execution_authorization=_verified_authorization(),
    )

    assert decision.status == "allow"
    assert decision.reasons == []


def test_safety_blocks_when_robot_is_offline():
    planning_result = RuleBasedPlanner().plan("导航到坐标 (2.0, 1.5)")
    registry = _navigation_registry()
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
    planning_result = RuleBasedPlanner().plan("导航到坐标 (2.0, 1.5)")
    registry = _navigation_registry()
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


def test_safety_clarifies_floor_only_command_in_point_navigation_core():
    planning_result = RuleBasedPlanner().plan("去五楼救人")
    registry = _navigation_registry()

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
    )

    assert decision.status == "clarify"
    assert "map 坐标系中的目标点" in decision.reasons[0]


def test_safety_blocks_visual_inspection_when_rgb_camera_is_degraded() -> None:
    planning_result = RuleBasedPlanner().plan("运行 visual_inspection")
    registry = SkillRegistry(
        skills={
            "visual_inspection": Skill(
                name="visual_inspection",
                description="Inspect the local scene using RGB imagery.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["rgb_camera"],
                dry_run_only=False,
                allow_real_robot=True,
            )
        }
    )
    robot_state = RobotState(
        robot_id="robot-1",
        mode="ros1",
        dry_run=False,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=["lidar"],
        supports_real_execution=True,
        sensor_diagnostics={
            "source": "ros1",
            "verified_sensors": ["lidar"],
            "findings": [
                {
                    "sensor": "rgb_camera",
                    "topic": "/camera/image_raw",
                    "message_type": "sensor_msgs/Image",
                    "status": "degraded",
                    "confidence": 0.9,
                    "source": "ros1",
                    "reason": "topic exists but no recent message within 2.0s",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        robot_state=robot_state,
    )

    assert decision.status == "block"
    assert (
        "Skill visual_inspection requires unavailable sensor: rgb_camera"
        in decision.reasons
    )


def test_safety_blocks_navigation_when_lidar_health_is_invalid() -> None:
    planning_result = RuleBasedPlanner().plan("导航到坐标 (2.0, 1.5)")
    registry = _navigation_registry()
    robot_state = RobotState(
        robot_id="robot-1",
        mode="ros1",
        dry_run=False,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=["rgb_camera"],
        supports_real_execution=True,
        sensor_diagnostics={
            "source": "ros1",
            "verified_sensors": ["rgb_camera"],
            "findings": [
                {
                    "sensor": "lidar",
                    "topic": "/scan",
                    "message_type": "sensor_msgs/LaserScan",
                    "status": "degraded",
                    "health_status": "invalid",
                    "health_reason": "no finite ranges",
                    "confidence": 0.99,
                    "source": "ros1",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        robot_state=robot_state,
        execution_authorization=_verified_authorization(),
    )

    assert decision.status == "block"
    assert (
        "Skill navigate_to_point requires lidar, but health is invalid: "
        "no finite ranges"
    ) in decision.reasons


def test_safety_requires_confirmation_when_imu_health_is_unknown_for_navigation() -> None:
    planning_result = RuleBasedPlanner().plan("运行 stabilized_motion")
    registry = SkillRegistry(
        skills={
            "stabilized_motion": Skill(
                name="stabilized_motion",
                description="Run a motion primitive requiring IMU health.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["imu"],
                allow_real_robot=True,
                dry_run_only=False,
                idempotent=True,
            )
        }
    )
    robot_state = RobotState(
        robot_id="robot-1",
        mode="ros1",
        dry_run=False,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=[],
        supports_real_execution=True,
        sensor_diagnostics={
            "source": "ros1",
            "verified_sensors": [],
            "findings": [
                {
                    "sensor": "imu",
                    "topic": "/imu",
                    "message_type": "sensor_msgs/Imu",
                    "status": "degraded",
                    "health_status": "unknown",
                    "health_reason": "observation age is unknown",
                    "confidence": 0.99,
                    "source": "ros1",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        robot_state=robot_state,
    )

    assert decision.status == "require_confirmation"
    assert (
        "Skill stabilized_motion requires imu, but health is unknown: "
        "observation age is unknown"
    ) in decision.reasons


def test_safety_warns_for_unknown_lidar_health_in_dry_run() -> None:
    planning_result = RuleBasedPlanner().plan("运行 local_motion")
    registry = SkillRegistry(
        skills={
            "local_motion": Skill(
                name="local_motion",
                description="Run a local motion primitive.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["lidar"],
                dry_run_only=True,
                idempotent=True,
            )
        }
    )
    robot_state = RobotState(
        robot_id="robot-1",
        mode="simulator",
        dry_run=True,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=[],
        supports_real_execution=False,
        sensor_diagnostics={
            "source": "simulator",
            "verified_sensors": [],
            "findings": [
                {
                    "sensor": "lidar",
                    "topic": "/scan",
                    "message_type": "sensor_msgs/LaserScan",
                    "status": "degraded",
                    "health_status": "unknown",
                    "health_reason": "observation age is unknown",
                    "confidence": 0.99,
                    "source": "simulator",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        robot_state=robot_state,
    )

    assert decision.status == "allow"
    assert (
        "Skill local_motion requires lidar, but health is unknown: "
        "observation age is unknown"
    ) in decision.warnings


def test_safety_uses_backend_verified_sensors_from_robot_state() -> None:
    planning_result = RuleBasedPlanner().plan("导航到坐标 (2.0, 1.5)")
    registry = _navigation_registry()
    robot_state = RobotState(
        robot_id="robot-1",
        mode="simulator",
        dry_run=True,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=["rgb_camera", "lidar", "thermal_camera"],
        supports_real_execution=False,
        sensor_diagnostics={
            "source": "simulator",
            "verified_sensors": ["rgb_camera", "lidar", "thermal_camera"],
            "findings": [],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        robot_state=robot_state,
    )

    assert decision.status == "allow"
