from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from fireclaw_core.mission.task_graph import (
    MissionEvidenceRequirement,
    MissionTarget,
    VALID_RECOVERY_POLICIES,
    VALID_TASK_RISK_LEVELS,
)


@dataclass(frozen=True)
class TaskTypeDefinition:
    task_type: str
    allowed_capabilities: tuple[str, ...]
    success_skills: tuple[str, ...]
    default_timeout_seconds: float
    recovery_policy: str
    risk_level: str = "low"
    result_evidence_kind: str | None = None
    result_criteria: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.task_type.strip():
            raise ValueError("Task type name must not be empty.")
        if not self.allowed_capabilities:
            raise ValueError(
                f"Task type {self.task_type!r} requires allowed capabilities."
            )
        if not self.success_skills:
            raise ValueError(
                f"Task type {self.task_type!r} requires success skills."
            )
        if self.default_timeout_seconds <= 0:
            raise ValueError(
                f"Task type {self.task_type!r} timeout must be positive."
            )
        if self.recovery_policy not in VALID_RECOVERY_POLICIES:
            raise ValueError(
                f"Task type {self.task_type!r} has invalid recovery policy."
            )
        if self.risk_level not in VALID_TASK_RISK_LEVELS:
            raise ValueError(
                f"Task type {self.task_type!r} has invalid risk level."
            )
        if (
            self.result_evidence_kind is None
            and self.result_criteria is not None
        ):
            raise ValueError(
                f"Task type {self.task_type!r} has criteria without result evidence."
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_type": self.task_type,
            "allowed_capabilities": list(self.allowed_capabilities),
            "success_skills": list(self.success_skills),
            "default_timeout_seconds": self.default_timeout_seconds,
            "recovery_policy": self.recovery_policy,
            "risk_level": self.risk_level,
        }
        if self.result_evidence_kind is not None:
            result["result_evidence_kind"] = self.result_evidence_kind
            result["result_criteria"] = dict(self.result_criteria or {})
        return result


class TaskTypeRegistry:
    """Deterministic catalogue of task semantics and completion policy."""

    def __init__(
        self,
        definitions: Iterable[TaskTypeDefinition] = (),
    ) -> None:
        self._definitions: dict[str, TaskTypeDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: TaskTypeDefinition) -> None:
        if definition.task_type in self._definitions:
            raise ValueError(
                f"Duplicate task type definition: {definition.task_type}"
            )
        self._definitions[definition.task_type] = definition

    def get(self, task_type: str) -> TaskTypeDefinition | None:
        return self._definitions.get(task_type)

    def require(self, task_type: str) -> TaskTypeDefinition:
        definition = self.get(task_type)
        if definition is None:
            raise CompletionContractError([
                f"Unknown mission task_type {task_type!r}."
            ])
        return definition

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def definitions(self) -> tuple[TaskTypeDefinition, ...]:
        return tuple(self._definitions[name] for name in self.names())


DEFAULT_TASK_TYPE_DEFINITIONS = (
    TaskTypeDefinition(
        task_type="victim_search",
        allowed_capabilities=("search_for_victims",),
        success_skills=("search_for_victims",),
        result_evidence_kind="victim_search_result",
        result_criteria={
            "victims_found": {"operator": "gte", "value": 0},
        },
        default_timeout_seconds=180.0,
        recovery_policy="reassign",
        risk_level="medium",
    ),
    TaskTypeDefinition(
        task_type="navigation",
        allowed_capabilities=(
            "navigate",
            "navigate_to_point",
            "navigate_to_pose",
            "navigate_to_floor",
        ),
        success_skills=(
            "navigate",
            "navigate_to_point",
            "navigate_to_pose",
            "navigate_to_floor",
        ),
        default_timeout_seconds=120.0,
        recovery_policy="replan",
        risk_level="medium",
    ),
    TaskTypeDefinition(
        task_type="fire_suppression",
        allowed_capabilities=("firefight", "extinguish_fire"),
        success_skills=("firefight", "extinguish_fire"),
        result_evidence_kind="fire_suppression_result",
        result_criteria={
            "fire_suppressed": {"operator": "eq", "value": True},
        },
        default_timeout_seconds=240.0,
        recovery_policy="replan",
        risk_level="medium",
    ),
    TaskTypeDefinition(
        task_type="reconnaissance",
        allowed_capabilities=("recon", "monitor_environment"),
        success_skills=("recon", "monitor_environment"),
        default_timeout_seconds=180.0,
        recovery_policy="reassign",
        risk_level="medium",
    ),
    TaskTypeDefinition(
        task_type="patrol",
        allowed_capabilities=("patrol",),
        success_skills=("patrol",),
        default_timeout_seconds=240.0,
        recovery_policy="replan",
    ),
    TaskTypeDefinition(
        task_type="transport",
        allowed_capabilities=("transport",),
        success_skills=("transport",),
        default_timeout_seconds=300.0,
        recovery_policy="reassign",
        risk_level="medium",
    ),
)


def default_task_type_registry() -> TaskTypeRegistry:
    return TaskTypeRegistry(DEFAULT_TASK_TYPE_DEFINITIONS)


class CompletionContractError(ValueError):
    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(dict.fromkeys(errors))
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class CompiledCompletionContract:
    task_type: str
    completion_goal: str
    success_evidence: tuple[MissionEvidenceRequirement, ...]
    timeout_seconds: float
    recovery_policy: str
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "completion_goal": self.completion_goal,
            "success_evidence": [
                requirement.to_dict()
                for requirement in self.success_evidence
            ],
            "timeout_seconds": self.timeout_seconds,
            "recovery_policy": self.recovery_policy,
            "risk_level": self.risk_level,
        }


class CompletionContractCompiler:
    def __init__(self, registry: TaskTypeRegistry | None = None) -> None:
        self.registry = registry or default_task_type_registry()

    def compile(
        self,
        *,
        task_type: str,
        capability_required: str,
        target: MissionTarget,
        completion_goal: str,
    ) -> CompiledCompletionContract:
        definition = self.registry.require(task_type)
        errors: list[str] = []
        if capability_required not in definition.allowed_capabilities:
            errors.append(
                f"Mission task_type {task_type!r} does not allow capability "
                f"{capability_required!r}."
            )
        if not completion_goal.strip():
            errors.append(
                f"Mission task_type {task_type!r} requires a completion goal."
            )
        if errors:
            raise CompletionContractError(errors)

        evidence = [
            MissionEvidenceRequirement(
                kind="task_terminal_success",
                source="execution_monitor",
            ),
            MissionEvidenceRequirement(
                kind="skill_succeeded",
                source="robot_gateway",
                criteria={
                    "skill_name": {
                        "operator": "in",
                        "value": list(definition.success_skills),
                    }
                },
            ),
        ]
        if definition.result_evidence_kind is not None:
            evidence.append(
                MissionEvidenceRequirement(
                    kind=definition.result_evidence_kind,
                    source="robot_gateway",
                    criteria=dict(definition.result_criteria or {}),
                )
            )
        evidence.extend(_target_evidence(target))
        return CompiledCompletionContract(
            task_type=task_type,
            completion_goal=completion_goal.strip(),
            success_evidence=tuple(evidence),
            timeout_seconds=definition.default_timeout_seconds,
            recovery_policy=definition.recovery_policy,
            risk_level=definition.risk_level,
        )


@dataclass(frozen=True)
class EvidenceObservation:
    kind: str
    source: str
    data: dict[str, Any]
    observed_at: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True)
class EvidenceRequirementCheck:
    requirement: MissionEvidenceRequirement
    satisfied: bool
    matched_evidence_ids: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "requirement": self.requirement.to_dict(),
            "satisfied": self.satisfied,
            "matched_evidence_ids": list(self.matched_evidence_ids),
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class EvidenceValidationResult:
    satisfied: bool
    checks: tuple[EvidenceRequirementCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "checks": [check.to_dict() for check in self.checks],
        }


class ExecutionEvidenceValidator:
    """Validate terminal robot output against a compiled completion contract."""

    def validate(
        self,
        *,
        requirements: tuple[MissionEvidenceRequirement, ...],
        terminal_state: dict[str, Any],
        now: datetime | None = None,
    ) -> EvidenceValidationResult:
        observations = _extract_observations(terminal_state)
        reference_time = now or datetime.now(timezone.utc)
        checks: list[EvidenceRequirementCheck] = []
        for requirement in requirements:
            matches = [
                observation
                for observation in observations
                if observation.kind == requirement.kind
                and observation.source == requirement.source
                and _criteria_match(
                    observation.data,
                    requirement.criteria,
                )
                and _fresh_enough(
                    observation.observed_at,
                    requirement.max_age_seconds,
                    reference_time,
                )
            ]
            checks.append(
                EvidenceRequirementCheck(
                    requirement=requirement,
                    satisfied=bool(matches),
                    matched_evidence_ids=tuple(
                        observation.evidence_id
                        for observation in matches
                        if observation.evidence_id is not None
                    ),
                    reason=(
                        None
                        if matches
                        else "No authoritative observation matched this requirement."
                    ),
                )
            )
        return EvidenceValidationResult(
            satisfied=all(check.satisfied for check in checks),
            checks=tuple(checks),
        )


def _target_evidence(
    target: MissionTarget,
) -> list[MissionEvidenceRequirement]:
    evidence: list[MissionEvidenceRequirement] = []
    for field_name, value in (
        ("floor", target.floor),
        ("area_id", target.area_id),
        ("entity_id", target.entity_id),
    ):
        if value is None:
            continue
        evidence.append(
            MissionEvidenceRequirement(
                kind=f"target_{field_name}_confirmed",
                source="robot_gateway",
                criteria={
                    field_name: {"operator": "eq", "value": value},
                },
            )
        )
    if target.pose is not None:
        evidence.append(
            MissionEvidenceRequirement(
                kind="target_pose_confirmed",
                source="robot_gateway",
                criteria={
                    axis: {
                        "operator": "approx",
                        "value": coordinate,
                        "tolerance": 0.5,
                    }
                    for axis, coordinate in target.pose.items()
                    if axis in {"x", "y", "z", "yaw"}
                },
            )
        )
    return evidence


def _extract_observations(
    terminal_state: dict[str, Any],
) -> list[EvidenceObservation]:
    observations: list[EvidenceObservation] = []
    status = terminal_state.get("status")
    if status in {"succeeded", "completed"}:
        observations.append(
            EvidenceObservation(
                kind="task_terminal_success",
                source="execution_monitor",
                data={"status": status},
                observed_at=_timestamp_from(terminal_state),
            )
        )

    robot_trace = terminal_state.get("robot_trace")
    if not isinstance(robot_trace, dict):
        robot_trace = terminal_state
    observations.extend(_declared_observations(robot_trace))
    result = robot_trace.get("result")
    if not isinstance(result, dict):
        return observations
    observations.extend(_declared_observations(result))
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return observations
    steps = execution.get("steps")
    if not isinstance(steps, list):
        return observations
    for step in steps:
        if not isinstance(step, dict) or step.get("status") != "succeeded":
            continue
        output = step.get("output")
        output = output if isinstance(output, dict) else {}
        nested_data = output.get("data")
        data = (
            dict(nested_data)
            if isinstance(nested_data, dict)
            else {
                key: value
                for key, value in output.items()
                if key not in {
                    "status",
                    "robot_id",
                    "mode",
                    "action",
                    "dry_run",
                    "timestamp",
                    "action_id",
                    "error",
                }
            }
        )
        observed_at = _first_string(
            output.get("timestamp"),
            result.get("timestamp"),
        )
        skill_name = step.get("skill_name")
        skill_data = {
            "skill_name": skill_name,
            "action": output.get("action"),
            **data,
        }
        observations.append(
            EvidenceObservation(
                kind="skill_succeeded",
                source="robot_gateway",
                data=skill_data,
                observed_at=observed_at,
            )
        )
        for field_name in ("floor", "area_id", "entity_id"):
            if field_name in data:
                observations.append(
                    EvidenceObservation(
                        kind=f"target_{field_name}_confirmed",
                        source="robot_gateway",
                        data={field_name: data[field_name]},
                        observed_at=observed_at,
                    )
                )
        pose = data.get("pose")
        if isinstance(pose, dict):
            observations.append(
                EvidenceObservation(
                    kind="target_pose_confirmed",
                    source="robot_gateway",
                    data=dict(pose),
                    observed_at=observed_at,
                )
            )
        if (
            skill_name == "search_for_victims"
            and isinstance(data.get("victims_found"), int)
            and not isinstance(data.get("victims_found"), bool)
        ):
            observations.append(
                EvidenceObservation(
                    kind="victim_search_result",
                    source="robot_gateway",
                    data={"victims_found": data["victims_found"]},
                    observed_at=observed_at,
                )
            )
        if isinstance(data.get("fire_suppressed"), bool):
            observations.append(
                EvidenceObservation(
                    kind="fire_suppression_result",
                    source="robot_gateway",
                    data={"fire_suppressed": data["fire_suppressed"]},
                    observed_at=observed_at,
                )
            )
    return observations


def _declared_observations(value: dict[str, Any]) -> list[EvidenceObservation]:
    raw = value.get("completion_evidence")
    if not isinstance(raw, list):
        return []
    observations: list[EvidenceObservation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        source = item.get("source")
        data = item.get("data", {})
        if (
            not isinstance(kind, str)
            or not kind.strip()
            or not isinstance(source, str)
            or not source.strip()
            or not isinstance(data, dict)
        ):
            continue
        observations.append(
            EvidenceObservation(
                kind=kind,
                source=source,
                data=dict(data),
                observed_at=_first_string(item.get("observed_at")),
                evidence_id=_first_string(item.get("evidence_id")),
            )
        )
    return observations


def _criteria_match(
    data: dict[str, Any],
    criteria: dict[str, Any],
) -> bool:
    for path, predicate in criteria.items():
        found, actual = _path_value(data, path)
        if not _predicate_matches(found, actual, predicate):
            return False
    return True


def _path_value(data: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _predicate_matches(found: bool, actual: Any, predicate: Any) -> bool:
    if not isinstance(predicate, dict) or "operator" not in predicate:
        return found and actual == predicate
    operator = predicate.get("operator")
    expected = predicate.get("value")
    if operator == "present":
        return found is bool(expected)
    if not found:
        return False
    if operator == "eq":
        return actual == expected
    if operator == "in":
        return isinstance(expected, list) and actual in expected
    if operator == "contains":
        return isinstance(actual, (list, str)) and expected in actual
    if operator == "gte":
        return _numeric(actual) and _numeric(expected) and actual >= expected
    if operator == "lte":
        return _numeric(actual) and _numeric(expected) and actual <= expected
    if operator == "approx":
        tolerance = predicate.get("tolerance", 0.0)
        return (
            _numeric(actual)
            and _numeric(expected)
            and _numeric(tolerance)
            and abs(float(actual) - float(expected)) <= float(tolerance)
        )
    return False


def _fresh_enough(
    observed_at: str | None,
    max_age_seconds: float | None,
    now: datetime,
) -> bool:
    if max_age_seconds is None:
        return True
    if observed_at is None:
        return False
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None or observed.utcoffset() is None:
        return False
    return 0 <= (now - observed).total_seconds() <= max_age_seconds


def _timestamp_from(value: dict[str, Any]) -> str | None:
    return _first_string(
        value.get("updated_at"),
        value.get("ended_at"),
        value.get("timestamp"),
    )


def _first_string(*values: Any) -> str | None:
    return next(
        (
            value
            for value in values
            if isinstance(value, str) and value.strip()
        ),
        None,
    )


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
