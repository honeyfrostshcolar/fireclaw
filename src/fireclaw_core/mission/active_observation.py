"""Host-owned active observation requests for mission deliberation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.mission.completion_contract import (
    CompletionContractCompiler,
    CompletionContractError,
)
from fireclaw_core.mission.mission_planner import MissionSubtask
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionRobotState,
    MissionStateSnapshot,
)
from fireclaw_core.mission.task_graph import MissionTarget
from fireclaw_core.mission.world_state_belief import WorldStateBelief


ACTIVE_OBSERVATION_CAPABILITIES = frozenset({
    "monitor_environment",
    "recon",
    "victim_search",
})
UNRESOLVED_BELIEF_STATUSES = frozenset({
    "conflicted",
    "stale",
    "uncertain",
})
TERMINAL_OBSERVATION_FAILURE_STATUSES = frozenset({
    "block",
    "cancelled",
    "denied",
    "failed",
    "lost",
    "orphaned",
    "rejected",
    "timed_out",
})
TERMINAL_OBSERVATION_SUCCESS_STATUSES = frozenset({
    "completed",
    "succeeded",
})


class ActiveObservationError(ValueError):
    pass


@dataclass(frozen=True)
class MissionActiveObservationLimits:
    max_rounds: int = 2
    timeout_seconds: float = 30.0
    poll_interval_seconds: float = 0.2

    def __post_init__(self) -> None:
        if self.max_rounds < 0:
            raise ValueError("max_rounds must be non-negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")


@dataclass(frozen=True)
class MissionObservationRequest:
    belief_id: str
    target: MissionTarget
    capability_required: str
    reason: str
    required_sensor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "belief_id": self.belief_id,
            "target": self.target.to_dict(),
            "capability_required": self.capability_required,
            "reason": self.reason,
        }
        if self.required_sensor is not None:
            result["required_sensor"] = self.required_sensor
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionObservationRequest:
        if not isinstance(value, dict):
            raise ActiveObservationError(
                "Mission observation request must be an object."
            )
        return cls(
            belief_id=_required_string(value, "belief_id"),
            target=MissionTarget.from_dict(value.get("target")),
            capability_required=_required_string(
                value,
                "capability_required",
            ),
            reason=_required_string(value, "reason"),
            required_sensor=_optional_string(
                value.get("required_sensor"),
                "required_sensor",
            ),
        )


@dataclass(frozen=True)
class CompiledMissionObservation:
    request: MissionObservationRequest
    robot_id: str
    belief: WorldStateBelief
    subtask: MissionSubtask

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "robot_id": self.robot_id,
            "belief": self.belief.to_dict(),
            "subtask": self.subtask.to_dict(),
        }


class MissionObservationCompiler:
    """Validate a planner request and deterministically allocate a robot."""

    def __init__(
        self,
        registry: RobotRegistry,
        *,
        completion_contract_compiler: CompletionContractCompiler | None = None,
        active_observation_capabilities: Iterable[str] | None = None,
        task_type_by_capability: Mapping[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self.completion_contract_compiler = (
            completion_contract_compiler or CompletionContractCompiler()
        )
        self.active_observation_capabilities = frozenset(
            active_observation_capabilities
            if active_observation_capabilities is not None
            else ACTIVE_OBSERVATION_CAPABILITIES
        )
        self.task_type_by_capability = dict(
            task_type_by_capability or {}
        )

    def compile(
        self,
        request: MissionObservationRequest,
        *,
        snapshot: MissionStateSnapshot,
    ) -> CompiledMissionObservation:
        belief = next(
            (
                item
                for item in snapshot.environment_beliefs
                if item.belief_id == request.belief_id
            ),
            None,
        )
        if belief is None:
            raise ActiveObservationError(
                "Mission observation request references a belief absent "
                "from the current snapshot."
            )
        if belief.status not in UNRESOLVED_BELIEF_STATUSES:
            raise ActiveObservationError(
                "Mission observation request must target an unresolved belief."
            )
        if (
            request.capability_required
            not in self.active_observation_capabilities
        ):
            raise ActiveObservationError(
                "Mission observation request uses a capability outside the "
                "active-observation allowlist."
            )
        _validate_target(request.target)
        reserved_robots = {
            reservation.owner_robot_id
            for reservation in snapshot.resource_reservations
            if reservation.status in {"active", "acquired", "reserved"}
            and reservation.owner_robot_id is not None
        }
        candidates = [
            state
            for state in snapshot.robots
            if self._eligible(
                state,
                request=request,
                reserved_robots=reserved_robots,
            )
        ]
        if not candidates:
            raise ActiveObservationError(
                "No online, safe, capable robot with the required sensor and "
                "available capacity can perform the requested observation."
            )
        selected = min(
            candidates,
            key=lambda state: self._allocation_score(state, request.target),
        )
        task_type = self._task_type_for_capability(
            request.capability_required
        )
        completion_goal = (
            f"Observe belief {belief.belief_id} and return a structured "
            f"{belief.kind} observation for subject {belief.subject_id}."
        )
        try:
            contract = self.completion_contract_compiler.compile(
                task_type=task_type,
                capability_required=request.capability_required,
                target=request.target,
                completion_goal=completion_goal,
            )
        except CompletionContractError as exc:
            raise ActiveObservationError(str(exc)) from exc
        node_digest = sha256(
            json.dumps(
                request.to_dict(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        subtask = MissionSubtask(
            robot_id=selected.robot_id,
            command=(
                f"Observe {belief.kind} for {belief.subject_id}; "
                f"return structured evidence for {belief.belief_id}."
            ),
            floor=request.target.floor,
            capability_required=request.capability_required,
            execution_group=0,
            node_id=f"active_observation_{node_digest}",
            task_type=task_type,
            target=request.target.to_dict(),
            completion_goal=completion_goal,
            completion_contract=contract.to_dict(),
        )
        return CompiledMissionObservation(
            request=request,
            robot_id=selected.robot_id,
            belief=belief,
            subtask=subtask,
        )

    def _task_type_for_capability(self, capability: str) -> str:
        configured = self.task_type_by_capability.get(capability)
        if configured is not None:
            return configured
        matches = [
            definition.task_type
            for definition in (
                self.completion_contract_compiler.registry.definitions()
            )
            if capability in definition.allowed_capabilities
        ]
        if len(matches) != 1:
            raise ActiveObservationError(
                "Active-observation capability must map to exactly one "
                f"registered mission task type: {capability!r}."
            )
        return matches[0]

    def _eligible(
        self,
        state: MissionRobotState,
        *,
        request: MissionObservationRequest,
        reserved_robots: set[str],
    ) -> bool:
        entry = self.registry.get(state.robot_id)
        if (
            entry is None
            or not entry.enabled
            or not state.online
            or state.stale
            or state.emergency_stop_active
            or state.robot_id in reserved_robots
            or request.capability_required not in entry.capabilities
            or request.capability_required not in state.capabilities
        ):
            return False
        if (
            state.task_capacity is not None
            and state.task_capacity.available_execution_slots <= 0
        ):
            return False
        if (
            request.required_sensor is not None
            and request.required_sensor not in state.available_sensors
        ):
            return False
        if (
            request.target.floor is not None
            and state.reachable_floors
            and request.target.floor not in state.reachable_floors
        ):
            return False
        return True

    @staticmethod
    def _allocation_score(
        state: MissionRobotState,
        target: MissionTarget,
    ) -> tuple[int, int, float, str]:
        floor_penalty = int(
            target.floor is not None
            and state.current_floor != target.floor
        )
        battery_penalty = (
            101.0
            if state.battery_percent is None
            else 100.0 - state.battery_percent
        )
        return (
            floor_penalty,
            len(state.active_task_ids),
            battery_penalty,
            state.robot_id,
        )


def environment_fact_from_observation_trace(
    trace: dict[str, Any],
    *,
    compiled: CompiledMissionObservation,
    mission_id: str,
    task_id: str,
) -> MissionEnvironmentFact:
    """Promote only a successful, schema-valid robot observation to a fact."""

    status = observation_trace_status(trace)
    if status not in TERMINAL_OBSERVATION_SUCCESS_STATUSES:
        raise ActiveObservationError(
            f"Observation task is not successfully complete: {status!r}."
        )
    payload = _structured_observation(trace)
    belief = compiled.belief
    if payload.get("belief_id") != belief.belief_id:
        raise ActiveObservationError(
            "Robot observation belief_id does not match the requested belief."
        )
    if payload.get("subject_id") != belief.subject_id:
        raise ActiveObservationError(
            "Robot observation subject_id does not match the requested belief."
        )
    if payload.get("kind") != belief.kind:
        raise ActiveObservationError(
            "Robot observation kind does not match the requested belief."
        )
    value = payload.get("value")
    if not _is_scalar(value):
        raise ActiveObservationError(
            "Robot observation value must be a JSON scalar."
        )
    observed_at = _required_string(payload, "observed_at")
    confidence_value = payload.get("confidence")
    if (
        not isinstance(confidence_value, (int, float))
        or isinstance(confidence_value, bool)
        or not 0 <= float(confidence_value) <= 1
    ):
        raise ActiveObservationError(
            "Robot observation confidence must be in the range [0, 1]."
        )
    raw_evidence = payload.get("evidence_ids")
    if (
        not isinstance(raw_evidence, list)
        or not raw_evidence
        or any(
            not isinstance(item, str) or not item.strip()
            for item in raw_evidence
        )
    ):
        raise ActiveObservationError(
            "Robot observation evidence_ids must be a non-empty string list."
        )
    fact_id = f"{mission_id}:observation:{task_id}"
    return MissionEnvironmentFact(
        fact_id=fact_id,
        kind=belief.kind,
        value=value,
        source=f"robot:{compiled.robot_id}",
        observed_at=observed_at,
        evidence_ids=tuple(dict.fromkeys([task_id, *raw_evidence])),
        confidence=float(confidence_value),
        subject_id=belief.subject_id,
    )


def observation_trace_status(trace: dict[str, Any]) -> str:
    status = trace.get("status")
    if isinstance(status, str):
        return status
    task = trace.get("task")
    if isinstance(task, dict) and isinstance(task.get("status"), str):
        return task["status"]
    return "unknown"


def _structured_observation(trace: dict[str, Any]) -> dict[str, Any]:
    direct = trace.get("observation")
    if isinstance(direct, dict):
        return direct
    result = trace.get("result")
    if isinstance(result, dict) and isinstance(result.get("observation"), dict):
        return result["observation"]
    execution = trace.get("execution")
    if isinstance(execution, dict):
        steps = execution.get("steps")
        if isinstance(steps, list):
            for step in reversed(steps):
                if not isinstance(step, dict) or step.get("status") != "succeeded":
                    continue
                output = step.get("output")
                if isinstance(output, dict) and isinstance(
                    output.get("observation"),
                    dict,
                ):
                    return output["observation"]
    raise ActiveObservationError(
        "Successful observation task did not return a structured observation."
    )


def _validate_target(target: MissionTarget) -> None:
    if not target.frame_id.strip():
        raise ActiveObservationError(
            "Mission observation target frame_id must not be empty."
        )
    if (
        target.floor is None
        and target.area_id is None
        and target.entity_id is None
        and target.pose is None
    ):
        raise ActiveObservationError(
            "Mission observation target requires a floor, area, entity, or pose."
        )
    if target.floor is not None and target.floor <= 0:
        raise ActiveObservationError(
            "Mission observation target floor must be positive."
        )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ActiveObservationError(
            f"Mission observation field {key!r} must be a non-empty string."
        )
    return item.strip()


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ActiveObservationError(
            f"Mission observation field {name!r} must be a non-empty string."
        )
    return value.strip()


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))
