"""Current-architecture FireClaw orchestration for live Gazebo acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Callable
from urllib import request

from fireclaw_core.agent.robot_deliberation import (
    RobotAgentDecision,
    RobotAgentDeliberationLimits,
    RobotAgentDeliberationRuntime,
)
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.evaluation.provenance import extension_inventory_snapshot
from fireclaw_core.mission.mission_planner import MissionPlanner
from fireclaw_core.mission.mission_run import MissionRunManager
from fireclaw_core.mission.mission_runtime import (
    MissionRuntimePaths,
    build_mission_agent_from_paths,
)
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry

from .artifacts import ArtifactBundle
from .scenario import AcceptanceScenario


LOOPBACK_OPERATOR_ID = "local-loopback-operator"
ROBOT_TERMINAL_STATUSES = {
    "blocked",
    "cancelled",
    "completed",
    "escalated",
    "failed",
    "lost",
    "timed_out",
}


class DiagnosticsFirstStallPolicy:
    """Deterministic integration policy for the two live stall lanes.

    This policy is deliberately test-owned: it proves the bounded Robot Agent
    execution path independently from LLM planning quality.  Every decision is
    derived from the task envelope plus observations returned by the host; it
    cannot change the assigned target or introduce arbitrary ROS parameters.
    """

    def __init__(self, scenario: AcceptanceScenario) -> None:
        if scenario.scenario_type not in {
            "stall_recover",
            "stall_escalate",
        }:
            raise ValueError("stall policy requires a stall scenario")
        if scenario.stall is None:
            raise ValueError("stall policy requires stall expectations")
        self.scenario = scenario

    def decide(self, request: Any) -> RobotAgentDecision:
        scenario = self.scenario
        stall = scenario.stall
        assert stall is not None
        observations = tuple(request.observations)
        navigation = tuple(
            item
            for item in observations
            if item.operation == "execute_skill"
            and item.tool_name == scenario.required_tool
        )
        diagnostics = tuple(
            item
            for item in observations
            if item.operation == "execute_agent_tool"
            and item.tool_name == "navigation_diagnostics"
        )
        recoveries = tuple(
            item
            for item in observations
            if item.operation == "execute_agent_tool"
            and item.tool_name == "move_base_set_parameters"
        )

        if not navigation:
            return RobotAgentDecision(
                operation="execute_skill",
                message="Execute the assigned navigation goal once.",
                tool_name=scenario.required_tool,
                inputs=_navigation_inputs(request.envelope.target),
            )

        first = navigation[0]
        if first.status != "timed_out":
            return RobotAgentDecision(
                operation="escalate",
                message=(
                    "The trusted stall precondition was not observed; "
                    "automatic recovery is unsafe."
                ),
                reason_code="stall_precondition_not_observed",
                evidence_ids=_observation_evidence_ids(first),
            )

        if not diagnostics:
            return RobotAgentDecision(
                operation="execute_agent_tool",
                message=(
                    "Collect one bounded Robot-local navigation diagnostic "
                    "snapshot before any recovery decision."
                ),
                tool_name="navigation_diagnostics",
                tool_effect="read",
                inputs={
                    "action_name": scenario.action_name,
                    "global_frame": "map",
                    "robot_frame": "base_link",
                    "scan_topic": "/scan",
                    "odom_topic": "/odom",
                    "cmd_vel_topic": "/cmd_vel",
                    "timeout_seconds": stall.diagnostics_timeout_seconds,
                },
            )

        diagnostic = diagnostics[-1]
        diagnostic_output = _agent_tool_output(diagnostic)
        evidence_ids = _observation_evidence_ids(first, diagnostic)
        finding_codes = {
            str(item.get("code"))
            for item in diagnostic_output.get("findings", [])
            if isinstance(item, dict) and item.get("code")
        }
        required_codes = set(stall.required_diagnostic_finding_codes)
        diagnostic_valid = (
            diagnostic.status == "executed"
            and diagnostic_output.get("status") == "ok"
            and required_codes.issubset(finding_codes)
            and bool(evidence_ids)
        )
        if not diagnostic_valid:
            return RobotAgentDecision(
                operation="escalate",
                message=(
                    "Bounded navigation diagnostics were unavailable or did "
                    "not support a safe recovery decision."
                ),
                reason_code="navigation_diagnostics_inconclusive",
                evidence_ids=evidence_ids,
            )

        if scenario.scenario_type == "stall_escalate":
            return RobotAgentDecision(
                operation="escalate",
                message=(
                    "Navigation remained stalled; no autonomous recovery is "
                    "authorized for this scenario."
                ),
                reason_code=(
                    stall.escalation_reason_code
                    or "persistent_navigation_stall"
                ),
                evidence_ids=evidence_ids,
            )

        if not recoveries:
            assert stall.recovery_parameters is not None
            return RobotAgentDecision(
                operation="execute_agent_tool",
                message=(
                    "Apply the single bounded DWA recovery admitted by the "
                    "scenario contract."
                ),
                tool_name="move_base_set_parameters",
                tool_effect="bounded_mutation",
                inputs={
                    "scope": "dwa",
                    "parameters": dict(stall.recovery_parameters),
                },
                evidence_ids=evidence_ids,
            )

        recovery = recoveries[-1]
        recovery_output = _agent_tool_output(recovery)
        evidence_ids = _observation_evidence_ids(
            first,
            diagnostic,
            recovery,
        )
        if (
            recovery.status != "executed"
            or recovery_output.get("status") != "succeeded"
        ):
            return RobotAgentDecision(
                operation="escalate",
                message="The bounded navigation recovery did not succeed.",
                reason_code="navigation_recovery_failed",
                evidence_ids=evidence_ids,
            )

        if len(navigation) == 1:
            return RobotAgentDecision(
                operation="execute_skill",
                message="Retry the exact assigned navigation goal once.",
                tool_name=scenario.required_tool,
                inputs=_navigation_inputs(request.envelope.target),
                evidence_ids=evidence_ids,
            )

        retry = navigation[1]
        evidence_ids = _observation_evidence_ids(
            first,
            diagnostic,
            recovery,
            retry,
        )
        if retry.status == "succeeded":
            return RobotAgentDecision(
                operation="complete",
                message=(
                    "Navigation succeeded after one diagnostics-backed "
                    "bounded recovery."
                ),
                evidence_ids=evidence_ids,
            )
        return RobotAgentDecision(
            operation="escalate",
            message=(
                "Navigation remained stalled after the single authorized "
                "recovery attempt."
            ),
            reason_code="navigation_recovery_exhausted",
            evidence_ids=evidence_ids,
        )


@dataclass(frozen=True)
class PluginInspection:
    owner_plugin_id: str
    backend_class: str
    backend_module: str
    action_name: str
    contribution_id: str
    timeout_seconds: float | None
    cancellation_ack_timeout_seconds: float
    extension_report: dict[str, Any]
    reproducibility_plugin_inventory: dict[str, Any]
    tool_inventory: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_plugin_id": self.owner_plugin_id,
            "backend_class": self.backend_class,
            "backend_module": self.backend_module,
            "action_name": self.action_name,
            "contribution_id": self.contribution_id,
            "timeout_seconds": self.timeout_seconds,
            "cancellation_ack_timeout_seconds": (
                self.cancellation_ack_timeout_seconds
            ),
            "extension_report": self.extension_report,
            "reproducibility_plugin_inventory": (
                self.reproducibility_plugin_inventory
            ),
            "tool_inventory": self.tool_inventory,
        }


def create_robot_gateway(
    scenario: AcceptanceScenario,
    bundle: ArtifactBundle,
    *,
    repo_root: Path,
) -> FireClawGateway:
    profile_path = bundle.run_dir / "robot-gateway-profile.toml"
    write_robot_profile(
        profile_path,
        scenario,
        base_url="http://127.0.0.1:0",
        data_dir=bundle.run_dir / "fireclaw" / "robot",
    )
    plugin_config: dict[str, Any] = {"enabled": True}
    if scenario.timeout is not None:
        plugin_config.update(
            {
                "navigate_timeout_seconds": (
                    scenario.timeout.execution_timeout_seconds
                ),
                "cancellation_ack_timeout_seconds": (
                    scenario.timeout.acknowledgement_timeout_seconds
                ),
            }
        )
    if scenario.stall is not None:
        plugin_config.update(
            {
                "navigate_timeout_seconds": (
                    scenario.stall.execution_timeout_seconds
                ),
            }
        )
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            dry_run=False,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
            extension_paths=(str(repo_root / "extensions"),),
            plugin_configs={scenario.plugin_owner: plugin_config},
            robot_profile_path=str(profile_path),
            embodied_memory_path=str(
                bundle.run_dir / "fireclaw" / "robot-embodied-memory.jsonl"
            ),
            embodied_runtime_mode="simulation",
        )
    )
    if scenario.stall is not None:
        recovery_enabled = scenario.scenario_type == "stall_recover"
        gateway.robot_agent_runtime = RobotAgentDeliberationRuntime(
            policy=DiagnosticsFirstStallPolicy(scenario),
            limits=RobotAgentDeliberationLimits(
                max_iterations=6 if recovery_enabled else 4,
                timeout_seconds=scenario.stall.terminal_timeout_seconds,
                max_skill_executions=2 if recovery_enabled else 1,
                max_context_queries=0,
                max_agent_tool_executions=2 if recovery_enabled else 1,
            ),
            checkpoint_store=gateway.agent_loop_checkpoints,
        )
    return gateway


def inspect_navigation_plugin(
    gateway: FireClawGateway,
    scenario: AcceptanceScenario,
) -> PluginInspection:
    agent = gateway._create_agent(
        task_id="acceptance-plugin-inspection",
        session_id="acceptance-plugin-inspection",
    )
    try:
        contribution = agent.plugin_host.get(
            "physical_capability",
            scenario.required_tool,
        )
        if contribution is None:
            raise AssertionError(
                f"physical Tool is not registered: {scenario.required_tool}"
            )
        handler = contribution.value.action_handler
        normalized_nonlocals = inspect.getclosurevars(handler).nonlocals
        public_spec = normalized_nonlocals.get("value")
        provider_handler = getattr(public_spec, "handler", None)
        if not callable(provider_handler):
            raise AssertionError("physical Tool handler is not inspectable")
        provider_nonlocals = inspect.getclosurevars(provider_handler).nonlocals
        backend = provider_nonlocals.get("backend")
        if backend is None:
            raise AssertionError(
                "Navigation Plugin handler does not close over its backend"
            )
        report = agent.extension_report
        if report is None:
            raise AssertionError("Navigation Plugin extension report is missing")
        plugin_inventory, tool_inventory = extension_inventory_snapshot(
            agent,
            repo_root=Path(__file__).resolve().parents[4],
            deployment_profile=gateway.config.deployment_profile,
        )
        return PluginInspection(
            owner_plugin_id=contribution.owner_plugin_id,
            backend_class=type(backend).__name__,
            backend_module=type(backend).__module__,
            action_name=str(getattr(backend, "action_name", "")),
            contribution_id=contribution.contribution_id,
            timeout_seconds=(
                float(contribution.value.timeout_seconds)
                if contribution.value.timeout_seconds is not None
                else None
            ),
            cancellation_ack_timeout_seconds=float(
                contribution.value.cancellation_ack_timeout_seconds
            ),
            extension_report=report.to_dict(),
            reproducibility_plugin_inventory=plugin_inventory,
            tool_inventory=tool_inventory,
        )
    finally:
        agent.plugin_host.dispose()


def install_adapter_navigation_trap(gateway: FireClawGateway) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def trap(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        raise AssertionError(
            "core RobotAdapter navigation fallback was invoked"
        )

    if hasattr(gateway.robot, "navigate_to_point"):
        raise AssertionError(
            "Ros1RobotAdapter unexpectedly defines navigate_to_point before trap"
        )
    setattr(gateway.robot, "navigate_to_point", trap)
    return calls


def build_mission_agent(
    scenario: AcceptanceScenario,
    bundle: ArtifactBundle,
    *,
    robot_gateway_base_url: str,
) -> Any:
    profile_path = bundle.run_dir / "mission-robot-profile.toml"
    write_robot_profile(
        profile_path,
        scenario,
        base_url=robot_gateway_base_url,
        data_dir=bundle.run_dir / "fireclaw" / "mission-robot-profile-data",
    )
    fireclaw_dir = bundle.run_dir / "fireclaw"
    subagent_registry_path = fireclaw_dir / "subagents.jsonl"
    paths = MissionRuntimePaths(
        robot_registry=fireclaw_dir / "unused-robots.json",
        mission_registry=fireclaw_dir / "missions.jsonl",
        robot_profiles=(profile_path,),
        mission_memory=fireclaw_dir / "mission-memory.jsonl",
        task_registry=fireclaw_dir / "mission-tasks.jsonl",
        subagent_registry=subagent_registry_path,
        session_lineage=fireclaw_dir / "session-lineage.jsonl",
        task_flow=fireclaw_dir / "task-flow.jsonl",
        mission_planning_audit=fireclaw_dir / "mission-planning-audit.jsonl",
        agent_loop_checkpoints=fireclaw_dir / "mission-agent-loops.jsonl",
    )
    client = RobotSubagentClient(
        timeout_seconds=30.0,
        registry=JsonlSubagentRegistry(subagent_registry_path),
        allow_plaintext_loopback=True,
    )
    return build_mission_agent_from_paths(
        paths,
        # The loopback Robot Gateway authenticates requests as this principal.
        # Matching the delegated identity is part of the capability policy;
        # caller-provided identities are never trusted across the HTTP boundary.
        operator_id=LOOPBACK_OPERATOR_ID,
        role="operator",
        planner=MissionPlanner(),
        subagent_client=client,
        resume_dispatches=False,
        source="gazebo_acceptance",
    )


def run_mission_until_authorization(
    mission_agent: Any,
    scenario: AcceptanceScenario,
    *,
    event_sink: Callable[[str, str, dict[str, Any]], None],
) -> tuple[MissionRunManager, dict[str, Any]]:
    manager = MissionRunManager(mission_agent, event_sink=event_sink)
    mission_id = (
        f"gazebo-acceptance-{scenario.scenario_id}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    accepted = manager.submit(
        scenario.command,
        mission_id=mission_id,
        operator={
            "operator_id": LOOPBACK_OPERATOR_ID,
            "role": "operator",
            "source": "trusted_acceptance_harness",
        },
        use_scheduler=True,
    )
    if accepted.get("status") != "accepted":
        raise AssertionError(f"Mission Run was not accepted: {accepted}")
    deadline = monotonic() + scenario.mission_seconds
    while monotonic() < deadline:
        current = manager.get(mission_id)
        trace = mission_agent.mission_trace(mission_id)
        waiting = next(
            (
                subtask
                for subtask in trace.get("subtasks", [])
                if isinstance(subtask, dict)
                and subtask.get("status")
                == scenario.mission_authorization_status
            ),
            None,
        )
        if waiting is not None:
            task_id = waiting.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise AssertionError(
                    f"Waiting Mission subtask has no Robot task id: {waiting}"
                )
            return manager, {
                **current,
                "mission_trace": trace,
                "robot_task_id": task_id,
                "authorization_status": waiting.get("status"),
            }
        if current.get("terminal") is True:
            raise AssertionError(
                "Mission Run terminated before reaching the authorization "
                f"suspension boundary: run={current}, trace={trace}"
            )
        sleep(0.2)
    manager.cancel(
        mission_id,
        operator={
            "operator_id": LOOPBACK_OPERATOR_ID,
            "role": "operator",
        },
    )
    raise TimeoutError(
        "Mission Run did not reach awaiting_confirmation within "
        f"{scenario.mission_seconds}s: "
        f"{manager.get(mission_id)}"
    )


def confirm_pending_authorization(
    robot_gateway_base_url: str,
    *,
    session_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Confirm through the Robot Gateway's authenticated control surface."""

    body = json.dumps({"session_id": session_id}).encode("utf-8")
    http_request = request.Request(
        f"{robot_gateway_base_url.rstrip('/')}/confirm",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(http_request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("Robot Gateway /confirm response is not an object")
    if payload.get("status") != "accepted":
        raise AssertionError(f"Robot Gateway did not accept confirmation: {payload}")
    return payload


def wait_for_robot_task(
    client: RobotSubagentClient,
    entry: Any,
    task_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    last_trace: dict[str, Any] = {}
    while monotonic() < deadline:
        last_trace = client.get_task_trace(entry, task_id)
        if str(last_trace.get("status")) in ROBOT_TERMINAL_STATUSES:
            return last_trace
        sleep(0.2)
    client.cancel_task(entry, task_id)
    raise TimeoutError(
        f"Robot task did not terminate within {timeout_seconds}s: {last_trace}"
    )


def wait_for_mission_run(
    manager: MissionRunManager,
    mission_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        current = manager.get(mission_id)
        if current.get("terminal") is True:
            return current
        sleep(0.2)
    manager.cancel(
        mission_id,
        operator={
            "operator_id": LOOPBACK_OPERATOR_ID,
            "role": "operator",
        },
    )
    raise TimeoutError(
        f"Mission Run did not terminate within {timeout_seconds}s: "
        f"{manager.get(mission_id)}"
    )


def wait_for_robot_event(
    gateway: FireClawGateway,
    task_id: str,
    event_type: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        matches = [
            event
            for event in gateway.events.events_for_task(task_id)
            if event.get("type") == event_type
        ]
        if matches:
            return matches[-1]
        sleep(0.02)
    raise TimeoutError(
        f"Robot task {task_id} did not emit {event_type} within "
        f"{timeout_seconds}s"
    )


def robot_task_id(run: dict[str, Any]) -> str:
    direct = run.get("robot_task_id")
    if isinstance(direct, str) and direct:
        return direct
    result = run.get("result")
    if isinstance(result, dict):
        for subtask in result.get("subtask_results", []):
            if isinstance(subtask, dict):
                task_id = subtask.get("task_id")
                if isinstance(task_id, str) and task_id:
                    return task_id
    raise AssertionError(f"Mission Run has no Robot task id: {run}")


def write_robot_profile(
    path: Path,
    scenario: AcceptanceScenario,
    *,
    base_url: str,
    data_dir: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    ros_config = scenario.assets.ros1_config
    content = f"""
[robot]
id = {_toml_string(scenario.robot_id)}
base_url = {_toml_string(base_url)}
adapter = "ros1"
ros1_config = {_toml_string(str(ros_config))}
data_dir = {_toml_string(str(data_dir))}
capabilities = ["navigation", {_toml_string(scenario.capability)}]
enabled_skills = [{_toml_string(scenario.required_tool)}]
llm_exposed_skills = [{_toml_string(scenario.required_tool)}]
primitive_skills = [{_toml_string(scenario.required_tool)}]

[capability_skill_chains]
navigation = [{_toml_string(scenario.required_tool)}]
{scenario.capability} = [{_toml_string(scenario.required_tool)}]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 2.0

[[robot.sensor_discovery.rules]]
topic_pattern = "/scan"
message_type = "sensor_msgs/LaserScan"
sensor = "lidar"
confidence = 0.99
confirmed = true
""".strip()
    path.write_text(content + "\n", encoding="utf-8")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _navigation_inputs(target: dict[str, Any]) -> dict[str, Any]:
    pose = target.get("pose")
    if not isinstance(pose, dict):
        raise ValueError("stall task target must contain a pose object")
    return {
        "x": float(pose["x"]),
        "y": float(pose["y"]),
        "yaw": float(pose.get("yaw", 0.0)),
        "frame_id": str(target.get("frame_id") or "map"),
    }


def _agent_tool_output(observation: Any) -> dict[str, Any]:
    wrapper = observation.output
    if not isinstance(wrapper, dict):
        return {}
    output = wrapper.get("output")
    return dict(output) if isinstance(output, dict) else {}


def _observation_evidence_ids(*observations: Any) -> tuple[str, ...]:
    found: list[str] = []

    def visit(value: Any) -> None:
        if len(found) >= 64:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_id" and isinstance(item, str) and item:
                    found.append(item)
                elif key == "evidence_ids" and isinstance(item, list):
                    found.extend(
                        str(candidate)
                        for candidate in item
                        if isinstance(candidate, str) and candidate
                    )
                else:
                    visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    for observation in observations:
        visit(getattr(observation, "output", None))
    return tuple(dict.fromkeys(found))


__all__ = [
    "DiagnosticsFirstStallPolicy",
    "PluginInspection",
    "build_mission_agent",
    "confirm_pending_authorization",
    "create_robot_gateway",
    "inspect_navigation_plugin",
    "install_adapter_navigation_trap",
    "robot_task_id",
    "run_mission_until_authorization",
    "wait_for_mission_run",
    "wait_for_robot_event",
    "wait_for_robot_task",
    "write_robot_profile",
]
