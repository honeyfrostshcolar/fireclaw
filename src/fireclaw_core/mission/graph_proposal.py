from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.mission.completion_contract import (
    CompletionContractCompiler,
    CompletionContractError,
    TaskTypeRegistry,
)
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_state import (
    MissionRobotState,
    MissionStateSnapshot,
)
from fireclaw_core.mission.task_assumption import (
    TaskAssumptionRegistry,
    default_task_assumption_registry,
)
from fireclaw_core.mission.task_graph import (
    MissionBeliefRequirement,
    MissionCondition,
    MissionTarget,
    MissionTaskGraph,
    MissionTaskNode,
)


VALID_PROPOSAL_EXECUTION_MODES = frozenset({"parallel", "sequential"})
_NODE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_PROPOSAL_NODES = 32
_MAX_TEXT_LENGTH = 512
_BUSY_RESERVATION_STATUSES = frozenset({"active", "acquired", "held", "reserved"})
_MINIMUM_BELIEF_CONFIDENCE = 0.8
_MAXIMUM_BELIEF_AGE_SECONDS = 15.0


@dataclass(frozen=True)
class MissionGraphBeliefAssumption:
    """Planner-declared world belief and the value required by one node."""

    belief_id: str
    expected_value: str | int | float | bool | None
    knowledge_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id,
            "expected_value": self.expected_value,
            "knowledge_refs": list(self.knowledge_refs),
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> MissionGraphBeliefAssumption:
        if not isinstance(value, dict):
            raise ValueError(
                "Mission graph belief assumption must be an object."
            )
        expected_value = value.get("expected_value")
        if not _is_scalar(expected_value):
            raise ValueError(
                "Mission graph belief assumption expected_value must be scalar."
            )
        raw_refs = value.get("knowledge_refs", [])
        if not isinstance(raw_refs, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in raw_refs
        ):
            raise ValueError(
                "Mission graph belief assumption knowledge_refs must contain "
                "non-empty strings."
            )
        return cls(
            belief_id=_required_string(value, "belief_id"),
            expected_value=expected_value,
            knowledge_refs=tuple(dict.fromkeys(raw_refs)),
        )


@dataclass(frozen=True)
class MissionGraphProposalNode:
    """One semantic task requested by the planner, before robot allocation."""

    node_id: str
    task_type: str
    command: str
    target: MissionTarget
    capability_required: str
    completion_goal: str
    depends_on: tuple[str, ...] = ()
    execution_mode: str = "parallel"
    belief_assumptions: tuple[MissionGraphBeliefAssumption, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "task_type": self.task_type,
            "command": self.command,
            "target": self.target.to_dict(),
            "capability_required": self.capability_required,
            "completion_goal": self.completion_goal,
            "depends_on": list(self.depends_on),
            "execution_mode": self.execution_mode,
            "belief_assumptions": [
                item.to_dict() for item in self.belief_assumptions
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionGraphProposalNode:
        if not isinstance(value, dict):
            raise ValueError("Mission graph proposal node must be an object.")
        target = value.get("target")
        if not isinstance(target, dict):
            raise ValueError("Mission graph proposal node target must be an object.")
        dependencies = value.get("depends_on", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in dependencies
        ):
            raise ValueError(
                "Mission graph proposal node depends_on must contain non-empty strings."
            )
        raw_assumptions = value.get("belief_assumptions", [])
        if not isinstance(raw_assumptions, list) or any(
            not isinstance(item, dict) for item in raw_assumptions
        ):
            raise ValueError(
                "Mission graph proposal node belief_assumptions must contain objects."
            )
        return cls(
            node_id=_required_string(value, "node_id"),
            task_type=_required_string(value, "task_type"),
            command=_required_string(value, "command"),
            target=MissionTarget.from_dict(target),
            capability_required=_required_string(value, "capability_required"),
            completion_goal=_required_string(value, "completion_goal"),
            depends_on=tuple(dependencies),
            execution_mode=str(value.get("execution_mode") or "parallel"),
            belief_assumptions=tuple(
                MissionGraphBeliefAssumption.from_dict(item)
                for item in raw_assumptions
            ),
        )


@dataclass(frozen=True)
class MissionGraphProposal:
    """A non-executable semantic graph produced by a planner policy."""

    intent: str
    command: str
    nodes: tuple[MissionGraphProposalNode, ...]
    knowledge_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "command": self.command,
            "nodes": [node.to_dict() for node in self.nodes],
            "knowledge_refs": list(self.knowledge_refs),
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
        *,
        command: str | None = None,
    ) -> MissionGraphProposal:
        if not isinstance(value, dict):
            raise ValueError("Mission graph proposal must be an object.")
        raw_nodes = value.get("nodes")
        if not isinstance(raw_nodes, list) or any(
            not isinstance(item, dict) for item in raw_nodes
        ):
            raise ValueError("Mission graph proposal nodes must contain objects.")
        raw_refs = value.get("knowledge_refs", [])
        if not isinstance(raw_refs, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_refs
        ):
            raise ValueError(
                "Mission graph proposal knowledge_refs must contain non-empty strings."
            )
        proposal_command = command or _required_string(value, "command")
        return cls(
            intent=_required_string(value, "intent"),
            command=proposal_command,
            nodes=tuple(
                MissionGraphProposalNode.from_dict(item) for item in raw_nodes
            ),
            knowledge_refs=tuple(dict.fromkeys(raw_refs)),
        )


class MissionGraphProposalValidator:
    """Validate planner-authored semantics before allocation or safety injection."""

    def validate(self, proposal: MissionGraphProposal) -> list[str]:
        errors: list[str] = []
        if not proposal.intent.strip():
            errors.append("Mission graph proposal intent must not be empty.")
        if not proposal.command.strip():
            errors.append("Mission graph proposal command must not be empty.")
        if not proposal.nodes:
            errors.append("Mission graph proposal must contain at least one node.")
        if len(proposal.nodes) > _MAX_PROPOSAL_NODES:
            errors.append(
                f"Mission graph proposal exceeds {_MAX_PROPOSAL_NODES} nodes."
            )

        positions: dict[str, int] = {}
        for index, node in enumerate(proposal.nodes):
            if not _NODE_ID_PATTERN.fullmatch(node.node_id):
                errors.append(
                    f"Mission graph proposal node_id {node.node_id!r} must match "
                    "[a-z][a-z0-9_]{0,63}."
                )
            elif node.node_id in positions:
                errors.append(
                    f"Mission graph proposal has duplicate node_id {node.node_id!r}."
                )
            else:
                positions[node.node_id] = index
            for field_name, text in (
                ("task_type", node.task_type),
                ("command", node.command),
                ("capability_required", node.capability_required),
                ("completion_goal", node.completion_goal),
            ):
                if not text.strip():
                    errors.append(
                        f"Mission graph proposal node {node.node_id!r} "
                        f"{field_name} must not be empty."
                    )
                elif len(text) > _MAX_TEXT_LENGTH:
                    errors.append(
                        f"Mission graph proposal node {node.node_id!r} "
                        f"{field_name} exceeds {_MAX_TEXT_LENGTH} characters."
                    )
            if node.execution_mode not in VALID_PROPOSAL_EXECUTION_MODES:
                errors.append(
                    f"Mission graph proposal node {node.node_id!r} has invalid "
                    "execution_mode."
                )
            assumption_ids = [
                assumption.belief_id
                for assumption in node.belief_assumptions
            ]
            if len(assumption_ids) != len(set(assumption_ids)):
                errors.append(
                    f"Mission graph proposal node {node.node_id!r} has "
                    "duplicate belief assumptions."
                )
            target = node.target
            if not target.frame_id.strip():
                errors.append(
                    f"Mission graph proposal node {node.node_id!r} target.frame_id "
                    "must not be empty."
                )
            if (
                target.floor is None
                and target.area_id is None
                and target.entity_id is None
                and target.pose is None
            ):
                errors.append(
                    f"Mission graph proposal node {node.node_id!r} requires a "
                    "floor, area_id, entity_id, or pose target."
                )
            if target.floor is not None and target.floor <= 0:
                errors.append(
                    f"Mission graph proposal node {node.node_id!r} target.floor "
                    "must be positive."
                )
            if target.pose is not None and not {"x", "y"}.issubset(target.pose):
                errors.append(
                    f"Mission graph proposal node {node.node_id!r} target.pose "
                    "requires x and y."
                )

        for index, node in enumerate(proposal.nodes):
            for dependency_id in node.depends_on:
                dependency_position = positions.get(dependency_id)
                if dependency_position is None:
                    errors.append(
                        f"Mission graph proposal node {node.node_id!r} depends on "
                        f"unknown node {dependency_id!r}."
                    )
                elif dependency_id == node.node_id:
                    errors.append(
                        f"Mission graph proposal node {node.node_id!r} cannot "
                        "depend on itself."
                    )
                elif dependency_position >= index:
                    errors.append(
                        f"Mission graph proposal node {node.node_id!r} must only "
                        "depend on earlier declared nodes."
                    )
        return list(dict.fromkeys(errors))


class MissionGraphCompilationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(dict.fromkeys(errors))
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class CompiledMissionGraph:
    proposal: MissionGraphProposal
    plan: MissionPlan
    task_graph: MissionTaskGraph


class MissionGraphCompiler:
    """Compile semantic goals into an allocated, validator-ready task graph."""

    def __init__(
        self,
        registry: RobotRegistry,
        *,
        task_type_registry: TaskTypeRegistry | None = None,
        task_assumption_registry: TaskAssumptionRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.completion_contract_compiler = CompletionContractCompiler(
            task_type_registry
        )
        self.task_assumption_registry = (
            task_assumption_registry
            or default_task_assumption_registry()
        )

    def compile(
        self,
        proposal: MissionGraphProposal,
        *,
        state_snapshot: MissionStateSnapshot,
        mission_id: str,
        plan_id: str,
        revision: int = 1,
        supersedes_plan_id: str | None = None,
        invalidation_evidence_ids: tuple[str, ...] = (),
    ) -> CompiledMissionGraph:
        errors = MissionGraphProposalValidator().validate(proposal)
        if state_snapshot.mission_id != mission_id:
            errors.append(
                "Mission graph proposal snapshot does not belong to the mission."
            )
        if errors:
            raise MissionGraphCompilationError(errors)

        state_by_robot = {
            state.robot_id: state for state in state_snapshot.robots
        }
        belief_by_id = {
            belief.belief_id: belief
            for belief in state_snapshot.environment_beliefs
        }
        belief_by_key = {
            (belief.subject_id, belief.kind): belief
            for belief in state_snapshot.environment_beliefs
        }
        reserved_resources = {
            reservation.resource_id
            for reservation in state_snapshot.resource_reservations
            if reservation.status in _BUSY_RESERVATION_STATUSES
        }
        assigned_counts: dict[str, int] = {}
        last_node_by_robot: dict[str, str] = {}
        compiled_nodes: list[MissionTaskNode] = []
        subtasks: list[MissionSubtask] = []
        depth_by_node: dict[str, int] = {}

        for index, proposal_node in enumerate(proposal.nodes):
            belief_requirements = self._compile_belief_requirements(
                proposal_node,
                belief_by_id=belief_by_id,
                belief_by_key=belief_by_key,
                state_snapshot=state_snapshot,
            )
            try:
                completion_contract = (
                    self.completion_contract_compiler.compile(
                        task_type=proposal_node.task_type,
                        capability_required=(
                            proposal_node.capability_required
                        ),
                        target=proposal_node.target,
                        completion_goal=proposal_node.completion_goal,
                    )
                )
            except CompletionContractError as exc:
                raise MissionGraphCompilationError([
                    f"Mission graph proposal node "
                    f"{proposal_node.node_id!r}: {error}"
                    for error in exc.errors
                ]) from exc
            dependencies = list(dict.fromkeys(proposal_node.depends_on))
            if proposal_node.execution_mode == "sequential" and index > 0:
                previous_id = proposal.nodes[index - 1].node_id
                if previous_id not in dependencies:
                    dependencies.append(previous_id)

            candidates = self._candidate_robots(
                proposal_node,
                state_by_robot,
                reserved_resources,
            )
            if not candidates:
                raise MissionGraphCompilationError([
                    f"Mission graph proposal node {proposal_node.node_id!r} has "
                    "no online, safe, capable robot with available task capacity."
                ])
            selected = min(
                candidates,
                key=lambda state: self._allocation_score(
                    state,
                    proposal_node,
                    assigned_counts,
                ),
            )
            previous_robot_node = last_node_by_robot.get(selected.robot_id)
            if (
                previous_robot_node is not None
                and previous_robot_node not in dependencies
            ):
                dependencies.append(previous_robot_node)

            assigned_counts[selected.robot_id] = (
                assigned_counts.get(selected.robot_id, 0) + 1
            )
            last_node_by_robot[selected.robot_id] = proposal_node.node_id
            dependency_depths = [
                depth_by_node[dependency_id] for dependency_id in dependencies
            ]
            execution_group = (
                max(dependency_depths) + 1 if dependency_depths else 0
            )
            depth_by_node[proposal_node.node_id] = execution_group
            task_node = MissionTaskNode(
                node_id=proposal_node.node_id,
                robot_id=selected.robot_id,
                command=proposal_node.command,
                capability_required=proposal_node.capability_required,
                target=proposal_node.target,
                task_type=proposal_node.task_type,
                completion_goal=proposal_node.completion_goal,
                depends_on=tuple(dependencies),
                preconditions=(
                    MissionCondition(
                        kind="robot_enabled",
                        subject=selected.robot_id,
                    ),
                    MissionCondition(
                        kind="robot_has_capability",
                        subject=selected.robot_id,
                        details={
                            "capability": proposal_node.capability_required
                        },
                    ),
                    MissionCondition(
                        kind="emergency_stop_inactive",
                        subject=selected.robot_id,
                    ),
                ),
                belief_requirements=belief_requirements,
                expected_effects=(
                    f"completion_goal:{proposal_node.completion_goal}",
                ),
                success_evidence=completion_contract.success_evidence,
                risk_level=completion_contract.risk_level,
                exclusive_resources=(f"robot:{selected.robot_id}",),
                timeout_seconds=completion_contract.timeout_seconds,
                recovery_policy=completion_contract.recovery_policy,
            )
            compiled_nodes.append(task_node)
            subtasks.append(
                MissionSubtask(
                    robot_id=selected.robot_id,
                    command=proposal_node.command,
                    floor=proposal_node.target.floor,
                    capability_required=proposal_node.capability_required,
                    execution_group=execution_group,
                    node_id=proposal_node.node_id,
                    task_type=proposal_node.task_type,
                    target=proposal_node.target.to_dict(),
                    completion_goal=proposal_node.completion_goal,
                    completion_contract=completion_contract.to_dict(),
                    belief_requirements=[
                        requirement.to_dict()
                        for requirement in belief_requirements
                    ],
                )
            )

        plan = MissionPlan(
            intent=proposal.intent,
            command=proposal.command,
            subtasks=subtasks,
            knowledge_refs=list(proposal.knowledge_refs),
        )
        graph = MissionTaskGraph(
            plan_id=plan_id,
            mission_id=mission_id,
            intent=proposal.intent,
            command=proposal.command,
            nodes=tuple(compiled_nodes),
            revision=revision,
            state_snapshot_id=state_snapshot.snapshot_id,
            supersedes_plan_id=supersedes_plan_id,
            invalidation_evidence_ids=invalidation_evidence_ids,
            knowledge_refs=proposal.knowledge_refs,
        )
        return CompiledMissionGraph(
            proposal=proposal,
            plan=plan,
            task_graph=graph,
        )

    def _compile_belief_requirements(
        self,
        node: MissionGraphProposalNode,
        *,
        belief_by_id: dict[str, Any],
        belief_by_key: dict[tuple[str, str], Any],
        state_snapshot: MissionStateSnapshot,
    ) -> tuple[MissionBeliefRequirement, ...]:
        requirements: list[MissionBeliefRequirement] = []
        errors: list[str] = []
        explicit_by_id = {
            assumption.belief_id: assumption
            for assumption in node.belief_assumptions
        }
        consumed_explicit_ids: set[str] = set()
        try:
            grounded = self.task_assumption_registry.ground(
                task_type=node.task_type,
                target=node.target,
            )
        except ValueError as exc:
            raise MissionGraphCompilationError([
                f"Mission graph proposal node {node.node_id!r}: {exc}"
            ]) from exc

        for mandatory in grounded:
            belief = belief_by_key.get(
                (mandatory.subject_id, mandatory.kind)
            )
            prefix = (
                f"Mission graph proposal node {node.node_id!r} requires "
                f"{mandatory.subject_id!r}/{mandatory.kind!r}"
            )
            if belief is None:
                errors.append(
                    f"{prefix}, but the planning snapshot has no such belief; "
                    "add reconnaissance or current-state sensing."
                )
                continue
            explicit = explicit_by_id.get(belief.belief_id)
            if explicit is not None:
                consumed_explicit_ids.add(belief.belief_id)
                if not _same_scalar(
                    explicit.expected_value,
                    mandatory.expected_value,
                ):
                    errors.append(
                        f"{prefix}, but the planner declared a conflicting "
                        "expected value."
                    )
                    continue
                origin = "planner_and_authoritative_rule"
                source_knowledge_ids = explicit.knowledge_refs
            else:
                origin = "authoritative_rule"
                source_knowledge_ids = ()
            requirement = self._requirement_from_belief(
                node=node,
                belief=belief,
                expected_value=mandatory.expected_value,
                minimum_confidence=mandatory.minimum_confidence,
                maximum_age_seconds=mandatory.maximum_age_seconds,
                state_snapshot=state_snapshot,
                origin=origin,
                source_rule_ids=mandatory.source_rule_ids,
                source_knowledge_ids=source_knowledge_ids,
                errors=errors,
            )
            if requirement is not None:
                requirements.append(requirement)

        for assumption in node.belief_assumptions:
            if assumption.belief_id in consumed_explicit_ids:
                continue
            belief = belief_by_id.get(assumption.belief_id)
            prefix = (
                f"Mission graph proposal node {node.node_id!r} belief "
                f"{assumption.belief_id!r}"
            )
            if belief is None:
                if any(
                    kw in assumption.belief_id.lower()
                    for kw in ("robot_state", "fleet_state", "robot_presence", "presence")
                ):
                    continue
                errors.append(f"{prefix} is absent from the planning snapshot.")
                continue
            requirement = self._requirement_from_belief(
                node=node,
                belief=belief,
                expected_value=assumption.expected_value,
                minimum_confidence=_MINIMUM_BELIEF_CONFIDENCE,
                maximum_age_seconds=_MAXIMUM_BELIEF_AGE_SECONDS,
                state_snapshot=state_snapshot,
                origin="planner_declared",
                source_rule_ids=(),
                source_knowledge_ids=assumption.knowledge_refs,
                errors=errors,
            )
            if requirement is not None:
                requirements.append(requirement)
        if errors:
            raise MissionGraphCompilationError(errors)
        return tuple(sorted(
            requirements,
            key=lambda item: (item.subject_id, item.kind, item.belief_id),
        ))

    @staticmethod
    def _requirement_from_belief(
        *,
        node: MissionGraphProposalNode,
        belief: Any,
        expected_value: Any,
        minimum_confidence: float,
        maximum_age_seconds: float,
        state_snapshot: MissionStateSnapshot,
        origin: str,
        source_rule_ids: tuple[str, ...],
        source_knowledge_ids: tuple[str, ...],
        errors: list[str],
    ) -> MissionBeliefRequirement | None:
        prefix = (
            f"Mission graph proposal node {node.node_id!r} belief "
            f"{belief.belief_id!r}"
        )
        if belief.status != "confirmed":
            errors.append(f"{prefix} is {belief.status!r}, not confirmed.")
            return None
        if not _same_scalar(belief.value, expected_value):
            errors.append(
                f"{prefix} does not match the required expected value."
            )
            return None
        if belief.confidence < minimum_confidence:
            errors.append(
                f"{prefix} confidence is below {minimum_confidence}."
            )
            return None
        age_seconds = _age_seconds(
            captured_at=state_snapshot.captured_at,
            observed_at=belief.observed_at,
        )
        if (
            age_seconds is None
            or age_seconds < 0
            or age_seconds > maximum_age_seconds
        ):
            errors.append(
                f"{prefix} is too old or has an invalid timestamp."
            )
            return None
        if not belief.evidence_ids:
            errors.append(f"{prefix} has no auditable evidence.")
            return None
        return MissionBeliefRequirement(
            belief_id=belief.belief_id,
            subject_id=belief.subject_id,
            kind=belief.kind,
            expected_value=expected_value,
            required_status="confirmed",
            minimum_confidence=minimum_confidence,
            maximum_age_seconds=maximum_age_seconds,
            planning_snapshot_id=state_snapshot.snapshot_id,
            planning_evidence_ids=belief.evidence_ids,
            origin=origin,
            source_rule_ids=source_rule_ids,
            source_knowledge_ids=source_knowledge_ids,
        )

    def _candidate_robots(
        self,
        node: MissionGraphProposalNode,
        state_by_robot: dict[str, MissionRobotState],
        reserved_resources: set[str],
    ) -> list[MissionRobotState]:
        candidates: list[MissionRobotState] = []
        for entry in self.registry.enabled_entries():
            state = state_by_robot.get(entry.robot_id)
            if state is None:
                continue
            if (
                not state.online
                or state.stale
                or state.emergency_stop_active
                or node.capability_required not in entry.capabilities
                or node.capability_required not in state.capabilities
                or f"robot:{entry.robot_id}" in reserved_resources
            ):
                continue
            if (
                state.task_capacity is not None
                and state.task_capacity.available_execution_slots <= 0
            ):
                continue
            if (
                node.target.floor is not None
                and state.reachable_floors
                and node.target.floor not in state.reachable_floors
            ):
                continue
            candidates.append(state)
        return candidates

    @staticmethod
    def _allocation_score(
        state: MissionRobotState,
        node: MissionGraphProposalNode,
        assigned_counts: dict[str, int],
    ) -> tuple[int, int, int, float, str]:
        same_floor_penalty = int(
            node.target.floor is not None
            and state.current_floor != node.target.floor
        )
        active_tasks = len(state.active_task_ids)
        battery_penalty = (
            101.0
            if state.battery_percent is None
            else 100.0 - state.battery_percent
        )
        return (
            assigned_counts.get(state.robot_id, 0),
            same_floor_penalty,
            active_tasks,
            battery_penalty,
            state.robot_id,
        )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(
            f"Mission graph proposal field {key!r} must be a non-empty string."
        )
    return item.strip()


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _same_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _age_seconds(*, captured_at: str, observed_at: str) -> float | None:
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if (
        captured.tzinfo is None
        or captured.utcoffset() is None
        or observed.tzinfo is None
        or observed.utcoffset() is None
    ):
        return None
    return (captured - observed).total_seconds()
