from pathlib import Path

from fireclaw_core.execution.action_runtime import RegisteredActionBackend, RobotActionRuntime
from fireclaw_core.execution.executor import PlanExecutor
from fireclaw_core.planner.planner import Plan, PlanStep, RuleBasedPlanner
from fireclaw_core.agent.robot import DryRunRobotAdapter, RobotActionResult
from fireclaw_core.execution.skills import Skill, SkillRegistry, create_default_skill_registry
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.plugin.plugin_host import FireClawPluginHost


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _navigation_registry(robot: DryRunRobotAdapter) -> SkillRegistry:
    host = FireClawPluginHost()
    load_fireclaw_extensions(
        host,
        (EXTENSIONS,),
        mode="simulation",
        role="robot_agent",
        services={"adapter": "dry-run"},
    )
    return create_default_skill_registry(
        robot,
        action_runtime=RobotActionRuntime(
            backend=RegisteredActionBackend(robot),
        ),
        plugin_host=host,
    )


def test_default_registry_exposes_typed_point_navigation_skill():
    registry = _navigation_registry(DryRunRobotAdapter(robot_id="robot-point"))

    skill = registry.get("navigate_to_point")

    assert skill is not None
    assert skill.input_schema["required"] == ["x", "y"]
    assert skill.preconditions == ["robot_online", "target_point_reachable"]
    assert skill.metadata["spatial_scope"] == "single_floor_2d"
    assert registry.get("navigate_to_waypoint") is None


def test_executor_emits_live_events_for_successful_steps():
    planning_result = RuleBasedPlanner().plan("去坐标 (2.0, 1.5)")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = _navigation_registry(robot)
    events = []

    result = PlanExecutor(registry, event_sink=lambda event_type, payload: events.append((event_type, payload))).execute(
        planning_result.plan
    )

    assert result.status == "succeeded"
    assert [event_type for event_type, _payload in events[:3]] == [
        "skill.started",
        "skill.attempted",
        "skill.succeeded",
    ]
    assert events[0][1]["skill_name"] == "navigate_to_point"
    assert events[0][1]["inputs"] == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert events[1][1]["attempt_number"] == 1
    assert events[1][1]["status"] == "succeeded"
    assert events[1][1]["output"]["action"] == "navigate_to_point"
    assert events[2][1]["status"] == "succeeded"
    assert [event_type for event_type, _payload in events].count("skill.started") == 1
    assert [event_type for event_type, _payload in events].count("skill.attempted") == 1
    assert [event_type for event_type, _payload in events].count("skill.succeeded") == 1


def test_executor_emits_retry_and_terminal_failure_events():
    calls = []
    events = []

    def failing_skill(inputs):
        calls.append(inputs)
        return RobotActionResult(
            ok=False,
            status="failed",
            robot_id="external",
            mode="test",
            action="failing_skill",
            dry_run=True,
            data={"attempt": len(calls)},
            timestamp=f"2026-06-01T00:00:0{len(calls)}+00:00",
            error="still blocked",
        )

    registry = SkillRegistry(
        skills={
            "failing_skill": Skill(
                name="failing_skill",
                description="Always fails.",
                handler=failing_skill,
                max_attempts=2,
            )
        }
    )
    plan = Plan(
        intent="direct_skill_invocation",
        steps=[PlanStep("failing_skill", {"text": "二楼"})],
    )

    result = PlanExecutor(registry, event_sink=lambda event_type, payload: events.append((event_type, payload))).execute(
        plan
    )

    assert result.status == "failed"
    assert [event_type for event_type, _payload in events] == [
        "skill.started",
        "skill.attempted",
        "skill.attempted",
        "skill.failed",
    ]
    assert events[1][1]["attempt_number"] == 1
    assert events[1][1]["status"] == "failed"
    assert events[2][1]["attempt_number"] == 2
    assert events[3][1]["skill_name"] == "failing_skill"
    assert events[3][1]["failure_category"] == "recoverable_exhausted"
    assert events[3][1]["operator_action"] == "escalate"


def test_executor_runs_single_floor_point_navigation_from_plugin():
    planning_result = RuleBasedPlanner().plan("去坐标 (3.0, -1.5)")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = _navigation_registry(robot)

    result = PlanExecutor(registry).execute(planning_result.plan)

    assert result.status == "succeeded"
    assert [step.skill_name for step in result.steps] == ["navigate_to_point"]
    assert all(step.status == "succeeded" for step in result.steps)
    assert result.steps[0].output["goal_reached"] is True
    assert result.steps[0].output["frame_id"] == "map"


def test_executor_records_attempt_history_for_successful_steps():
    planning_result = RuleBasedPlanner().plan("去坐标 (2.0, 1.0)")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = _navigation_registry(robot)

    result = PlanExecutor(registry).execute(planning_result.plan)

    first_step = result.steps[0]
    assert first_step.attempt_count == 1
    assert first_step.failure_category is None
    assert first_step.operator_action is None
    assert len(first_step.attempts) == 1
    assert first_step.attempts[0].attempt_number == 1
    assert first_step.attempts[0].status == "succeeded"
    assert first_step.attempts[0].output["goal_reached"] is True
    assert first_step.attempts[0].error is None


def test_executor_stops_when_skill_fails():
    calls = []

    def result(action: str, *, ok: bool) -> RobotActionResult:
        calls.append(action)
        return RobotActionResult(
            ok=ok,
            status="succeeded" if ok else "failed",
            robot_id="robot-1",
            mode="test",
            action=action,
            dry_run=True,
            data={},
            timestamp="2026-06-01T00:00:00+00:00",
            error=None if ok else f"{action} failed",
        )

    registry = SkillRegistry(
        skills={
            "first": Skill("first", "First.", lambda _inputs: result("first", ok=True)),
            "second": Skill("second", "Second.", lambda _inputs: result("second", ok=False)),
            "third": Skill("third", "Third.", lambda _inputs: result("third", ok=True)),
        }
    )
    plan = Plan(
        intent="executor_failure",
        steps=[PlanStep("first", {}), PlanStep("second", {}), PlanStep("third", {})],
    )

    execution = PlanExecutor(registry).execute(plan)

    assert execution.status == "failed"
    assert [step.skill_name for step in execution.steps] == ["first", "second"]
    assert execution.steps[-1].status == "failed"
    assert calls == ["first", "second"]


def test_executor_cooperatively_cancels_between_steps():
    calls = []

    def succeed(action: str) -> RobotActionResult:
        calls.append(action)
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id="robot-1",
            mode="test",
            action=action,
            dry_run=True,
            data={},
            timestamp="2026-06-01T00:00:00+00:00",
        )

    registry = SkillRegistry(
        skills={
            "first": Skill("first", "First.", lambda _inputs: succeed("first")),
            "second": Skill("second", "Second.", lambda _inputs: succeed("second")),
        }
    )
    plan = Plan(
        intent="executor_cancellation",
        steps=[PlanStep("first", {}), PlanStep("second", {})],
    )
    checks = {"count": 0}

    def cancellation_requested():
        checks["count"] += 1
        return checks["count"] >= 2

    result = PlanExecutor(
        registry,
        cancellation_requested=cancellation_requested,
    ).execute(plan)

    assert result.status == "cancelled"
    assert [step.skill_name for step in result.steps] == ["first"]
    assert calls == ["first"]


def test_executor_preserves_timed_out_terminal_status():
    def timed_out(_inputs):
        return RobotActionResult(
            ok=False,
            status="timed_out",
            robot_id="robot-1",
            mode="test",
            action="slow_physical_action",
            dry_run=False,
            data={
                "cancellation_acknowledged": True,
                "runtime_stopped": True,
                "resource_release_safe": True,
            },
            timestamp="2026-08-09T00:00:00+00:00",
            error="Physical action deadline exceeded.",
        )

    registry = SkillRegistry(
        skills={
            "slow_physical_action": Skill(
                "slow_physical_action",
                "A bounded physical action.",
                timed_out,
            )
        }
    )

    execution = PlanExecutor(registry).execute(
        Plan(
            intent="timeout_contract",
            steps=[PlanStep("slow_physical_action", {})],
        )
    )

    assert execution.status == "timed_out"
    assert execution.steps[0].status == "timed_out"
    assert execution.steps[0].attempts[0].status == "timed_out"
    assert execution.steps[0].output["runtime_stopped"] is True


def test_executor_retries_retryable_skill_until_it_succeeds():
    calls = []

    def flaky_skill(inputs):
        calls.append(inputs)
        if len(calls) == 1:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="test",
                action="flaky_skill",
                dry_run=True,
                data={"attempt": 1},
                timestamp="2026-06-01T00:00:00+00:00",
                error="temporary failure",
            )
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id="external",
            mode="test",
            action="flaky_skill",
            dry_run=True,
            data={"attempt": 2},
            timestamp="2026-06-01T00:00:01+00:00",
        )

    registry = SkillRegistry(
        skills={
            "flaky_skill": Skill(
                name="flaky_skill",
                description="Fails once, then succeeds.",
                handler=flaky_skill,
                max_attempts=2,
            )
        }
    )
    plan = Plan(
        intent="direct_skill_invocation",
        steps=[PlanStep("flaky_skill", {"text": "二楼"})],
    )

    result = PlanExecutor(registry).execute(plan)

    assert result.status == "succeeded"
    assert len(calls) == 2
    assert result.steps[0].status == "succeeded"
    assert result.steps[0].attempt_count == 2
    assert [attempt.status for attempt in result.steps[0].attempts] == ["failed", "succeeded"]
    assert result.steps[0].output["attempt"] == 2


def test_executor_escalates_when_retryable_skill_exhausts_attempts():
    calls = []

    def failing_skill(inputs):
        calls.append(inputs)
        return RobotActionResult(
            ok=False,
            status="failed",
            robot_id="external",
            mode="test",
            action="failing_skill",
            dry_run=True,
            data={"attempt": len(calls)},
            timestamp=f"2026-06-01T00:00:0{len(calls)}+00:00",
            error="still blocked",
        )

    registry = SkillRegistry(
        skills={
            "failing_skill": Skill(
                name="failing_skill",
                description="Always fails.",
                handler=failing_skill,
                max_attempts=2,
            )
        }
    )
    plan = Plan(
        intent="direct_skill_invocation",
        steps=[PlanStep("failing_skill", {"text": "二楼"})],
    )

    result = PlanExecutor(registry).execute(plan)

    assert result.status == "failed"
    assert len(calls) == 2
    assert result.steps[0].status == "failed"
    assert result.steps[0].attempt_count == 2
    assert result.steps[0].failure_category == "recoverable_exhausted"
    assert result.steps[0].operator_action == "escalate"
    assert [attempt.status for attempt in result.steps[0].attempts] == ["failed", "failed"]


def test_plugin_projected_skills_declare_in_process_runtime_metadata():
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = _navigation_registry(robot)

    assert {skill.runtime for skill in registry.skills.values()} == {"in_process"}
    assert all(skill.dry_run_only for skill in registry.skills.values())


def test_navigation_plugin_declares_input_schema_metadata():
    registry = _navigation_registry(DryRunRobotAdapter(robot_id="robot-1"))
    metadata = {skill["name"]: skill for skill in registry.list_metadata()}

    assert metadata["navigate_to_point"]["input_schema"]["required"] == ["x", "y"]
    assert metadata["navigate_to_point"]["input_schema"]["properties"]["x"]["type"] == "number"
    assert metadata["navigate_to_point"]["metadata"]["plugin_id"] == "fireclaw.navigation.move-base"


def test_navigation_plugin_declares_motion_safety_metadata():
    registry = _navigation_registry(DryRunRobotAdapter(robot_id="robot-1"))

    metadata = registry.list_metadata()[0]

    assert metadata["domain"] == "navigation"
    assert metadata["physical_plugin"]["safety_class"] == "motion"
    assert metadata["physical_plugin"]["plugin_id"] == "fireclaw.navigation.move-base"


def test_skill_registry_lists_execution_and_safety_metadata():
    registry = SkillRegistry(
        skills={
            "policy": Skill(
                name="policy",
                description="Retryable policy.",
                handler=lambda inputs: None,
                runtime="subprocess",
                dry_run_only=True,
                max_attempts=2,
                idempotent=True,
                required_sensors=["rgb_camera"],
                failure_categories=["timeout"],
                allow_real_robot=False,
                timeout_seconds=3.0,
                risk_level="high",
            )
        }
    )

    metadata = registry.list_metadata()
    assert len(metadata) == 1
    m = metadata[0]
    assert m["name"] == "policy"
    assert m["risk_level"] == "high"
    assert m["required_sensors"] == ["rgb_camera"]
    assert m["failure_categories"] == ["timeout"]
    assert m["output_schema"] == {"type": "object", "additionalProperties": True}
    assert m["domain"] == "navigation"
    assert m["preconditions"] == []
    assert m["degraded_mode_policy"] is None


def test_skill_registry_registers_external_skill():
    registry = SkillRegistry(skills={})
    skill = Skill(
        name="external_skill",
        description="External test skill.",
        handler=lambda inputs: None,
    )

    registry.register(skill)

    assert registry.get("external_skill") is skill


def test_skill_registry_rejects_duplicate_names_by_default():
    registry = SkillRegistry(skills={})
    original = Skill(
        name="duplicate_skill",
        description="Original skill.",
        handler=lambda inputs: None,
    )
    duplicate = Skill(
        name="duplicate_skill",
        description="Duplicate skill.",
        handler=lambda inputs: None,
    )
    registry.register(original)

    try:
        registry.register(duplicate)
    except ValueError as exc:
        assert "duplicate_skill" in str(exc)
    else:
        raise AssertionError("Expected duplicate skill registration to fail.")

    assert registry.get("duplicate_skill") is original


def test_skill_registry_can_replace_duplicate_when_requested():
    registry = SkillRegistry(skills={})
    original = Skill(
        name="replaceable_skill",
        description="Original skill.",
        handler=lambda inputs: None,
    )
    replacement = Skill(
        name="replaceable_skill",
        description="Replacement skill.",
        handler=lambda inputs: None,
    )
    registry.register(original)

    registry.register(replacement, replace=True)

    assert registry.get("replaceable_skill") is replacement


def test_skill_registry_extends_multiple_skills():
    registry = SkillRegistry(skills={})
    first = Skill(name="first", description="First skill.", handler=lambda inputs: None)
    second = Skill(name="second", description="Second skill.", handler=lambda inputs: None)

    registry.extend([first, second])

    assert registry.names() == {"first", "second"}


class FakeRobotAdapter:
    robot_id = "fake-robot"
    mode = "fake"
    dry_run = True

    def __init__(self):
        self.calls = []

    def navigate_to_point(self, **_kwargs):
        raise AssertionError("Adapter domain methods must never be reflected.")


def test_default_skill_registry_never_reflects_adapter_domain_methods():
    robot = FakeRobotAdapter()
    registry = create_default_skill_registry(robot)

    assert registry.get("navigate_to_point") is None
    assert robot.calls == []
