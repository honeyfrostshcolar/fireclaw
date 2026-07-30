from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from fireclaw_core.agent.robot import (
    AdapterCapabilities,
    EnvironmentState,
    RobotActionResult,
    RobotState,
)
from fireclaw_core.agent.robot_agent import (
    RobotAgentPolicy,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)
from fireclaw_core.execution.action_runtime import (
    RobotActionRuntime,
    RobotAdapterActionBackend,
)
from fireclaw_core.execution.executor import PlanExecutor
from fireclaw_core.execution.skill_plugin import (
    TaskInputBinding,
    define_physical_skill_plugin,
)
from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.infra.operator_projection import OperatorEventProjector
from fireclaw_core.infra.runtime_state import (
    SqliteAuthoritativeRuntimeStore,
    SqliteResourceLeaseManager,
)
from fireclaw_core.safety.safety import SafetyGate
from fireclaw_core.task.task_contract import (
    StructuredRobotTask,
    planning_result_from_structured_task,
)


@dataclass
class BeaconRobotAdapter:
    robot_id: str = "robot-beacon"
    mode: str = "dry_run"
    dry_run: bool = True
    deployed: list[dict[str, str]] = field(default_factory=list)

    def deploy_beacon(self, zone: str, color: str) -> RobotActionResult:
        self.deployed.append({"zone": zone, "color": color})
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id=self.robot_id,
            mode=self.mode,
            action="deploy_beacon",
            dry_run=True,
            data={"zone": zone, "color": color},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supported_actions={"deploy_beacon"},
            supported_modes={"dry_run"},
            supports_dry_run=True,
            supports_real_execution=False,
            supports_feedback=False,
            supports_cancellation=False,
            max_concurrent_actions=1,
            required_sensors=[],
            is_simulator=False,
        )

    def get_robot_state(self) -> RobotState:
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=True,
            online=True,
            battery_percent=100.0,
            current_floor=1,
            available_sensors=[],
            supports_real_execution=False,
        )

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(reachable_floors=[1])


def _beacon_plugin():
    return define_physical_skill_plugin(
        plugin_id="test.safety-beacon",
        name="place_safety_beacon",
        label="Place safety beacon",
        description="Place a visible safety beacon in the contracted zone.",
        parameters={
            "type": "object",
            "properties": {
                "zone": {"type": "string", "minLength": 1},
                "color": {
                    "type": "string",
                    "enum": ["red", "yellow"],
                    "default": "yellow",
                },
            },
            "required": ["zone", "color"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "zone": {"type": "string"},
                "color": {"type": "string"},
            },
            "required": ["zone", "color"],
        },
        action="deploy_beacon",
        action_input_builder=lambda _robot, inputs: {
            "zone": str(inputs["zone"]),
            "color": str(inputs["color"]),
        },
        domain="safety",
        safety_class="deployment",
        task_input_bindings=(
            TaskInputBinding(
                "zone",
                (("zone",),),
                required=True,
                coercion="string",
            ),
            TaskInputBinding(
                "color",
                (("beacon_color",),),
                default="yellow",
                coercion="string",
            ),
        ),
        resource_locks=("robot_payload",),
        success_evidence=("beacon_deployed",),
        operator_started_message=lambda inputs: (
            f"正在向 {inputs['zone']} 放置{inputs['color']}色安全标记。"
        ),
    )


def _runtime():
    robot = BeaconRobotAdapter()
    events: list[tuple[str, dict]] = []
    action_runtime = RobotActionRuntime(
        backend=RobotAdapterActionBackend(robot),
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )
    registry = SkillRegistry(skills={})
    registry.register_plugin(
        _beacon_plugin(),
        robot=robot,
        action_runtime=action_runtime,
    )
    return robot, registry, events


def test_new_physical_skill_registers_and_executes_without_agent_core_branch():
    robot, registry, events = _runtime()
    task = StructuredRobotTask(
        task_id="task-beacon",
        task_type="mark_safe_zone",
        target={"zone": "corridor-a"},
        required_skills=["place_safety_beacon"],
    )

    planning = planning_result_from_structured_task(
        task,
        skill_catalog=registry,
    )
    safety = SafetyGate().evaluate(
        planning,
        registry,
        dry_run=True,
        available_sensors=set(),
    )
    execution = PlanExecutor(
        registry,
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    ).execute(planning.plan)

    assert planning.plan.steps[0].inputs == {
        "zone": "corridor-a",
        "color": "yellow",
    }
    assert safety.status == "allow"
    assert execution.status == "succeeded"
    assert robot.deployed == [{"zone": "corridor-a", "color": "yellow"}]
    assert "place_safety_beacon" not in {
        payload.get("action_type")
        for event_type, payload in events
        if event_type == "action.requested"
    }
    assert {
        payload.get("action_type")
        for event_type, payload in events
        if event_type == "action.requested"
    } == {"deploy_beacon"}


def test_plugin_contract_generically_blocks_target_mutation():
    _robot, registry, _events = _runtime()
    envelope = RobotAgentTaskEnvelope(
        task_id="task-beacon",
        mission_id="mission-1",
        robot_id="robot-beacon",
        command="place beacon",
        task_type="mark_safe_zone",
        target={"zone": "corridor-a"},
        allowed_skills=["place_safety_beacon"],
        required_skills=["place_safety_beacon"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )
    plan = RobotLocalPlan(
        intent="mark_safe_zone",
        steps=[
            RobotLocalPlanStep(
                "place_safety_beacon",
                {"zone": "corridor-b", "color": "yellow"},
            )
        ],
    )

    decision = RobotAgentPolicy(skill_catalog=registry).validate(
        envelope,
        plan,
    )

    assert decision.status == "reject"
    assert any("corridor-b" in reason for reason in decision.reasons)


def test_plugin_schema_is_host_enforced_before_adapter_execution():
    robot, registry, _events = _runtime()
    task = StructuredRobotTask(
        task_id="task-beacon",
        task_type="mark_safe_zone",
        target={"zone": "corridor-a"},
        required_skills=["place_safety_beacon"],
    )
    planning = planning_result_from_structured_task(
        task,
        skill_catalog=registry,
    )
    planning.plan.steps[0].inputs["color"] = "blue"

    safety = SafetyGate().evaluate(
        planning,
        registry,
        dry_run=True,
        available_sensors=set(),
    )
    execution = PlanExecutor(registry).execute(planning.plan)

    assert safety.status == "block"
    assert "inputs.color" in safety.reasons[0]
    assert execution.status == "failed"
    assert robot.deployed == []


def test_executor_enforces_plugin_resource_locks_across_operations(tmp_path):
    robot, registry, events = _runtime()
    task = StructuredRobotTask(
        task_id="task-beacon",
        task_type="mark_safe_zone",
        target={"zone": "corridor-a"},
        required_skills=["place_safety_beacon"],
    )
    planning = planning_result_from_structured_task(
        task,
        skill_catalog=registry,
    )
    leases = SqliteResourceLeaseManager(
        SqliteAuthoritativeRuntimeStore(tmp_path / "runtime.sqlite3")
    )
    leases.acquire(
        robot_id=robot.robot_id,
        resource_names=("robot_payload",),
        owner_id="task-other",
        operation_id="task-other:step:1",
        ttl_seconds=30,
    )
    executor = PlanExecutor(
        registry,
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
        robot_id=robot.robot_id,
        subtask_id=task.task_id,
        resource_lease_manager=leases,
    )

    blocked = executor.execute(planning.plan)

    assert blocked.status == "failed"
    assert blocked.steps[0].failure_category == "resource_lease_conflict"
    assert robot.deployed == []

    leases.release(
        robot_id=robot.robot_id,
        owner_id="task-other",
        operation_id="task-other:step:1",
    )
    succeeded = executor.execute(planning.plan)

    assert succeeded.status == "succeeded"
    assert robot.deployed == [{"zone": "corridor-a", "color": "yellow"}]
    assert leases.active(robot_id=robot.robot_id) == []
    assert any(event_type == "resource.acquired" for event_type, _ in events)
    assert any(event_type == "resource.released" for event_type, _ in events)


def test_plugin_operator_projection_requires_no_skill_name_branch():
    _robot, registry, events = _runtime()
    planning = planning_result_from_structured_task(
        StructuredRobotTask(
            task_id="task-beacon",
            task_type="mark_safe_zone",
            target={"zone": "corridor-a"},
            required_skills=["place_safety_beacon"],
        ),
        skill_catalog=registry,
    )
    PlanExecutor(
        registry,
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    ).execute(planning.plan)
    started = next(
        payload for event_type, payload in events if event_type == "skill.started"
    )

    message = OperatorEventProjector(skill_catalog=registry).project(
        {"type": "skill.started", "payload": started}
    )

    assert message == "正在向 corridor-a 放置yellow色安全标记。"


def test_freeform_metadata_cannot_override_trusted_plugin_contract():
    plugin = _beacon_plugin()
    plugin.metadata.update({
        "action_binding": "unsafe_action",
        "safety_class": "unrestricted",
        "risk_level": "low",
    })

    metadata = plugin.to_metadata()

    assert metadata["action_binding"] == "deploy_beacon"
    assert metadata["safety_class"] == "deployment"
    assert metadata["risk_level"] == plugin.risk_level
