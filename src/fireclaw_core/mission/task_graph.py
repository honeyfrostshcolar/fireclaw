from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fireclaw_core.agent.robot_registry import RobotRegistry

if TYPE_CHECKING:
    from fireclaw_core.mission.mission_planner import MissionPlan


VALID_TASK_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
VALID_RECOVERY_POLICIES = frozenset({"retry", "reassign", "replan", "escalate", "abort"})
VALID_BELIEF_REQUIREMENT_STATUSES = frozenset({"confirmed"})
VALID_BELIEF_REQUIREMENT_ORIGINS = frozenset({
    "planner_declared",
    "authoritative_rule",
    "planner_and_authoritative_rule",
})


@dataclass(frozen=True)
class MissionTarget:
    """A task target expressed in an explicit spatial frame."""

    frame_id: str
    floor: int | None = None
    area_id: str | None = None
    entity_id: str | None = None
    pose: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"frame_id": self.frame_id}
        if self.floor is not None:
            result["floor"] = self.floor
        if self.area_id is not None:
            result["area_id"] = self.area_id
        if self.entity_id is not None:
            result["entity_id"] = self.entity_id
        if self.pose is not None:
            result["pose"] = dict(self.pose)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionTarget:
        if not isinstance(value, dict):
            raise ValueError("Mission target must be an object.")
        pose_value = value.get("pose")
        if pose_value is not None:
            if not isinstance(pose_value, dict):
                raise ValueError("Mission target pose must be an object.")
            pose = {
                str(axis): float(coordinate)
                for axis, coordinate in pose_value.items()
                if isinstance(axis, str)
                and isinstance(coordinate, (int, float))
                and not isinstance(coordinate, bool)
            }
            if len(pose) != len(pose_value):
                raise ValueError("Mission target pose coordinates must be numeric.")
        else:
            pose = None
        floor_value = value.get("floor")
        if (
            floor_value is not None
            and (
                not isinstance(floor_value, int)
                or isinstance(floor_value, bool)
            )
        ):
            raise ValueError("Mission target floor must be an integer.")
        return cls(
            frame_id=_required_string(value, "frame_id"),
            floor=floor_value,
            area_id=_optional_string(value.get("area_id"), "area_id"),
            entity_id=_optional_string(value.get("entity_id"), "entity_id"),
            pose=pose,
        )


@dataclass(frozen=True)
class MissionCondition:
    """A deterministic condition that must hold before a task starts."""

    kind: str
    subject: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "details": dict(self.details)}
        if self.subject is not None:
            result["subject"] = self.subject
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionCondition:
        if not isinstance(value, dict):
            raise ValueError("Mission condition must be an object.")
        details = value.get("details", {})
        if not isinstance(details, dict):
            raise ValueError("Mission condition details must be an object.")
        return cls(
            kind=_required_string(value, "kind"),
            subject=_optional_string(value.get("subject"), "subject"),
            details=dict(details),
        )


@dataclass(frozen=True)
class MissionEvidenceRequirement:
    """Evidence needed to declare a task complete or safe to continue."""

    kind: str
    source: str
    max_age_seconds: float | None = None
    criteria: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "source": self.source,
            "criteria": dict(self.criteria),
        }
        if self.max_age_seconds is not None:
            result["max_age_seconds"] = self.max_age_seconds
        return result

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> MissionEvidenceRequirement:
        if not isinstance(value, dict):
            raise ValueError("Mission evidence requirement must be an object.")
        max_age = value.get("max_age_seconds")
        if (
            max_age is not None
            and (
                not isinstance(max_age, (int, float))
                or isinstance(max_age, bool)
            )
        ):
            raise ValueError("Mission evidence max_age_seconds must be numeric.")
        criteria = value.get("criteria", {})
        if not isinstance(criteria, dict):
            raise ValueError("Mission evidence criteria must be an object.")
        return cls(
            kind=_required_string(value, "kind"),
            source=_required_string(value, "source"),
            max_age_seconds=None if max_age is None else float(max_age),
            criteria=dict(criteria),
        )


@dataclass(frozen=True)
class MissionBeliefRequirement:
    """A compiled world-state assumption that must hold at dispatch time."""

    belief_id: str
    subject_id: str
    kind: str
    expected_value: str | int | float | bool | None
    required_status: str = "confirmed"
    minimum_confidence: float = 0.8
    maximum_age_seconds: float = 15.0
    planning_snapshot_id: str | None = None
    planning_evidence_ids: tuple[str, ...] = ()
    origin: str = "planner_declared"
    source_rule_ids: tuple[str, ...] = ()
    source_knowledge_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "belief_id": self.belief_id,
            "subject_id": self.subject_id,
            "kind": self.kind,
            "expected_value": self.expected_value,
            "required_status": self.required_status,
            "minimum_confidence": self.minimum_confidence,
            "maximum_age_seconds": self.maximum_age_seconds,
            "planning_evidence_ids": list(self.planning_evidence_ids),
            "origin": self.origin,
            "source_rule_ids": list(self.source_rule_ids),
            "source_knowledge_ids": list(self.source_knowledge_ids),
        }
        if self.planning_snapshot_id is not None:
            result["planning_snapshot_id"] = self.planning_snapshot_id
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionBeliefRequirement:
        if not isinstance(value, dict):
            raise ValueError("Mission belief requirement must be an object.")
        minimum_confidence = value.get("minimum_confidence", 0.8)
        maximum_age_seconds = value.get("maximum_age_seconds", 15.0)
        if (
            not isinstance(minimum_confidence, (int, float))
            or isinstance(minimum_confidence, bool)
        ):
            raise ValueError(
                "Mission belief requirement minimum_confidence must be numeric."
            )
        if (
            not isinstance(maximum_age_seconds, (int, float))
            or isinstance(maximum_age_seconds, bool)
        ):
            raise ValueError(
                "Mission belief requirement maximum_age_seconds must be numeric."
            )
        expected_value = value.get("expected_value")
        if not _is_scalar(expected_value):
            raise ValueError(
                "Mission belief requirement expected_value must be scalar."
            )
        return cls(
            belief_id=_required_string(value, "belief_id"),
            subject_id=_required_string(value, "subject_id"),
            kind=_required_string(value, "kind"),
            expected_value=expected_value,
            required_status=str(value.get("required_status") or "confirmed"),
            minimum_confidence=float(minimum_confidence),
            maximum_age_seconds=float(maximum_age_seconds),
            planning_snapshot_id=_optional_string(
                value.get("planning_snapshot_id"),
                "planning_snapshot_id",
            ),
            planning_evidence_ids=_string_tuple(
                value,
                "planning_evidence_ids",
            ),
            origin=str(value.get("origin") or "planner_declared"),
            source_rule_ids=_string_tuple(value, "source_rule_ids"),
            source_knowledge_ids=_string_tuple(
                value,
                "source_knowledge_ids",
            ),
        )


@dataclass(frozen=True)
class MissionTaskNode:
    """One bounded robot task in a mission dependency graph."""

    node_id: str
    robot_id: str
    command: str
    capability_required: str
    target: MissionTarget
    task_type: str | None = None
    completion_goal: str | None = None
    depends_on: tuple[str, ...] = ()
    preconditions: tuple[MissionCondition, ...] = ()
    belief_requirements: tuple[MissionBeliefRequirement, ...] = ()
    expected_effects: tuple[str, ...] = ()
    success_evidence: tuple[MissionEvidenceRequirement, ...] = ()
    risk_level: str = "low"
    exclusive_resources: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    recovery_policy: str = "escalate"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "node_id": self.node_id,
            "robot_id": self.robot_id,
            "command": self.command,
            "capability_required": self.capability_required,
            "target": self.target.to_dict(),
            "depends_on": list(self.depends_on),
            "preconditions": [item.to_dict() for item in self.preconditions],
            "belief_requirements": [
                item.to_dict() for item in self.belief_requirements
            ],
            "expected_effects": list(self.expected_effects),
            "success_evidence": [item.to_dict() for item in self.success_evidence],
            "risk_level": self.risk_level,
            "exclusive_resources": list(self.exclusive_resources),
            "recovery_policy": self.recovery_policy,
        }
        if self.task_type is not None:
            result["task_type"] = self.task_type
        if self.completion_goal is not None:
            result["completion_goal"] = self.completion_goal
        if self.timeout_seconds is not None:
            result["timeout_seconds"] = self.timeout_seconds
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionTaskNode:
        if not isinstance(value, dict):
            raise ValueError("Mission task node must be an object.")
        target = value.get("target")
        if not isinstance(target, dict):
            raise ValueError("Mission task node target must be an object.")
        timeout = value.get("timeout_seconds")
        if (
            timeout is not None
            and (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
            )
        ):
            raise ValueError("Mission task timeout_seconds must be numeric.")
        return cls(
            node_id=_required_string(value, "node_id"),
            robot_id=_required_string(value, "robot_id"),
            command=_required_string(value, "command"),
            capability_required=_required_string(
                value,
                "capability_required",
            ),
            target=MissionTarget.from_dict(target),
            task_type=_optional_string(value.get("task_type"), "task_type"),
            completion_goal=_optional_string(
                value.get("completion_goal"),
                "completion_goal",
            ),
            depends_on=_string_tuple(value, "depends_on"),
            preconditions=tuple(
                MissionCondition.from_dict(item)
                for item in _object_list(value, "preconditions")
            ),
            belief_requirements=tuple(
                MissionBeliefRequirement.from_dict(item)
                for item in _object_list(value, "belief_requirements")
            ),
            expected_effects=_string_tuple(value, "expected_effects"),
            success_evidence=tuple(
                MissionEvidenceRequirement.from_dict(item)
                for item in _object_list(value, "success_evidence")
            ),
            risk_level=str(value.get("risk_level") or "low"),
            exclusive_resources=_string_tuple(
                value,
                "exclusive_resources",
            ),
            timeout_seconds=None if timeout is None else float(timeout),
            recovery_policy=str(
                value.get("recovery_policy") or "escalate"
            ),
        )


@dataclass(frozen=True)
class MissionTaskGraph:
    """A revisioned, evidence-bound mission plan.

    The graph is the planner's cognitive artifact. Robot task envelopes and
    SafetyGate remain the authority for actual physical execution.
    """

    plan_id: str
    mission_id: str
    intent: str
    command: str
    nodes: tuple[MissionTaskNode, ...]
    revision: int = 1
    state_snapshot_id: str | None = None
    supersedes_plan_id: str | None = None
    invalidation_evidence_ids: tuple[str, ...] = ()
    knowledge_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plan_id": self.plan_id,
            "mission_id": self.mission_id,
            "intent": self.intent,
            "command": self.command,
            "revision": self.revision,
            "nodes": [node.to_dict() for node in self.nodes],
            "invalidation_evidence_ids": list(self.invalidation_evidence_ids),
            "knowledge_refs": list(self.knowledge_refs),
        }
        if self.state_snapshot_id is not None:
            result["state_snapshot_id"] = self.state_snapshot_id
        if self.supersedes_plan_id is not None:
            result["supersedes_plan_id"] = self.supersedes_plan_id
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionTaskGraph:
        if not isinstance(value, dict):
            raise ValueError("Mission task graph must be an object.")
        revision = value.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError("Mission task graph revision must be an integer.")
        return cls(
            plan_id=_required_string(value, "plan_id"),
            mission_id=_required_string(value, "mission_id"),
            intent=_required_string(value, "intent"),
            command=_required_string(value, "command"),
            nodes=tuple(
                MissionTaskNode.from_dict(item)
                for item in _object_list(value, "nodes")
            ),
            revision=revision,
            state_snapshot_id=_optional_string(
                value.get("state_snapshot_id"),
                "state_snapshot_id",
            ),
            supersedes_plan_id=_optional_string(
                value.get("supersedes_plan_id"),
                "supersedes_plan_id",
            ),
            invalidation_evidence_ids=_string_tuple(
                value,
                "invalidation_evidence_ids",
            ),
            knowledge_refs=_string_tuple(value, "knowledge_refs"),
        )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"Mission task graph field {key!r} must be a non-empty string.")
    return item


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Mission task graph field {field_name!r} must be a non-empty string."
        )
    return value


def _string_tuple(value: dict[str, Any], key: str) -> tuple[str, ...]:
    items = value.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"Mission task graph field {key!r} must be a list.")
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(
            f"Mission task graph field {key!r} must contain non-empty strings."
        )
    return tuple(items)


def _object_list(value: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = value.get(key, [])
    if not isinstance(items, list) or any(
        not isinstance(item, dict) for item in items
    ):
        raise ValueError(
            f"Mission task graph field {key!r} must contain objects."
        )
    return items


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def task_graph_from_mission_plan(
    plan: MissionPlan,
    *,
    mission_id: str,
    plan_id: str,
    state_snapshot_id: str | None = None,
    revision: int = 1,
    supersedes_plan_id: str | None = None,
    invalidation_evidence_ids: tuple[str, ...] = (),
) -> MissionTaskGraph:
    """Project the legacy execution-group plan into an equivalent DAG.

    Every task in group N depends on all tasks in earlier groups. This exactly
    preserves the current scheduler's group barrier while making dependencies
    explicit for future re-planning.
    """

    node_ids_by_group: dict[int, list[str]] = {}
    node_specs: list[tuple[str, Any]] = []
    for index, subtask in enumerate(plan.subtasks, start=1):
        node_id = subtask.node_id or f"task-{index}"
        node_ids_by_group.setdefault(subtask.execution_group, []).append(node_id)
        node_specs.append((node_id, subtask))

    nodes: list[MissionTaskNode] = []
    for node_id, subtask in node_specs:
        dependencies = tuple(
            previous_node_id
            for group, previous_node_ids in sorted(node_ids_by_group.items())
            if group < subtask.execution_group
            for previous_node_id in previous_node_ids
        )
        completion_contract = subtask.completion_contract
        raw_success_evidence = completion_contract.get("success_evidence")
        if completion_contract:
            if not isinstance(raw_success_evidence, list) or not raw_success_evidence:
                raise ValueError(
                    f"Mission subtask {node_id!r} has an empty completion contract."
                )
            if completion_contract.get("task_type") != subtask.task_type:
                raise ValueError(
                    f"Mission subtask {node_id!r} completion task type does not match."
                )
            if (
                completion_contract.get("completion_goal")
                != subtask.completion_goal
            ):
                raise ValueError(
                    f"Mission subtask {node_id!r} completion goal does not match."
                )
        success_evidence = (
            tuple(
                MissionEvidenceRequirement.from_dict(item)
                for item in raw_success_evidence
            )
            if isinstance(raw_success_evidence, list)
            else (
                MissionEvidenceRequirement(
                    kind="task_terminal_success",
                    source="execution_monitor",
                ),
            )
        )
        timeout_seconds = completion_contract.get("timeout_seconds", 300.0)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError(
                f"Mission subtask {node_id!r} has invalid completion timeout."
            )
        recovery_policy = completion_contract.get("recovery_policy", "reassign")
        risk_level = completion_contract.get("risk_level", "low")
        if not isinstance(recovery_policy, str) or not recovery_policy.strip():
            raise ValueError(
                f"Mission subtask {node_id!r} has invalid recovery policy."
            )
        if not isinstance(risk_level, str) or not risk_level.strip():
            raise ValueError(
                f"Mission subtask {node_id!r} has invalid risk level."
            )
        nodes.append(
            MissionTaskNode(
                node_id=node_id,
                robot_id=subtask.robot_id,
                command=subtask.command,
                capability_required=subtask.capability_required,
                target=(
                    MissionTarget.from_dict(subtask.target)
                    if subtask.target
                    else MissionTarget(
                        frame_id="building",
                        floor=subtask.floor,
                    )
                ),
                task_type=subtask.task_type,
                completion_goal=subtask.completion_goal,
                depends_on=dependencies,
                preconditions=(
                    MissionCondition(kind="robot_enabled", subject=subtask.robot_id),
                    MissionCondition(
                        kind="robot_has_capability",
                        subject=subtask.robot_id,
                        details={"capability": subtask.capability_required},
                    ),
                ),
                belief_requirements=tuple(
                    MissionBeliefRequirement.from_dict(item)
                    for item in subtask.belief_requirements
                ),
                expected_effects=(
                    (
                        f"completion_goal:{subtask.completion_goal}",
                    )
                    if subtask.completion_goal is not None
                    else (f"task_completed:{node_id}",)
                ),
                success_evidence=success_evidence,
                exclusive_resources=(f"robot:{subtask.robot_id}",),
                risk_level=risk_level,
                timeout_seconds=float(timeout_seconds),
                recovery_policy=recovery_policy,
            )
        )
    return MissionTaskGraph(
        plan_id=plan_id,
        mission_id=mission_id,
        intent=plan.intent,
        command=plan.command,
        nodes=tuple(nodes),
        revision=revision,
        state_snapshot_id=state_snapshot_id,
        supersedes_plan_id=supersedes_plan_id,
        invalidation_evidence_ids=invalidation_evidence_ids,
        knowledge_refs=tuple(plan.knowledge_refs),
    )


class MissionTaskGraphValidator:
    """Validate graph semantics before a task graph reaches scheduling."""

    def validate(self, graph: MissionTaskGraph, registry: RobotRegistry) -> list[str]:
        errors: list[str] = []
        if not graph.plan_id:
            errors.append("Mission task graph plan_id must not be empty.")
        if not graph.mission_id:
            errors.append("Mission task graph mission_id must not be empty.")
        if not graph.intent:
            errors.append("Mission task graph intent must not be empty.")
        if not graph.command:
            errors.append("Mission task graph command must not be empty.")
        if not graph.nodes:
            errors.append("Mission task graph must contain at least one node.")
        if graph.revision < 1:
            errors.append("Mission task graph revision must be at least 1.")
        if graph.revision > 1:
            if not graph.state_snapshot_id:
                errors.append("Revised mission task graph requires state_snapshot_id.")
            if not graph.supersedes_plan_id:
                errors.append("Revised mission task graph requires supersedes_plan_id.")
            if not graph.invalidation_evidence_ids:
                errors.append("Revised mission task graph requires invalidation evidence.")
        if graph.supersedes_plan_id == graph.plan_id and graph.supersedes_plan_id:
            errors.append("Mission task graph cannot supersede itself.")

        nodes_by_id: dict[str, MissionTaskNode] = {}
        for node in graph.nodes:
            if not node.node_id:
                errors.append("Mission task node_id must not be empty.")
                continue
            if node.node_id in nodes_by_id:
                errors.append(f"Mission task graph has duplicate node_id {node.node_id!r}.")
                continue
            nodes_by_id[node.node_id] = node

        for node in graph.nodes:
            errors.extend(self._validate_node(node, registry))
            for dependency_id in node.depends_on:
                if dependency_id == node.node_id:
                    errors.append(f"Mission task node {node.node_id!r} cannot depend on itself.")
                elif dependency_id not in nodes_by_id:
                    errors.append(
                        f"Mission task node {node.node_id!r} depends on unknown node {dependency_id!r}."
                    )

        errors.extend(self._cycle_errors(nodes_by_id))
        errors.extend(self._resource_conflict_errors(nodes_by_id))
        return list(dict.fromkeys(errors))

    def _validate_node(self, node: MissionTaskNode, registry: RobotRegistry) -> list[str]:
        errors: list[str] = []
        if not node.robot_id:
            errors.append(f"Mission task node {node.node_id!r} robot_id must not be empty.")
        if not node.command:
            errors.append(f"Mission task node {node.node_id!r} command must not be empty.")
        if not node.capability_required:
            errors.append(f"Mission task node {node.node_id!r} capability_required must not be empty.")
        if not node.target.frame_id:
            errors.append(f"Mission task node {node.node_id!r} target.frame_id must not be empty.")
        if node.target.floor is not None and node.target.floor <= 0:
            errors.append(f"Mission task node {node.node_id!r} target.floor must be positive.")
        if node.target.pose is not None:
            for axis in ("x", "y"):
                if axis not in node.target.pose:
                    errors.append(
                        f"Mission task node {node.node_id!r} target.pose requires {axis!r}."
                    )
        if node.risk_level not in VALID_TASK_RISK_LEVELS:
            errors.append(f"Mission task node {node.node_id!r} has invalid risk_level.")
        if node.recovery_policy not in VALID_RECOVERY_POLICIES:
            errors.append(f"Mission task node {node.node_id!r} has invalid recovery_policy.")
        if node.timeout_seconds is not None and node.timeout_seconds <= 0:
            errors.append(f"Mission task node {node.node_id!r} timeout_seconds must be positive.")
        if not node.preconditions:
            errors.append(f"Mission task node {node.node_id!r} requires preconditions.")
        if not node.success_evidence:
            errors.append(f"Mission task node {node.node_id!r} requires success evidence.")

        entry = registry.get(node.robot_id)
        if entry is None:
            errors.append(f"Robot {node.robot_id} is not registered.")
        else:
            if not entry.enabled:
                errors.append(f"Robot {node.robot_id} is disabled.")
            if node.capability_required not in entry.capabilities:
                errors.append(
                    f"Robot {node.robot_id} lacks required capability {node.capability_required}."
                )

        precondition_kinds = {condition.kind for condition in node.preconditions}
        if "robot_enabled" not in precondition_kinds:
            errors.append(f"Mission task node {node.node_id!r} requires robot_enabled precondition.")
        if "robot_has_capability" not in precondition_kinds:
            errors.append(
                f"Mission task node {node.node_id!r} requires robot_has_capability precondition."
            )
        for condition in node.preconditions:
            if not condition.kind:
                errors.append(f"Mission task node {node.node_id!r} has an empty precondition kind.")
            if condition.kind == "robot_enabled" and condition.subject != node.robot_id:
                errors.append(
                    f"Mission task node {node.node_id!r} robot_enabled precondition must name its robot."
                )
            if condition.kind == "robot_has_capability":
                if condition.subject != node.robot_id:
                    errors.append(
                        f"Mission task node {node.node_id!r} capability precondition must name its robot."
                    )
                if condition.details.get("capability") != node.capability_required:
                    errors.append(
                        f"Mission task node {node.node_id!r} capability precondition must match capability_required."
                    )
        belief_ids: set[str] = set()
        for requirement in node.belief_requirements:
            if requirement.belief_id in belief_ids:
                errors.append(
                    f"Mission task node {node.node_id!r} has duplicate belief requirement "
                    f"{requirement.belief_id!r}."
                )
            belief_ids.add(requirement.belief_id)
            if (
                requirement.required_status
                not in VALID_BELIEF_REQUIREMENT_STATUSES
            ):
                errors.append(
                    f"Mission task node {node.node_id!r} has invalid belief "
                    "required_status."
                )
            if not 0.8 <= requirement.minimum_confidence <= 1.0:
                errors.append(
                    f"Mission task node {node.node_id!r} belief minimum_confidence "
                    "must be between 0.8 and 1.0."
                )
            if not 0 < requirement.maximum_age_seconds <= 30.0:
                errors.append(
                    f"Mission task node {node.node_id!r} belief maximum_age_seconds "
                    "must be greater than 0 and at most 30."
                )
            if not requirement.planning_snapshot_id:
                errors.append(
                    f"Mission task node {node.node_id!r} belief requirement must "
                    "reference its planning snapshot."
                )
            if not requirement.planning_evidence_ids:
                errors.append(
                    f"Mission task node {node.node_id!r} belief requirement must "
                    "retain planning evidence."
                )
            if requirement.origin not in VALID_BELIEF_REQUIREMENT_ORIGINS:
                errors.append(
                    f"Mission task node {node.node_id!r} belief requirement "
                    "has invalid origin."
                )
            if (
                requirement.origin
                in {"authoritative_rule", "planner_and_authoritative_rule"}
                and not requirement.source_rule_ids
            ):
                errors.append(
                    f"Mission task node {node.node_id!r} authoritative belief "
                    "requirement must retain source_rule_ids."
                )
        for evidence in node.success_evidence:
            if not evidence.kind or not evidence.source:
                errors.append(f"Mission task node {node.node_id!r} has incomplete success evidence.")
            if not isinstance(evidence.criteria, dict):
                errors.append(
                    f"Mission task node {node.node_id!r} evidence criteria must be an object."
                )
            if evidence.max_age_seconds is not None and evidence.max_age_seconds <= 0:
                errors.append(
                    f"Mission task node {node.node_id!r} evidence max_age_seconds must be positive."
                )
        if node.risk_level in {"high", "critical"} and not any(
            evidence.kind == "sensor_observation" for evidence in node.success_evidence
        ):
            errors.append(
                f"Mission task node {node.node_id!r} requires sensor_observation evidence at risk {node.risk_level}."
            )
        return errors

    def _cycle_errors(self, nodes_by_id: dict[str, MissionTaskNode]) -> list[str]:
        visiting: set[str] = set()
        visited: set[str] = set()
        errors: list[str] = []

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                errors.append(f"Mission task graph contains a dependency cycle at {node_id!r}.")
                return
            visiting.add(node_id)
            for dependency_id in nodes_by_id[node_id].depends_on:
                if dependency_id in nodes_by_id:
                    visit(dependency_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in nodes_by_id:
            visit(node_id)
        return errors

    def _resource_conflict_errors(self, nodes_by_id: dict[str, MissionTaskNode]) -> list[str]:
        errors: list[str] = []
        node_ids = sorted(nodes_by_id)
        for index, left_id in enumerate(node_ids):
            left = nodes_by_id[left_id]
            for right_id in node_ids[index + 1 :]:
                right = nodes_by_id[right_id]
                shared = sorted(set(left.exclusive_resources) & set(right.exclusive_resources))
                if shared and not self._ordered(left_id, right_id, nodes_by_id):
                    errors.append(
                        "Mission task nodes "
                        f"{left_id!r} and {right_id!r} conflict on exclusive resources {shared}."
                    )
        return errors

    def _ordered(
        self,
        left_id: str,
        right_id: str,
        nodes_by_id: dict[str, MissionTaskNode],
    ) -> bool:
        return self._depends_on(left_id, right_id, nodes_by_id) or self._depends_on(
            right_id, left_id, nodes_by_id
        )

    def _depends_on(
        self,
        node_id: str,
        ancestor_id: str,
        nodes_by_id: dict[str, MissionTaskNode],
    ) -> bool:
        pending = list(nodes_by_id[node_id].depends_on)
        seen: set[str] = set()
        while pending:
            dependency_id = pending.pop()
            if dependency_id == ancestor_id:
                return True
            if dependency_id in seen or dependency_id not in nodes_by_id:
                continue
            seen.add(dependency_id)
            pending.extend(nodes_by_id[dependency_id].depends_on)
        return False
