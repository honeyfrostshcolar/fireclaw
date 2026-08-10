"""Versioned, lane-neutral scenario contracts for embodied evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Mapping

from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_TERMINAL_STATUSES,
    normalize_robot_task_terminal_status,
)


SCENARIO_SUITE_SCHEMA_VERSION = "fireclaw.evaluation.scenario-suite.v1"
EVALUATION_RUN_SCHEMA_VERSION = "fireclaw.evaluation.run.v1"

EVALUATION_LANES = frozenset({
    "deterministic_integration",
    "llm_planning",
    "ros_gazebo_system",
})
TARGET_TYPES = frozenset({"point", "area", "entity"})
DATASET_SPLITS = frozenset({"train", "development", "validation", "test"})
PLANNING_STATUSES = frozenset({
    "proposed",
    "clarification_required",
    "observation_required",
    "escalated",
    "blocked",
    "timed_out",
    "cancelled",
})
PLANNING_OPERATIONS = frozenset({
    "inspect_state",
    "execute_agent_tool",
    "propose_plan",
    "request_observation",
    "request_clarification",
    "escalate",
})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SUITE_FIELDS = frozenset({
    "schema_version",
    "suite_id",
    "suite_version",
    "lane",
    "split",
    "defaults",
    "scenarios",
    "metadata",
})
_SCENARIO_FIELDS = frozenset({
    "scenario_id",
    "scenario_version",
    "command",
    "target_type",
    "target",
    "expected_target",
    "expected_capability",
    "expected_terminal_outcomes",
    "expected_plan_success",
    "expected_dispatch_success",
    "requires_target_contract",
    "requires_terminal_status",
    "min_memory_records",
    "split",
    "seed",
    "seeds",
    "repetitions",
    "task_type",
    "planning_context",
    "expected_planning_statuses",
    "expected_intent",
    "expected_task_count",
    "expected_task_types",
    "required_planning_operations",
    "max_safety_rejections",
    "max_model_calls",
    "tags",
    "metadata",
})
_DEFAULT_FIELDS = _SCENARIO_FIELDS - {
    "scenario_id",
    "command",
    "target",
    "expected_target",
}
_PLANNING_CONTEXT_FIELDS = frozenset({
    "captured_at",
    "robots",
    "environment_facts",
    "resource_reservations",
    "retrieved_memories",
    "operator_corrections",
    "external_knowledge",
    "tool_exposed_belief_ids",
    "active_observation_capabilities",
})
_PLANNING_ROBOT_FIELDS = frozenset({
    "robot_id",
    "base_url",
    "capabilities",
    "zone",
    "enabled",
    "presence",
})
_PLANNING_FACT_FIELDS = frozenset({
    "fact_id",
    "kind",
    "value",
    "source",
    "observed_at",
    "evidence_ids",
    "confidence",
    "expires_at",
    "subject_id",
})
_PLANNING_RESERVATION_FIELDS = frozenset({
    "resource_id",
    "owner_task_id",
    "owner_robot_id",
    "status",
    "acquired_at",
    "evidence_ids",
    "expires_at",
})


@dataclass(frozen=True)
class EvaluationScenario:
    """One concrete scenario case after seed/repetition expansion."""

    suite_id: str
    suite_version: str
    scenario_id: str
    scenario_version: str
    lane: str
    split: str
    seed: int
    repeat_index: int
    command: str
    target_type: str
    target: dict[str, Any]
    expected_capability: str
    expected_terminal_outcomes: tuple[str, ...] = ("completed",)
    expected_plan_success: bool | None = True
    expected_dispatch_success: bool | None = None
    requires_target_contract: bool = True
    requires_terminal_status: bool = True
    min_memory_records: int = 0
    task_type: str = "navigate"
    planning_context: dict[str, Any] = field(default_factory=dict)
    expected_planning_statuses: tuple[str, ...] = ()
    expected_intent: str | None = None
    expected_task_count: int | None = None
    expected_task_types: tuple[str, ...] = ()
    required_planning_operations: tuple[str, ...] = ()
    max_safety_rejections: int | None = None
    max_model_calls: int | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return (
            f"{self.scenario_id}__seed-{self.seed}__"
            f"repeat-{self.repeat_index:03d}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "scenario_id": self.scenario_id,
            "scenario_version": self.scenario_version,
            "lane": self.lane,
            "split": self.split,
            "seed": self.seed,
            "repeat_index": self.repeat_index,
            "command": self.command,
            "target_type": self.target_type,
            "target": _json_copy(self.target),
            "expected_capability": self.expected_capability,
            "expected_terminal_outcomes": list(
                self.expected_terminal_outcomes
            ),
            "expected_plan_success": self.expected_plan_success,
            "expected_dispatch_success": self.expected_dispatch_success,
            "requires_target_contract": self.requires_target_contract,
            "requires_terminal_status": self.requires_terminal_status,
            "min_memory_records": self.min_memory_records,
            "task_type": self.task_type,
            "planning_context": _json_copy(self.planning_context),
            "expected_planning_statuses": list(
                self.expected_planning_statuses
            ),
            "expected_intent": self.expected_intent,
            "expected_task_count": self.expected_task_count,
            "expected_task_types": list(self.expected_task_types),
            "required_planning_operations": list(
                self.required_planning_operations
            ),
            "max_safety_rejections": self.max_safety_rejections,
            "max_model_calls": self.max_model_calls,
            "tags": list(self.tags),
            "metadata": _json_copy(self.metadata),
        }


@dataclass(frozen=True)
class EvaluationSuite:
    """A validated scenario suite and its immutable source identity."""

    schema_version: str
    suite_id: str
    suite_version: str
    lane: str
    source_path: str
    source_sha256: str
    scenarios: tuple[EvaluationScenario, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    legacy_source_format: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "lane": self.lane,
            "source": {
                "path": self.source_path,
                "sha256": self.source_sha256,
                "legacy_format": self.legacy_source_format,
            },
            "scenario_case_count": len(self.scenarios),
            "metadata": _json_copy(self.metadata),
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }


def load_evaluation_suite(path: str | Path) -> EvaluationSuite:
    """Load and validate a scenario suite, expanding seeds and repetitions.

    A top-level JSON list remains accepted as a compatibility input. It is
    normalized to the versioned contract and explicitly marked as legacy in
    the resulting provenance record.
    """

    source = Path(path)
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    legacy = isinstance(payload, list)
    if legacy:
        if not payload:
            raise ValueError("Fixture must be a non-empty JSON array.")
        suite_payload: dict[str, Any] = {
            "schema_version": SCENARIO_SUITE_SCHEMA_VERSION,
            "suite_id": _safe_legacy_suite_id(source.stem),
            "suite_version": "legacy-1",
            "lane": "deterministic_integration",
            "split": "test",
            "defaults": {"seed": 0, "repetitions": 1},
            "scenarios": payload,
            "metadata": {"normalized_from": "legacy_json_array"},
        }
    elif isinstance(payload, dict):
        suite_payload = dict(payload)
    else:
        raise ValueError("Scenario suite must be a JSON object or non-empty array.")

    _reject_unknown(suite_payload, _SUITE_FIELDS, "scenario suite")
    schema_version = _required_string(suite_payload, "schema_version")
    if schema_version != SCENARIO_SUITE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported scenario suite schema_version: "
            f"{schema_version!r}."
        )
    suite_id = _validated_id(suite_payload.get("suite_id"), "suite_id")
    suite_version = _required_string(suite_payload, "suite_version")
    lane = _required_string(suite_payload, "lane")
    if lane not in EVALUATION_LANES:
        raise ValueError(f"lane must be one of {sorted(EVALUATION_LANES)}")
    suite_split = _validated_split(suite_payload.get("split", "test"))
    defaults = suite_payload.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("scenario suite defaults must be an object")
    _reject_unknown(defaults, _DEFAULT_FIELDS, "scenario defaults")
    scenarios_payload = suite_payload.get("scenarios")
    if not isinstance(scenarios_payload, list) or not scenarios_payload:
        raise ValueError("scenario suite scenarios must be a non-empty array")
    metadata = suite_payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("scenario suite metadata must be an object")

    concrete: list[EvaluationScenario] = []
    for scenario_payload in scenarios_payload:
        if not isinstance(scenario_payload, dict):
            raise ValueError("each scenario must be an object")
        _reject_unknown(scenario_payload, _SCENARIO_FIELDS, "scenario")
        merged = {**defaults, **scenario_payload}
        if "seeds" in scenario_payload:
            merged.pop("seed", None)
        if "seed" in scenario_payload:
            merged.pop("seeds", None)
        concrete.extend(
            _expand_scenario(
                merged,
                suite_id=suite_id,
                suite_version=suite_version,
                lane=lane,
                suite_split=suite_split,
            )
        )

    case_ids = [scenario.case_id for scenario in concrete]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("scenario suite expands to duplicate case_id values")

    return EvaluationSuite(
        schema_version=schema_version,
        suite_id=suite_id,
        suite_version=suite_version,
        lane=lane,
        source_path=str(source.resolve(strict=False)),
        source_sha256=sha256(raw).hexdigest(),
        scenarios=tuple(concrete),
        metadata=_json_copy(metadata),
        legacy_source_format=legacy,
    )


def _expand_scenario(
    value: Mapping[str, Any],
    *,
    suite_id: str,
    suite_version: str,
    lane: str,
    suite_split: str,
) -> list[EvaluationScenario]:
    scenario_id = _validated_id(value.get("scenario_id"), "scenario_id")
    scenario_version = str(value.get("scenario_version") or "1.0.0").strip()
    if not scenario_version:
        raise ValueError("scenario_version must not be empty")
    command = _required_string(value, "command")
    target = value.get("target", value.get("expected_target"))
    if not isinstance(target, dict):
        raise ValueError(f"scenario {scenario_id!r} target must be an object")
    target_type = value.get("target_type") or _infer_target_type(target)
    if target_type not in TARGET_TYPES:
        raise ValueError(f"target_type must be one of {sorted(TARGET_TYPES)}")
    _validate_target(str(target_type), target, scenario_id=scenario_id)
    expected_capability = _required_string(value, "expected_capability")
    task_type = str(value.get("task_type") or "navigate").strip()
    if not task_type:
        raise ValueError("task_type must not be empty")
    split = _validated_split(value.get("split", suite_split))
    seeds = _seeds(value)
    repetitions = _nonnegative_int(
        value.get("repetitions", 1),
        "repetitions",
        minimum=1,
    )
    terminal_outcomes = _terminal_outcomes(
        value.get("expected_terminal_outcomes", ["completed"])
    )
    expected_plan_success = _optional_boolean(
        value.get("expected_plan_success", True),
        "expected_plan_success",
    )
    expected_dispatch_success = _optional_boolean(
        value.get(
            "expected_dispatch_success",
            (
                None
                if lane == "llm_planning"
                else True if terminal_outcomes == ("completed",) else None
            ),
        ),
        "expected_dispatch_success",
    )
    requires_target_contract = value.get("requires_target_contract", True)
    if not isinstance(requires_target_contract, bool):
        raise ValueError("requires_target_contract must be a boolean")
    requires_terminal = value.get(
        "requires_terminal_status",
        lane != "llm_planning",
    )
    if not isinstance(requires_terminal, bool):
        raise ValueError("requires_terminal_status must be a boolean")
    min_memory_records = _nonnegative_int(
        value.get("min_memory_records", 0),
        "min_memory_records",
    )
    planning_context = value.get("planning_context", {})
    if not isinstance(planning_context, dict):
        raise ValueError("planning_context must be an object")
    expected_planning_statuses = _planning_statuses(
        value.get(
            "expected_planning_statuses",
            ["proposed"] if lane == "llm_planning" else [],
        )
    )
    expected_intent = _optional_string(
        value.get("expected_intent"),
        "expected_intent",
    )
    expected_task_count = _optional_nonnegative_int(
        value.get(
            "expected_task_count",
            1 if lane == "llm_planning" and expected_plan_success else None,
        ),
        "expected_task_count",
    )
    expected_task_types = _string_tuple(
        value.get(
            "expected_task_types",
            [task_type]
            if lane == "llm_planning" and expected_plan_success
            else [],
        ),
        "expected_task_types",
    )
    required_planning_operations = _string_tuple(
        value.get("required_planning_operations", []),
        "required_planning_operations",
        allowed=PLANNING_OPERATIONS,
    )
    max_safety_rejections = _optional_nonnegative_int(
        value.get(
            "max_safety_rejections",
            0 if lane == "llm_planning" else None,
        ),
        "max_safety_rejections",
    )
    max_model_calls = _optional_nonnegative_int(
        value.get(
            "max_model_calls",
            4 if lane == "llm_planning" else None,
        ),
        "max_model_calls",
        minimum=1,
    )
    if lane == "llm_planning":
        if not planning_context:
            raise ValueError(
                f"scenario {scenario_id!r} requires planning_context"
            )
        if requires_terminal:
            raise ValueError(
                "llm_planning scenarios must set "
                "requires_terminal_status=false"
            )
        if expected_dispatch_success is not None:
            raise ValueError(
                "llm_planning scenarios must set "
                "expected_dispatch_success=null"
            )
        _validate_planning_context(planning_context, scenario_id=scenario_id)
    elif planning_context or expected_planning_statuses:
        raise ValueError(
            "planning_context and expected_planning_statuses are only valid "
            "for the llm_planning lane"
        )
    tags_value = value.get("tags", [])
    if not isinstance(tags_value, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in tags_value
    ):
        raise ValueError("tags must be an array of non-empty strings")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("scenario metadata must be an object")

    return [
        EvaluationScenario(
            suite_id=suite_id,
            suite_version=suite_version,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            lane=lane,
            split=split,
            seed=seed,
            repeat_index=repeat_index,
            command=command,
            target_type=str(target_type),
            target=_json_copy(target),
            expected_capability=expected_capability,
            expected_terminal_outcomes=terminal_outcomes,
            expected_plan_success=expected_plan_success,
            expected_dispatch_success=expected_dispatch_success,
            requires_target_contract=requires_target_contract,
            requires_terminal_status=requires_terminal,
            min_memory_records=min_memory_records,
            task_type=task_type,
            planning_context=_json_copy(planning_context),
            expected_planning_statuses=expected_planning_statuses,
            expected_intent=expected_intent,
            expected_task_count=expected_task_count,
            expected_task_types=expected_task_types,
            required_planning_operations=required_planning_operations,
            max_safety_rejections=max_safety_rejections,
            max_model_calls=max_model_calls,
            tags=tuple(tag.strip() for tag in tags_value),
            metadata=_json_copy(metadata),
        )
        for seed in seeds
        for repeat_index in range(repetitions)
    ]


def _validate_target(
    target_type: str,
    target: Mapping[str, Any],
    *,
    scenario_id: str,
) -> None:
    frame_id = target.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id.strip():
        raise ValueError(f"scenario {scenario_id!r} target requires frame_id")
    required_key = {
        "point": "pose",
        "area": "area_id",
        "entity": "entity_id",
    }[target_type]
    if required_key not in target:
        raise ValueError(
            f"scenario {scenario_id!r} {target_type} target requires "
            f"{required_key}"
        )
    if target_type == "point":
        pose = target.get("pose")
        if not isinstance(pose, dict):
            raise ValueError(f"scenario {scenario_id!r} target pose must be an object")
        for axis in ("x", "y"):
            coordinate = pose.get(axis)
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not isfinite(float(coordinate))
            ):
                raise ValueError(
                    f"scenario {scenario_id!r} point target requires finite {axis}"
                )
        yaw = pose.get("yaw", 0.0)
        if (
            isinstance(yaw, bool)
            or not isinstance(yaw, (int, float))
            or not isfinite(float(yaw))
        ):
            raise ValueError(
                f"scenario {scenario_id!r} point target yaw must be finite"
            )
    else:
        identifier = target.get(required_key)
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(
                f"scenario {scenario_id!r} target {required_key} must not be empty"
            )


def _infer_target_type(target: Mapping[str, Any]) -> str:
    matches = [
        target_type
        for target_type, key in (
            ("point", "pose"),
            ("area", "area_id"),
            ("entity", "entity_id"),
        )
        if key in target
    ]
    if len(matches) != 1:
        raise ValueError(
            "target_type is required when a target has zero or multiple "
            "point/area/entity selectors"
        )
    return matches[0]


def _validate_planning_context(
    value: Mapping[str, Any],
    *,
    scenario_id: str,
) -> None:
    _reject_unknown(
        value,
        _PLANNING_CONTEXT_FIELDS,
        f"scenario {scenario_id!r} planning_context",
    )
    _validated_timestamp(
        value.get("captured_at"),
        "planning_context.captured_at",
    )
    robots = value.get("robots")
    if not isinstance(robots, list) or not robots:
        raise ValueError("planning_context.robots must be a non-empty array")
    robot_ids: list[str] = []
    for index, robot in enumerate(robots):
        if not isinstance(robot, dict):
            raise ValueError(
                f"planning_context.robots[{index}] must be an object"
            )
        _reject_unknown(
            robot,
            _PLANNING_ROBOT_FIELDS,
            f"planning_context.robots[{index}]",
        )
        robot_id = _required_string(robot, "robot_id")
        robot_ids.append(robot_id)
        _required_string(robot, "base_url")
        _string_tuple(
            robot.get("capabilities", []),
            f"planning_context.robots[{index}].capabilities",
        )
        if not isinstance(robot.get("enabled", True), bool):
            raise ValueError(
                f"planning_context.robots[{index}].enabled must be a boolean"
            )
        zone = robot.get("zone")
        if zone is not None and (
            not isinstance(zone, str) or not zone.strip()
        ):
            raise ValueError(
                f"planning_context.robots[{index}].zone must be a non-empty "
                "string or null"
            )
        presence = robot.get("presence", {})
        if not isinstance(presence, dict):
            raise ValueError(
                f"planning_context.robots[{index}].presence must be an object"
            )
    if len(robot_ids) != len(set(robot_ids)):
        raise ValueError("planning_context robot_id values must be unique")

    facts = _mapping_list(
        value.get("environment_facts", []),
        "planning_context.environment_facts",
    )
    fact_ids: list[str] = []
    for index, fact in enumerate(facts):
        label = f"planning_context.environment_facts[{index}]"
        _reject_unknown(fact, _PLANNING_FACT_FIELDS, label)
        fact_ids.append(_required_string(fact, "fact_id"))
        for field_name in ("kind", "source"):
            _required_string(fact, field_name)
        _validated_timestamp(fact.get("observed_at"), f"{label}.observed_at")
        expires_at = fact.get("expires_at")
        if expires_at is not None:
            _validated_timestamp(expires_at, f"{label}.expires_at")
        evidence_ids = _string_tuple(
            fact.get("evidence_ids", []),
            f"{label}.evidence_ids",
        )
        if not evidence_ids:
            raise ValueError(f"{label}.evidence_ids must not be empty")
        confidence = fact.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ValueError(f"{label}.confidence must be between 0 and 1")
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("planning_context fact_id values must be unique")

    reservations = _mapping_list(
        value.get("resource_reservations", []),
        "planning_context.resource_reservations",
    )
    resource_ids: list[str] = []
    for index, reservation in enumerate(reservations):
        label = f"planning_context.resource_reservations[{index}]"
        _reject_unknown(reservation, _PLANNING_RESERVATION_FIELDS, label)
        resource_ids.append(_required_string(reservation, "resource_id"))
        for field_name in ("owner_task_id", "status"):
            _required_string(reservation, field_name)
        _validated_timestamp(
            reservation.get("acquired_at"),
            f"{label}.acquired_at",
        )
        expires_at = reservation.get("expires_at")
        if expires_at is not None:
            _validated_timestamp(expires_at, f"{label}.expires_at")
        _string_tuple(
            reservation.get("evidence_ids", []),
            f"{label}.evidence_ids",
        )
    if len(resource_ids) != len(set(resource_ids)):
        raise ValueError("planning_context resource_id values must be unique")

    for field_name in (
        "retrieved_memories",
        "operator_corrections",
        "external_knowledge",
    ):
        _mapping_list(
            value.get(field_name, []),
            f"planning_context.{field_name}",
        )
    exposed_belief_ids = value.get("tool_exposed_belief_ids")
    if exposed_belief_ids is not None:
        _string_tuple(
            exposed_belief_ids,
            "planning_context.tool_exposed_belief_ids",
        )
    _string_tuple(
        value.get("active_observation_capabilities", []),
        "planning_context.active_observation_capabilities",
    )


def _planning_statuses(value: Any) -> tuple[str, ...]:
    return _string_tuple(
        value,
        "expected_planning_statuses",
        allowed=PLANNING_STATUSES,
    )


def _mapping_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise ValueError(f"{field_name} must be an array of objects")
    return value


def _string_tuple(
    value: Any,
    field_name: str,
    *,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field_name} must be an array of non-empty strings")
    normalized = tuple(dict.fromkeys(item.strip() for item in value))
    if len(normalized) != len(value):
        raise ValueError(f"{field_name} must not contain duplicates")
    if allowed is not None:
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise ValueError(
                f"{field_name} contains unsupported values: {unknown}"
            )
    return normalized


def _terminal_outcomes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("expected_terminal_outcomes must be a non-empty array")
    normalized: list[str] = []
    for raw in value:
        outcome = normalize_robot_task_terminal_status(raw)
        if outcome is None or outcome not in ROBOT_TASK_TERMINAL_STATUSES:
            raise ValueError(
                "expected_terminal_outcomes contains a non-canonical status: "
                f"{raw!r}"
            )
        if outcome not in normalized:
            normalized.append(outcome)
    return tuple(normalized)


def _seeds(value: Mapping[str, Any]) -> tuple[int, ...]:
    if "seeds" in value and "seed" in value:
        raise ValueError("scenario must provide seed or seeds, not both")
    raw = value.get("seeds", [value.get("seed", 0)])
    if not isinstance(raw, list) or not raw:
        raise ValueError("seeds must be a non-empty array")
    seeds = tuple(_nonnegative_int(seed, "seed") for seed in raw)
    if len(seeds) != len(set(seeds)):
        raise ValueError("seeds must not contain duplicates")
    return seeds


def _validated_split(value: Any) -> str:
    split = str(value or "").strip()
    if split not in DATASET_SPLITS:
        raise ValueError(f"split must be one of {sorted(DATASET_SPLITS)}")
    return split


def _required_string(value: Mapping[str, Any], field_name: str) -> str:
    candidate = value.get(field_name)
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return candidate.strip()


def _validated_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} must match {_ID_RE.pattern!r}"
        )
    return value


def _nonnegative_int(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _optional_boolean(value: Any, field_name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean or null")


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string or null")
    return value.strip()


def _optional_nonnegative_int(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field_name, minimum=minimum)


def _validated_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} timestamp must include a timezone")
    return candidate


def _reject_unknown(
    value: Mapping[str, Any],
    allowed: frozenset[str],
    description: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"{description} contains unknown fields: {', '.join(unknown)}"
        )


def _safe_legacy_suite_id(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return candidate[:128] if candidate else "legacy-suite"


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


__all__ = [
    "DATASET_SPLITS",
    "EVALUATION_LANES",
    "EVALUATION_RUN_SCHEMA_VERSION",
    "EvaluationScenario",
    "EvaluationSuite",
    "PLANNING_OPERATIONS",
    "PLANNING_STATUSES",
    "SCENARIO_SUITE_SCHEMA_VERSION",
    "TARGET_TYPES",
    "load_evaluation_suite",
]
