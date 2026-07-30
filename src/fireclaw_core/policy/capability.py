from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
    execution_action_hash,
)
from fireclaw_core.execution.skills import Skill, SkillRegistry
from fireclaw_core.plugin.plugin_host import FireClawPluginHost


CAPABILITY_POLICY_ID = "fireclaw.capability-policy:v1"
CapabilityPolicyPhase = Literal["planning", "execution"]
CapabilityPolicyStageStatus = Literal[
    "allow",
    "block",
    "require_authorization",
    "deferred",
    "not_applicable",
]
CapabilityPolicyStatus = Literal["allow", "block", "require_authorization"]


@dataclass(frozen=True)
class CapabilityActor:
    actor_id: str
    role: str
    scopes: frozenset[str]
    source: str
    agent_role: str = "robot_agent"

    @classmethod
    def from_value(cls, value: Any) -> "CapabilityActor":
        if isinstance(value, CapabilityActor):
            return value
        scopes = getattr(value, "control_scopes", ())
        return cls(
            actor_id=str(getattr(value, "operator_id", "") or ""),
            role=str(getattr(value, "role", "") or ""),
            scopes=frozenset(str(item) for item in scopes),
            source=str(getattr(value, "source", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "role": self.role,
            "scopes": sorted(self.scopes),
            "source": self.source,
            "agent_role": self.agent_role,
        }


@dataclass(frozen=True)
class CapabilityRobotProfile:
    robot_id: str
    enabled: bool
    enabled_skills: frozenset[str]
    llm_exposed_skills: frozenset[str]
    profile_ref: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "CapabilityRobotProfile | None":
        if value is None:
            return None
        if isinstance(value, CapabilityRobotProfile):
            return value
        data_dir = getattr(value, "data_dir", None)
        return cls(
            robot_id=str(getattr(value, "robot_id", "") or ""),
            enabled=bool(getattr(value, "enabled", True)),
            enabled_skills=frozenset(
                str(item) for item in getattr(value, "enabled_skills", ())
            ),
            llm_exposed_skills=frozenset(
                str(item) for item in getattr(value, "llm_exposed_skills", ())
            ),
            profile_ref=str(data_dir) if data_dir is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "enabled": self.enabled,
            "enabled_skills": sorted(self.enabled_skills),
            "llm_exposed_skills": sorted(self.llm_exposed_skills),
            "profile_ref": self.profile_ref,
        }


@dataclass(frozen=True)
class CapabilityRuntimeState:
    robot_id: str
    online: bool | None
    battery_percent: float | None
    available_sensors: frozenset[str] | None
    emergency_stop_active: bool
    state_ref: str

    @classmethod
    def from_values(
        cls,
        robot_state: Any,
        *,
        emergency_stop_active: bool = False,
    ) -> "CapabilityRuntimeState | None":
        if robot_state is None:
            return None
        sensors = getattr(robot_state, "available_sensors", None)
        payload = asdict(robot_state) if hasattr(robot_state, "__dataclass_fields__") else {
            "robot_id": getattr(robot_state, "robot_id", None),
            "online": getattr(robot_state, "online", None),
            "battery_percent": getattr(robot_state, "battery_percent", None),
            "available_sensors": sensors,
        }
        payload["emergency_stop_active"] = emergency_stop_active
        return cls(
            robot_id=str(getattr(robot_state, "robot_id", "") or ""),
            online=(
                bool(getattr(robot_state, "online"))
                if getattr(robot_state, "online", None) is not None
                else None
            ),
            battery_percent=(
                float(getattr(robot_state, "battery_percent"))
                if getattr(robot_state, "battery_percent", None) is not None
                else None
            ),
            available_sensors=(
                frozenset(str(item) for item in sensors)
                if sensors is not None
                else None
            ),
            emergency_stop_active=emergency_stop_active,
            state_ref=_payload_ref(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "online": self.online,
            "battery_percent": self.battery_percent,
            "available_sensors": (
                sorted(self.available_sensors)
                if self.available_sensors is not None
                else None
            ),
            "emergency_stop_active": self.emergency_stop_active,
            "state_ref": self.state_ref,
        }


@dataclass(frozen=True)
class CapabilityPolicyContext:
    phase: CapabilityPolicyPhase
    actor: CapabilityActor
    robot_id: str
    mission_id: str | None = None
    task_id: str | None = None
    delegated_operator_id: str | None = None
    required_scope: str = "task.submit"
    allowed_skills: frozenset[str] | None = None
    target: dict[str, Any] = field(default_factory=dict)
    robot_profile: CapabilityRobotProfile | None = None
    runtime_state: CapabilityRuntimeState | None = None
    dry_run: bool = True
    safety_status: str | None = None
    safety_reasons: tuple[str, ...] = ()
    execution_authorization: VerifiedExecutionAuthorization | None = None


@dataclass(frozen=True)
class CapabilityPolicyStageDecision:
    stage: str
    status: CapabilityPolicyStageStatus
    reason_code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class CapabilityPolicyDecision:
    decision_id: str
    policy_id: str
    phase: CapabilityPolicyPhase
    status: CapabilityPolicyStatus
    skill_name: str
    inputs_hash: str
    action_hash: str
    robot_id: str
    mission_id: str | None
    task_id: str | None
    evaluated_at: str
    stages: tuple[CapabilityPolicyStageDecision, ...]

    @property
    def blocking_stage(self) -> CapabilityPolicyStageDecision | None:
        return next(
            (
                stage
                for stage in self.stages
                if stage.status in {"block", "require_authorization"}
            ),
            None,
        )

    @property
    def reason_code(self) -> str:
        stage = self.blocking_stage
        return stage.reason_code if stage is not None else "capability_allowed"

    @property
    def message(self) -> str:
        stage = self.blocking_stage
        return stage.message if stage is not None else "Capability policy allowed the action."

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "policy_id": self.policy_id,
            "phase": self.phase,
            "status": self.status,
            "skill_name": self.skill_name,
            "inputs_hash": self.inputs_hash,
            "action_hash": self.action_hash,
            "robot_id": self.robot_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "evaluated_at": self.evaluated_at,
            "reason_code": self.reason_code,
            "message": self.message,
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass(frozen=True)
class CapabilityPolicyProjection:
    projection_id: str
    policy_id: str
    phase: CapabilityPolicyPhase
    before: tuple[str, ...]
    after: tuple[str, ...]
    decisions: tuple[CapabilityPolicyDecision, ...]
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "policy_id": self.policy_id,
            "phase": self.phase,
            "before": list(self.before),
            "after": list(self.after),
            "excluded": [
                decision.to_dict()
                for decision in self.decisions
                if decision.status != "allow"
            ],
            "decisions": [
                decision.to_dict() for decision in self.decisions
            ],
            "evaluated_at": self.evaluated_at,
        }


class CapabilityPolicyPipeline:
    """Apply ordered capability policy at LLM projection and action execution."""

    def __init__(
        self,
        *,
        plugin_host: FireClawPluginHost,
        registry: SkillRegistry,
    ) -> None:
        self.plugin_host = plugin_host
        self.registry = registry

    def project(
        self,
        skill_names: set[str] | tuple[str, ...] | list[str],
        *,
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyProjection:
        before = tuple(sorted(dict.fromkeys(skill_names)))
        decisions = tuple(
            self.evaluate(
                skill_name,
                {},
                context=context,
            )
            for skill_name in before
        )
        return CapabilityPolicyProjection(
            projection_id=f"cap-proj-{uuid4().hex}",
            policy_id=CAPABILITY_POLICY_ID,
            phase=context.phase,
            before=before,
            after=tuple(
                decision.skill_name
                for decision in decisions
                if decision.status == "allow"
            ),
            decisions=decisions,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    def evaluate(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        *,
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyDecision:
        skill = self.registry.get(skill_name)
        stages = (
            self._identity_stage(context),
            evaluate_delegation_policy(
                skill_name=skill_name,
                inputs=inputs,
                allowed_skills=context.allowed_skills,
                target=context.target,
                skill=skill,
                validate_inputs=context.phase == "execution",
            ),
            self._plugin_stage(skill_name, skill),
            self._robot_profile_stage(skill_name, context),
            self._runtime_state_stage(skill, context),
            self._safety_stage(context),
            self._authorization_stage(skill_name, inputs, context),
        )
        status: CapabilityPolicyStatus = "allow"
        if any(stage.status == "block" for stage in stages):
            status = "block"
        elif any(
            stage.status == "require_authorization" for stage in stages
        ):
            status = "require_authorization"
        return CapabilityPolicyDecision(
            decision_id=f"cap-decision-{uuid4().hex}",
            policy_id=CAPABILITY_POLICY_ID,
            phase=context.phase,
            status=status,
            skill_name=skill_name,
            inputs_hash=_payload_ref(inputs),
            action_hash=execution_action_hash(skill_name, inputs),
            robot_id=context.robot_id,
            mission_id=context.mission_id,
            task_id=context.task_id,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            stages=stages,
        )

    @staticmethod
    def _identity_stage(
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyStageDecision:
        actor = context.actor
        evidence = {
            "actor": actor.to_dict(),
            "required_scope": context.required_scope,
            "delegated_operator_id": context.delegated_operator_id,
        }
        if not actor.actor_id:
            return _stage(
                "identity",
                "block",
                "actor_identity_missing",
                "Capability request has no authenticated actor identity.",
                evidence,
            )
        if context.required_scope not in actor.scopes:
            return _stage(
                "identity",
                "block",
                "actor_scope_missing",
                (
                    f"Actor {actor.actor_id!r} lacks required scope "
                    f"{context.required_scope!r}."
                ),
                evidence,
            )
        if (
            context.delegated_operator_id is not None
            and context.delegated_operator_id != actor.actor_id
        ):
            return _stage(
                "identity",
                "block",
                "delegated_operator_mismatch",
                "Task operator identity does not match the admitted actor.",
                evidence,
            )
        return _stage(
            "identity",
            "allow",
            "actor_scope_allowed",
            "Actor identity and required scope are admitted.",
            evidence,
        )

    def _plugin_stage(
        self,
        skill_name: str,
        skill: Skill | None,
    ) -> CapabilityPolicyStageDecision:
        contribution = self.plugin_host.get("tool", skill_name)
        if contribution is None or skill is None:
            return _stage(
                "plugin_exposure",
                "block",
                "tool_not_registered",
                f"Tool {skill_name!r} is not registered by the active Plugin Host.",
                {"skill_name": skill_name},
            )
        record = self.plugin_host.record(contribution.owner_plugin_id)
        if record is None or record.status != "active":
            return _stage(
                "plugin_exposure",
                "block",
                "plugin_not_active",
                f"Tool {skill_name!r} is owned by an inactive plugin.",
                {
                    "skill_name": skill_name,
                    "owner_plugin_id": contribution.owner_plugin_id,
                    "plugin_status": record.status if record is not None else None,
                },
            )
        return _stage(
            "plugin_exposure",
            "allow",
            "plugin_tool_active",
            "Tool is registered by an active plugin.",
            {
                "skill_name": skill_name,
                "owner_plugin_id": contribution.owner_plugin_id,
                "plugin_api_version": record.api_version,
                "plugin_source": record.source,
            },
        )

    @staticmethod
    def _robot_profile_stage(
        skill_name: str,
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyStageDecision:
        profile = context.robot_profile
        if profile is None:
            return _stage(
                "robot_profile",
                "not_applicable",
                "robot_profile_not_configured",
                "No Robot Capability Profile is configured.",
            )
        evidence = profile.to_dict()
        if not profile.enabled:
            return _stage(
                "robot_profile",
                "block",
                "robot_profile_disabled",
                f"Robot profile {profile.robot_id!r} is disabled.",
                evidence,
            )
        if profile.robot_id != context.robot_id:
            return _stage(
                "robot_profile",
                "block",
                "robot_profile_mismatch",
                "Robot profile identity does not match the action target.",
                evidence,
            )
        if skill_name not in profile.enabled_skills:
            return _stage(
                "robot_profile",
                "block",
                "skill_not_enabled_for_robot",
                f"Skill {skill_name!r} is not enabled for this robot.",
                evidence,
            )
        if (
            context.phase == "planning"
            and skill_name not in profile.llm_exposed_skills
        ):
            return _stage(
                "robot_profile",
                "block",
                "skill_not_exposed_to_llm",
                f"Skill {skill_name!r} is not exposed to the robot-local LLM.",
                evidence,
            )
        return _stage(
            "robot_profile",
            "allow",
            "robot_profile_allows_skill",
            "Robot Capability Profile allows this skill.",
            evidence,
        )

    @staticmethod
    def _runtime_state_stage(
        skill: Skill | None,
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyStageDecision:
        state = context.runtime_state
        if state is None:
            return _stage(
                "runtime_state",
                "deferred" if context.phase == "planning" else "block",
                "robot_state_unavailable",
                "Robot runtime state is unavailable.",
            )
        evidence = state.to_dict()
        if state.robot_id and state.robot_id != context.robot_id:
            return _stage(
                "runtime_state",
                "block",
                "robot_state_identity_mismatch",
                "Runtime state belongs to a different robot.",
                evidence,
            )
        if state.emergency_stop_active:
            return _stage(
                "runtime_state",
                "block",
                "emergency_stop_active",
                "Robot resource admission is closed by emergency stop.",
                evidence,
            )
        if state.online is False:
            return _stage(
                "runtime_state",
                "block",
                "robot_offline",
                f"Robot {context.robot_id!r} is offline.",
                evidence,
            )
        if (
            state.battery_percent is not None
            and state.battery_percent < 10.0
        ):
            return _stage(
                "runtime_state",
                "block",
                "robot_battery_too_low",
                f"Robot battery is too low: {state.battery_percent}%.",
                evidence,
            )
        if skill is not None and skill.required_sensors:
            if state.available_sensors is None:
                return _stage(
                    "runtime_state",
                    "deferred",
                    "sensor_state_unknown",
                    "Required sensor availability is unknown and must be checked by SafetyGate.",
                    evidence,
                )
            missing = _missing_sensors(skill, state.available_sensors)
            if missing:
                return _stage(
                    "runtime_state",
                    "block",
                    "required_sensor_unavailable",
                    (
                        f"Skill {skill.name!r} requires unavailable sensors: "
                        f"{', '.join(missing)}."
                    ),
                    {**evidence, "missing_sensors": missing},
                )
        return _stage(
            "runtime_state",
            "allow",
            "runtime_state_allows_skill",
            "Current robot state allows this skill.",
            evidence,
        )

    @staticmethod
    def _safety_stage(
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyStageDecision:
        if context.phase == "planning":
            return _stage(
                "safety_gate",
                "deferred",
                "safety_deferred_until_action",
                "SafetyGate requires concrete action inputs and current state.",
            )
        evidence = {
            "status": context.safety_status,
            "reasons": list(context.safety_reasons),
        }
        if context.safety_status == "allow":
            return _stage(
                "safety_gate",
                "allow",
                "safety_gate_allowed",
                "SafetyGate allowed the concrete plan.",
                evidence,
            )
        if context.safety_status == "require_confirmation":
            return _stage(
                "safety_gate",
                "require_authorization",
                "safety_confirmation_required",
                "SafetyGate requires operator authorization.",
                evidence,
            )
        return _stage(
            "safety_gate",
            "block",
            "safety_gate_not_allowed",
            "SafetyGate did not allow the concrete plan.",
            evidence,
        )

    @staticmethod
    def _authorization_stage(
        skill_name: str,
        inputs: dict[str, Any],
        context: CapabilityPolicyContext,
    ) -> CapabilityPolicyStageDecision:
        if context.phase == "planning":
            return _stage(
                "execution_authorization",
                "deferred",
                "authorization_deferred_until_action",
                "Exact execution authorization requires concrete action inputs.",
            )
        if context.dry_run:
            return _stage(
                "execution_authorization",
                "not_applicable",
                "authorization_not_required_for_dry_run",
                "Dry-run execution does not require physical action authority.",
            )
        grant = context.execution_authorization
        if grant is None:
            return _stage(
                "execution_authorization",
                "require_authorization",
                "execution_authorization_missing",
                "Real robot execution requires exact action authorization.",
            )
        evidence = {
            "authorization_id": grant.authorization_id,
            "request_id": grant.request_id,
            "operator_id": grant.operator_id,
            "scope_hash": grant.scope_hash,
            "expires_at": grant.expires_at,
        }
        if _authorization_expired(grant.expires_at):
            return _stage(
                "execution_authorization",
                "block",
                "execution_authorization_expired",
                "Execution authorization expired before the physical action.",
                evidence,
            )
        if not grant.authorizes(skill_name, inputs):
            return _stage(
                "execution_authorization",
                "block",
                "authorization_action_mismatch",
                "Execution authorization does not cover the exact skill inputs.",
                evidence,
            )
        return _stage(
            "execution_authorization",
            "allow",
            "execution_authorization_allowed",
            "Exact action authorization is valid.",
            evidence,
        )


def evaluate_delegation_policy(
    *,
    skill_name: str,
    inputs: dict[str, Any],
    allowed_skills: frozenset[str] | set[str] | None,
    target: dict[str, Any],
    skill: Any | None,
    validate_inputs: bool,
) -> CapabilityPolicyStageDecision:
    evidence = {
        "allowed_skills": (
            sorted(allowed_skills) if allowed_skills is not None else None
        ),
        "target_ref": _payload_ref(target),
    }
    if allowed_skills is None:
        return _stage(
            "task_delegation",
            "not_applicable",
            "task_delegation_not_present",
            "No structured task delegation restricts this action.",
            evidence,
        )
    if skill_name not in allowed_skills:
        return _stage(
            "task_delegation",
            "block",
            "skill_outside_task_delegation",
            f"Skill {skill_name!r} is outside the task's allowed skills.",
            evidence,
        )
    physical_plugin = (
        getattr(skill, "physical_plugin", None)
        if skill is not None
        else None
    )
    if (
        physical_plugin is None
        and skill is not None
        and callable(getattr(skill, "protected_input_errors", None))
    ):
        physical_plugin = skill
    if validate_inputs and physical_plugin is not None:
        errors = physical_plugin.protected_input_errors(
            target=target,
            proposed_inputs=inputs,
        )
        if errors:
            return _stage(
                "task_delegation",
                "block",
                "protected_task_input_mismatch",
                "; ".join(errors),
                {**evidence, "input_errors": errors},
            )
    return _stage(
        "task_delegation",
        "allow",
        "task_delegation_allows_skill",
        "Task delegation allows this skill and its protected inputs.",
        evidence,
    )


def _missing_sensors(
    skill: Skill,
    available_sensors: frozenset[str],
) -> list[str]:
    alternatives = (
        skill.physical_plugin.sensor_alternatives
        if skill.physical_plugin is not None
        else {}
    )
    missing: list[str] = []
    for sensor in skill.required_sensors:
        candidates = {sensor, *alternatives.get(sensor, ())}
        if candidates.isdisjoint(available_sensors):
            missing.append(sensor)
    return missing


def _authorization_expired(expires_at: str) -> bool:
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return True
    return datetime.now(timezone.utc) >= parsed.astimezone(timezone.utc)


def _stage(
    stage: str,
    status: CapabilityPolicyStageStatus,
    reason_code: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> CapabilityPolicyStageDecision:
    return CapabilityPolicyStageDecision(
        stage=stage,
        status=status,
        reason_code=reason_code,
        message=message,
        evidence=dict(evidence or {}),
    )


def _payload_ref(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
