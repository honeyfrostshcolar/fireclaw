from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
    execution_action_hash,
)
from fireclaw_core.execution.executor import PlanExecutor
from fireclaw_core.execution.skills import (
    Skill,
    SkillRegistry,
    create_default_skill_registry,
)
from fireclaw_core.planner.planner import Plan, PlanStep
from fireclaw_core.policy.capability import (
    CapabilityActor,
    CapabilityPolicyContext,
    CapabilityPolicyPipeline,
    CapabilityRobotProfile,
    CapabilityRuntimeState,
)


def _actor() -> CapabilityActor:
    return CapabilityActor(
        actor_id="operator-1",
        role="operator",
        scopes=frozenset({"task.submit"}),
        source="gateway",
    )


def _runtime_state(
    *,
    sensors: frozenset[str] = frozenset({"lidar", "thermal_camera"}),
) -> CapabilityRuntimeState:
    return CapabilityRuntimeState(
        robot_id="robot-a",
        online=True,
        battery_percent=80.0,
        available_sensors=sensors,
        emergency_stop_active=False,
        state_ref="robot-a:state:test",
    )


def _context(**overrides) -> CapabilityPolicyContext:
    values = {
        "phase": "planning",
        "actor": _actor(),
        "robot_id": "robot-a",
        "mission_id": "mission-1",
        "task_id": "task-1",
        "delegated_operator_id": "operator-1",
        "allowed_skills": frozenset(
            {"navigate_to_point", "search_for_victims"}
        ),
        "target": {
            "pose": {"x": 2.0, "y": 3.0, "yaw": 0.0},
            "frame_id": "map",
        },
        "runtime_state": _runtime_state(),
        "dry_run": True,
    }
    values.update(overrides)
    return CapabilityPolicyContext(**values)


def test_planning_projection_records_each_exclusion_layer():
    robot = DryRunRobotAdapter(
        robot_id="robot-a",
        available_sensors={"lidar", "thermal_camera"},
    )
    registry = create_default_skill_registry(robot)
    pipeline = CapabilityPolicyPipeline(
        plugin_host=registry.host,
        registry=registry,
    )
    profile = CapabilityRobotProfile(
        robot_id="robot-a",
        enabled=True,
        enabled_skills=frozenset(
            {
                "navigate_to_point",
                "search_for_victims",
                "report_status",
            }
        ),
        llm_exposed_skills=frozenset({"navigate_to_point"}),
        profile_ref="profiles/robot-a.toml",
    )

    projection = pipeline.project(
        {
            "navigate_to_point",
            "search_for_victims",
            "report_status",
        },
        context=_context(robot_profile=profile),
    )

    assert projection.after == ("navigate_to_point",)
    decisions = {
        decision.skill_name: decision
        for decision in projection.decisions
    }
    assert decisions["navigate_to_point"].status == "allow"
    assert decisions["search_for_victims"].reason_code == (
        "skill_not_exposed_to_llm"
    )
    assert decisions["report_status"].reason_code == (
        "skill_outside_task_delegation"
    )
    plugin_stage = next(
        stage
        for stage in decisions["navigate_to_point"].stages
        if stage.stage == "plugin_exposure"
    )
    assert plugin_stage.evidence["owner_plugin_id"] == (
        "fireclaw.navigation.move-base"
    )


def test_execution_policy_rejects_protected_target_rewrite():
    robot = DryRunRobotAdapter(
        robot_id="robot-a",
        available_sensors={"lidar"},
    )
    registry = create_default_skill_registry(robot)
    pipeline = CapabilityPolicyPipeline(
        plugin_host=registry.host,
        registry=registry,
    )

    decision = pipeline.evaluate(
        "navigate_to_point",
        {"x": 9.0, "y": 3.0, "yaw": 0.0, "frame_id": "map"},
        context=_context(
            phase="execution",
            target={
                "pose": {"x": 2.0, "y": 4.0, "yaw": 0.0},
                "frame_id": "map",
            },
            runtime_state=_runtime_state(
                sensors=frozenset({"lidar"})
            ),
            safety_status="allow",
        ),
    )

    assert decision.status == "block"
    assert decision.reason_code == "protected_task_input_mismatch"


def test_execution_policy_rechecks_exact_authorized_inputs():
    robot = DryRunRobotAdapter(
        robot_id="robot-a",
        available_sensors={"lidar"},
    )
    registry = create_default_skill_registry(robot)
    pipeline = CapabilityPolicyPipeline(
        plugin_host=registry.host,
        registry=registry,
    )
    authorized_inputs = {
        "x": 2.0,
        "y": 3.0,
        "yaw": 0.0,
        "frame_id": "map",
    }
    grant = VerifiedExecutionAuthorization(
        authorization_id="exec-auth-1",
        request_id="request-1",
        operator_id="operator-1",
        scope_hash="scope-1",
        authorized_action_hashes=frozenset(
            {
                execution_action_hash(
                    "navigate_to_point",
                    authorized_inputs,
                )
            }
        ),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
    )

    decision = pipeline.evaluate(
        "navigate_to_point",
        {
            "x": 2.0,
            "y": 4.0,
            "yaw": 0.0,
            "frame_id": "map",
        },
        context=_context(
            phase="execution",
            target={
                "pose": {"x": 2.0, "y": 4.0, "yaw": 0.0},
                "frame_id": "map",
            },
            runtime_state=_runtime_state(
                sensors=frozenset({"lidar"})
            ),
            dry_run=False,
            safety_status="allow",
            execution_authorization=grant,
        ),
    )

    assert decision.status == "block"
    assert decision.reason_code == "authorization_action_mismatch"


def test_execution_policy_rejects_expired_authorization():
    robot = DryRunRobotAdapter(
        robot_id="robot-a",
        available_sensors={"lidar"},
    )
    registry = create_default_skill_registry(robot)
    pipeline = CapabilityPolicyPipeline(
        plugin_host=registry.host,
        registry=registry,
    )
    inputs = {
        "x": 2.0,
        "y": 3.0,
        "yaw": 0.0,
        "frame_id": "map",
    }
    grant = VerifiedExecutionAuthorization(
        authorization_id="exec-auth-expired",
        request_id="request-1",
        operator_id="operator-1",
        scope_hash="scope-1",
        authorized_action_hashes=frozenset(
            {execution_action_hash("navigate_to_point", inputs)}
        ),
        expires_at=(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(),
    )

    decision = pipeline.evaluate(
        "navigate_to_point",
        inputs,
        context=_context(
            phase="execution",
            runtime_state=_runtime_state(
                sensors=frozenset({"lidar"})
            ),
            dry_run=False,
            safety_status="allow",
            execution_authorization=grant,
        ),
    )

    assert decision.status == "block"
    assert decision.reason_code == "execution_authorization_expired"


def test_executor_applies_policy_before_skill_handler():
    calls: list[dict] = []
    skill = Skill(
        name="test_motion",
        description="test",
        handler=lambda inputs: calls.append(inputs),  # type: ignore[arg-type]
        dry_run_only=True,
    )
    registry = SkillRegistry({})
    registry.register(skill)
    pipeline = CapabilityPolicyPipeline(
        plugin_host=registry.host,
        registry=registry,
    )
    context = _context(
        phase="execution",
        allowed_skills=frozenset(),
        target={},
        runtime_state=_runtime_state(sensors=frozenset()),
        safety_status="allow",
    )

    result = PlanExecutor(registry).execute(
        Plan(
            intent="test",
            steps=[PlanStep(skill_name="test_motion", inputs={})],
        ),
        capability_policy_check=(
            lambda selected, inputs: pipeline.evaluate(
                selected.name,
                inputs,
                context=context,
            )
        ),
    )

    assert result.status == "failed"
    assert result.steps[0].failure_category == (
        "skill_outside_task_delegation"
    )
    assert calls == []


def test_identity_scope_is_part_of_every_capability_decision():
    robot = DryRunRobotAdapter(
        robot_id="robot-a",
        available_sensors={"lidar"},
    )
    registry = create_default_skill_registry(robot)
    pipeline = CapabilityPolicyPipeline(
        plugin_host=registry.host,
        registry=registry,
    )
    observer = CapabilityActor(
        actor_id="observer-1",
        role="observer",
        scopes=frozenset({"state.read"}),
        source="gateway",
    )

    decision = pipeline.evaluate(
        "navigate_to_point",
        {},
        context=_context(
            actor=observer,
            delegated_operator_id=None,
        ),
    )

    assert decision.status == "block"
    assert decision.reason_code == "actor_scope_missing"
