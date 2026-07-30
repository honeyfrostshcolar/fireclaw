from __future__ import annotations

from dataclasses import dataclass, field
import logging

from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
)
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryProducer,
)
from fireclaw_core.planner.planner import PlanningResult
from fireclaw_core.agent.robot import EnvironmentState, RobotState
from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.safety.sensor_policy import evaluate_sensor_policy


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyDecision:
    status: str
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)


class SafetyGate:
    def __init__(
        self,
        *,
        memory_producer: EmbodiedMemoryProducer | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        if memory_producer is not None:
            if memory_producer.producer_type != "safety_gate":
                raise ValueError("SafetyGate requires a safety_gate embodied-memory producer")
            if runtime_mode not in MEMORY_RUNTIME_MODES:
                raise ValueError(
                    "SafetyGate requires runtime_mode to be one of: "
                    f"{sorted(MEMORY_RUNTIME_MODES)}"
                )
        self._memory_producer = memory_producer
        self._runtime_mode = runtime_mode

    def evaluate(
        self,
        planning_result: PlanningResult,
        registry: SkillRegistry,
        *,
        dry_run: bool,
        available_sensors: set[str] | None = None,
        execution_authorization: VerifiedExecutionAuthorization | None = None,
        robot_state: RobotState | None = None,
        environment_state: EnvironmentState | None = None,
        mission_id: str | None = None,
        subtask_id: str | None = None,
        evidence_event_ids: tuple[str, ...] = (),
    ) -> SafetyDecision:
        decision, _ = self.evaluate_with_memory_event(
            planning_result,
            registry,
            dry_run=dry_run,
            available_sensors=available_sensors,
            execution_authorization=execution_authorization,
            robot_state=robot_state,
            environment_state=environment_state,
            mission_id=mission_id,
            subtask_id=subtask_id,
            evidence_event_ids=evidence_event_ids,
        )
        return decision

    def evaluate_with_memory_event(
        self,
        planning_result: PlanningResult,
        registry: SkillRegistry,
        *,
        dry_run: bool,
        available_sensors: set[str] | None = None,
        execution_authorization: VerifiedExecutionAuthorization | None = None,
        robot_state: RobotState | None = None,
        environment_state: EnvironmentState | None = None,
        mission_id: str | None = None,
        subtask_id: str | None = None,
        evidence_event_ids: tuple[str, ...] = (),
    ) -> tuple[SafetyDecision, str | None]:
        decision = self._evaluate(
            planning_result,
            registry,
            dry_run=dry_run,
            available_sensors=available_sensors,
            execution_authorization=execution_authorization,
            robot_state=robot_state,
            environment_state=environment_state,
        )
        memory_event_id = self._record_decision(
            decision,
            planning_result=planning_result,
            dry_run=dry_run,
            execution_authorization=execution_authorization,
            robot_state=robot_state,
            environment_state=environment_state,
            mission_id=mission_id,
            subtask_id=subtask_id,
            evidence_event_ids=evidence_event_ids,
        )
        return decision, memory_event_id

    def _evaluate(
        self,
        planning_result: PlanningResult,
        registry: SkillRegistry,
        *,
        dry_run: bool,
        available_sensors: set[str] | None = None,
        execution_authorization: VerifiedExecutionAuthorization | None = None,
        robot_state: RobotState | None = None,
        environment_state: EnvironmentState | None = None,
    ) -> SafetyDecision:
        if planning_result.status == "clarify":
            return SafetyDecision(status="clarify", reasons=[planning_result.message])

        if planning_result.plan is None:
            return SafetyDecision(status="block", reasons=["Planner did not produce an executable plan."])

        if (
            planning_result.intent == "rescue_victim"
            and planning_result.target_pose is None
            and planning_result.target_floor is None
        ):
            return SafetyDecision(status="block", reasons=["Target point is missing."])

        state_blocks, state_warnings, state_confirmations = self._evaluate_state(
            planning_result,
            robot_state,
            environment_state,
            dry_run=dry_run,
        )
        if state_blocks:
            return SafetyDecision(status="block", reasons=state_blocks, warnings=state_warnings)

        missing = [
            f"Missing skill: {step.skill_name}"
            for step in planning_result.plan.steps
            if not registry.has(step.skill_name)
        ]
        if missing:
            return SafetyDecision(status="block", reasons=missing)

        # Infer sensors from robot_state when not explicitly provided
        sensors_unknown = False
        if available_sensors is not None:
            sensors = available_sensors
        elif robot_state is not None and robot_state.available_sensors is None:
            sensors = set()
            sensors_unknown = True
        elif robot_state is not None:
            sensors = set(robot_state.available_sensors)
        else:
            sensors = set()
        sensor_findings = _sensor_findings_by_name(robot_state)
        missing_sensors: list[str] = []
        sensor_confirmations: list[str] = []
        sensor_warnings: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is None:
                continue
            for sensor in skill.required_sensors:
                if sensors_unknown:
                    message = f"Robot {robot_state.robot_id if robot_state else 'unknown'} available sensors are unknown."
                    if dry_run:
                        sensor_warnings.append(message)
                    else:
                        sensor_confirmations.append(message)
                    break
                finding = sensor_findings.get(sensor)
                health_status = finding.get("health_status") if finding else ("healthy" if sensor in sensors else None)
                health_reason = finding.get("health_reason") if finding else None
                physical_plugin = skill.physical_plugin
                policy_decision = evaluate_sensor_policy(
                    skill_name=step.skill_name,
                    sensor=sensor,
                    health_status=str(health_status) if health_status is not None else None,
                    health_reason=str(health_reason) if health_reason is not None else None,
                    mode=robot_state.mode if robot_state is not None else "unknown",
                    dry_run=dry_run,
                    verified_sensors=set(sensors),
                    safety_class=(
                        physical_plugin.safety_class
                        if physical_plugin is not None
                        else str(skill.metadata.get("safety_class") or "")
                    ),
                    sensor_alternatives=(
                        physical_plugin.sensor_alternatives
                        if physical_plugin is not None
                        else {}
                    ),
                )
                if policy_decision.action == "block":
                    missing_sensors.append(policy_decision.reason or f"Skill {step.skill_name} requires unavailable sensor: {sensor}")
                elif policy_decision.action == "escalate":
                    sensor_confirmations.append(policy_decision.reason or f"Skill {step.skill_name} requires operator confirmation for sensor: {sensor}")
                elif policy_decision.action in {"warn", "degrade"} and policy_decision.reason:
                    sensor_warnings.append(policy_decision.reason)
        state_warnings.extend(sensor_warnings)
        state_confirmations.extend(sensor_confirmations)
        if missing_sensors:
            return SafetyDecision(status="block", reasons=missing_sensors, warnings=state_warnings)

        unsafe_retries: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is not None and skill.max_attempts > 1 and not skill.idempotent:
                unsafe_retries.append(
                    f"Skill {step.skill_name} has max_attempts > 1 but is not idempotent."
                )
        if unsafe_retries:
            return SafetyDecision(status="block", reasons=unsafe_retries)

        if dry_run:
            non_dry_run_skills = [
                f"Skill is not dry-run only: {step.skill_name}"
                for step in planning_result.plan.steps
                if registry.get(step.skill_name) is not None and not registry.get(step.skill_name).dry_run_only
            ]
            if non_dry_run_skills:
                return SafetyDecision(status="block", reasons=non_dry_run_skills)
        else:
            real_robot_blocks: list[str] = []
            for step in planning_result.plan.steps:
                skill = registry.get(step.skill_name)
                if skill is None:
                    continue
                if skill.dry_run_only or not skill.allow_real_robot:
                    real_robot_blocks.append(
                        f"Skill is not allowed for real robot execution: {step.skill_name}"
                    )
            if real_robot_blocks:
                return SafetyDecision(status="block", reasons=real_robot_blocks)

        invalid_inputs: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is None:
                continue
            invalid_inputs.extend(
                f"Skill {step.skill_name} input contract violation: {error}"
                for error in skill.validate_inputs(step.inputs)
            )
        if invalid_inputs:
            return SafetyDecision(status="block", reasons=invalid_inputs)

        confirmation_reasons: list[str] = list(state_confirmations)
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is None:
                continue
            if not dry_run:
                confirmation_reasons.append(
                    f"Real robot execution requires operator confirmation: {step.skill_name}"
                )
            if skill.risk_level in {"high", "critical"}:
                confirmation_reasons.append(
                    f"Skill {step.skill_name} has high risk level: {skill.risk_level}"
                )
        if confirmation_reasons and execution_authorization is None:
            return SafetyDecision(status="require_confirmation", reasons=confirmation_reasons, warnings=state_warnings)

        return SafetyDecision(status="allow", reasons=[], warnings=state_warnings)

    def _record_decision(
        self,
        decision: SafetyDecision,
        *,
        planning_result: PlanningResult,
        dry_run: bool,
        execution_authorization: VerifiedExecutionAuthorization | None,
        robot_state: RobotState | None,
        environment_state: EnvironmentState | None,
        mission_id: str | None,
        subtask_id: str | None,
        evidence_event_ids: tuple[str, ...],
    ) -> str | None:
        if (
            self._memory_producer is None
            or self._runtime_mode is None
            or mission_id is None
        ):
            return None
        payload = {
            "decision": decision.status,
            "reasons": list(decision.reasons),
            "warnings": list(decision.warnings),
            "dry_run": dry_run,
            "execution_authorization_id": (
                execution_authorization.authorization_id
                if execution_authorization is not None
                else None
            ),
            "execution_authorization_request_id": (
                execution_authorization.request_id
                if execution_authorization is not None
                else None
            ),
            "planning_status": planning_result.status,
            "intent": planning_result.intent,
            "target_floor": planning_result.target_floor,
            "target_pose": planning_result.target_pose,
            "robot_state": _robot_state_summary(robot_state),
            "environment_state": _environment_state_summary(environment_state),
        }
        try:
            decision_event = self._memory_producer.record_event(
                mission_id=mission_id,
                event_type="safety_decision",
                evidence_kind="runtime_evidence",
                payload=payload,
                runtime_mode=self._runtime_mode,
                source_type="safety_gate",
                robot_id=robot_state.robot_id if robot_state is not None else None,
                subtask_id=subtask_id,
            )
        except Exception:
            logger.warning("Failed to write embodied safety decision", exc_info=True)
            return None
        for evidence_event_id in dict.fromkeys(evidence_event_ids):
            try:
                self._memory_producer.add_relation(
                    mission_id=mission_id,
                    source_record_id=evidence_event_id,
                    target_record_id=decision_event.event_id,
                    relation_type="supports",
                    runtime_mode=self._runtime_mode,
                )
            except Exception:
                logger.warning("Failed to link safety evidence event", exc_info=True)
        return decision_event.event_id

    def _evaluate_state(
        self,
        planning_result: PlanningResult,
        robot_state: RobotState | None,
        environment_state: EnvironmentState | None,
        *,
        dry_run: bool,
    ) -> tuple[list[str], list[str], list[str]]:
        blocks: list[str] = []
        warnings: list[str] = []
        confirmations: list[str] = []
        if robot_state is not None:
            if not robot_state.online:
                blocks.append(f"Robot {robot_state.robot_id} is offline.")
            if robot_state.battery_percent is None:
                message = f"Robot {robot_state.robot_id} battery state is unknown."
                if dry_run:
                    warnings.append(message)
                else:
                    confirmations.append(message)
            elif robot_state.battery_percent < 10.0:
                blocks.append(
                    f"Robot {robot_state.robot_id} battery is too low: {robot_state.battery_percent}%."
                )
        if environment_state is not None and planning_result.target_floor is not None:
            if environment_state.reachable_floors is None:
                message = "Reachable floors are unknown."
                if dry_run:
                    warnings.append(message)
                else:
                    confirmations.append(message)
            elif planning_result.target_floor not in environment_state.reachable_floors:
                blocks.append(f"Target floor is not reachable: {planning_result.target_floor}")
        return blocks, warnings, confirmations


def _sensor_findings_by_name(robot_state: RobotState | None) -> dict[str, dict[str, object]]:
    if robot_state is None or not robot_state.sensor_diagnostics:
        return {}
    findings = robot_state.sensor_diagnostics.get("findings")
    if not isinstance(findings, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        sensor = finding.get("sensor")
        if isinstance(sensor, str) and sensor not in result:
            result[sensor] = finding
    return result


def _robot_state_summary(robot_state: RobotState | None) -> dict[str, object] | None:
    if robot_state is None:
        return None
    return {
        "robot_id": robot_state.robot_id,
        "online": robot_state.online,
        "battery_percent": robot_state.battery_percent,
        "mode": robot_state.mode,
        "available_sensors": (
            sorted(robot_state.available_sensors)
            if robot_state.available_sensors is not None
            else None
        ),
    }


def _environment_state_summary(
    environment_state: EnvironmentState | None,
) -> dict[str, object] | None:
    if environment_state is None:
        return None
    return {
        "reachable_floors": (
            sorted(environment_state.reachable_floors)
            if environment_state.reachable_floors is not None
            else None
        ),
    }
