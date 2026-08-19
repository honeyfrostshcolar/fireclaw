from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable, List, Sequence

from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.mission.world_state_belief import (
    BELIEF_STATUSES,
    WorldStateBelief,
    WorldStateBeliefBuilder,
    WorldStateObservation,
)


EnvironmentFactProvider = Callable[[str], Sequence["MissionEnvironmentFact"]]
ResourceReservationProvider = Callable[[str], Sequence["MissionResourceReservation"]]


@dataclass(frozen=True)
class RobotTaskCapacity:
    active_execution_tasks: int
    max_active_execution_tasks: int
    available_execution_slots: int

    def to_dict(self) -> dict[str, int]:
        return {
            "active_execution_tasks": self.active_execution_tasks,
            "max_active_execution_tasks": self.max_active_execution_tasks,
            "available_execution_slots": self.available_execution_slots,
        }


@dataclass(frozen=True)
class MissionRobotState:
    """Planner-safe projection of one robot's current gateway state."""

    robot_id: str
    online: bool
    stale: bool
    last_seen_at: str | None
    capabilities: tuple[str, ...]
    zone: str | None
    mode: str | None = None
    dry_run: bool | None = None
    battery_percent: float | None = None
    current_floor: int | None = None
    available_sensors: tuple[str, ...] = ()
    supports_real_execution: bool | None = None
    reachable_floors: tuple[int, ...] = ()
    hazards: tuple[str, ...] = ()
    victims_by_floor: dict[int, int] = field(default_factory=dict)
    task_capacity: RobotTaskCapacity | None = None
    active_task_ids: tuple[str, ...] = ()
    emergency_stop_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "robot_id": self.robot_id,
            "online": self.online,
            "stale": self.stale,
            "capabilities": list(self.capabilities),
            "available_sensors": list(self.available_sensors),
            "reachable_floors": list(self.reachable_floors),
            "hazards": list(self.hazards),
            "victims_by_floor": dict(self.victims_by_floor),
            "active_task_ids": list(self.active_task_ids),
            "emergency_stop_active": self.emergency_stop_active,
        }
        optional_values = {
            "last_seen_at": self.last_seen_at,
            "zone": self.zone,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "battery_percent": self.battery_percent,
            "current_floor": self.current_floor,
            "supports_real_execution": self.supports_real_execution,
        }
        result.update({key: value for key, value in optional_values.items() if value is not None})
        if self.task_capacity is not None:
            result["task_capacity"] = self.task_capacity.to_dict()
        return result


@dataclass(frozen=True)
class MissionTaskState:
    """Lifecycle-only task state; free-form results are deliberately excluded."""

    task_id: str
    status: str
    delivery_status: str
    owner_id: str
    parent_task_id: str | None
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    last_event_at: str | None = None
    terminal_outcome: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "delivery_status": self.delivery_status,
            "owner_id": self.owner_id,
            "created_at": self.created_at,
        }
        optional_values = {
            "parent_task_id": self.parent_task_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "last_event_at": self.last_event_at,
            "terminal_outcome": self.terminal_outcome,
        }
        result.update({key: value for key, value in optional_values.items() if value is not None})
        return result


@dataclass(frozen=True)
class MissionEnvironmentFact:
    """An auditable environment observation supplied by a runtime owner."""

    fact_id: str
    kind: str
    value: str | int | float | bool | None
    source: str
    observed_at: str
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    expires_at: str | None = None
    subject_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "fact_id": self.fact_id,
            "kind": self.kind,
            "value": self.value,
            "source": self.source,
            "observed_at": self.observed_at,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.expires_at is not None:
            result["expires_at"] = self.expires_at
        if self.subject_id is not None:
            result["subject_id"] = self.subject_id
        return result


@dataclass(frozen=True)
class MissionResourceReservation:
    """Exclusive resource ownership known at snapshot time."""

    resource_id: str
    owner_task_id: str
    owner_robot_id: str | None
    status: str
    acquired_at: str
    evidence_ids: tuple[str, ...] = ()
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "resource_id": self.resource_id,
            "owner_task_id": self.owner_task_id,
            "status": self.status,
            "acquired_at": self.acquired_at,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.owner_robot_id is not None:
            result["owner_robot_id"] = self.owner_robot_id
        if self.expires_at is not None:
            result["expires_at"] = self.expires_at
        return result


@dataclass(frozen=True)
class MissionStateSnapshot:
    """Versioned current-world input consumed by the mission planner."""

    snapshot_id: str
    mission_id: str
    version: int
    captured_at: str
    robots: tuple[MissionRobotState, ...]
    tasks: tuple[MissionTaskState, ...] = ()
    environment_facts: tuple[MissionEnvironmentFact, ...] = ()
    environment_beliefs: tuple[WorldStateBelief, ...] = ()
    resource_reservations: tuple[MissionResourceReservation, ...] = ()
    previous_snapshot_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    belief_projection_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "snapshot_id": self.snapshot_id,
            "mission_id": self.mission_id,
            "version": self.version,
            "captured_at": self.captured_at,
            "robots": [robot.to_dict() for robot in self.robots],
            "tasks": [task.to_dict() for task in self.tasks],
            "environment_facts": [fact.to_dict() for fact in self.environment_facts],
            "environment_beliefs": [
                belief.to_dict() for belief in self.environment_beliefs
            ],
            "environment_belief_summary": {
                status: sum(
                    belief.status == status
                    for belief in self.environment_beliefs
                )
                for status in sorted(BELIEF_STATUSES)
            },
            "resource_reservations": [
                reservation.to_dict() for reservation in self.resource_reservations
            ],
            "evidence_ids": list(self.evidence_ids),
            "belief_projection_version": self.belief_projection_version,
        }
        if self.previous_snapshot_id is not None:
            result["previous_snapshot_id"] = self.previous_snapshot_id
        return result


class MissionStateSnapshotBuilder:
    """Build a bounded snapshot from authoritative runtime owners."""

    def __init__(
        self,
        *,
        registry: RobotRegistry,
        task_registry: Any | None = None,
        environment_fact_provider: EnvironmentFactProvider | None = None,
        resource_reservation_provider: ResourceReservationProvider | None = None,
        world_state_belief_builder: WorldStateBeliefBuilder | None = None,
        max_tasks: int = 100,
        max_external_items: int = 100,
    ) -> None:
        self.registry = registry
        self.task_registry = task_registry
        self.environment_fact_provider = environment_fact_provider
        self.resource_reservation_provider = resource_reservation_provider
        self.world_state_belief_builder = (
            world_state_belief_builder or WorldStateBeliefBuilder()
        )
        self.max_tasks = max_tasks
        self.max_external_items = max_external_items

    def build(
        self,
        *,
        mission_id: str,
        presence: dict[str, dict[str, Any]],
        version: int = 1,
        previous_snapshot_id: str | None = None,
        captured_at: str | None = None,
    ) -> MissionStateSnapshot:
        captured = captured_at or datetime.now(timezone.utc).isoformat()
        robots = tuple(
            self._robot_state(entry.robot_id, presence.get(entry.robot_id, {}))
            for entry in sorted(
                (item for item in self.registry.list_entries() if item.enabled),
                key=lambda item: item.robot_id,
            )
        )
        tasks = tuple(self._task_states(mission_id))
        environment_facts = tuple(
            self._provided_items(
                self.environment_fact_provider,
                mission_id,
                MissionEnvironmentFact,
                "environment fact",
            )
        )
        environment_beliefs = self._environment_beliefs(
            environment_facts,
            captured_at=captured,
        )
        reservations = tuple(
            self._provided_items(
                self.resource_reservation_provider,
                mission_id,
                MissionResourceReservation,
                "resource reservation",
            )
        )
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for item in (*environment_facts, *reservations)
                for evidence_id in item.evidence_ids
            )
        )
        return MissionStateSnapshot(
            snapshot_id=f"{mission_id}:state:{version}",
            mission_id=mission_id,
            version=version,
            captured_at=captured,
            robots=robots,
            tasks=tasks,
            environment_facts=environment_facts,
            environment_beliefs=environment_beliefs,
            resource_reservations=reservations,
            previous_snapshot_id=previous_snapshot_id,
            evidence_ids=evidence_ids,
            belief_projection_version=1,
        )

    def with_environment_facts(
        self,
        snapshot: MissionStateSnapshot,
        environment_facts: tuple[MissionEnvironmentFact, ...],
        *,
        extra_evidence_ids: tuple[str, ...] = (),
    ) -> MissionStateSnapshot:
        """Replace raw observations and atomically recompute their belief projection."""

        facts = tuple(environment_facts[: self.max_external_items])
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *(
                        evidence_id
                        for fact in facts
                        for evidence_id in fact.evidence_ids
                    ),
                    *(
                        evidence_id
                        for reservation in snapshot.resource_reservations
                        for evidence_id in reservation.evidence_ids
                    ),
                    *extra_evidence_ids,
                )
            )
        )
        return replace(
            snapshot,
            environment_facts=facts,
            environment_beliefs=self._environment_beliefs(
                facts,
                captured_at=snapshot.captured_at,
            ),
            evidence_ids=evidence_ids,
            belief_projection_version=1,
        )

    def _environment_beliefs(
        self,
        facts: tuple[MissionEnvironmentFact, ...],
        *,
        captured_at: str,
    ) -> tuple[WorldStateBelief, ...]:
        observations = tuple(
            WorldStateObservation(
                observation_id=fact.fact_id,
                subject_id=fact.subject_id or f"fact:{fact.fact_id}",
                kind=fact.kind,
                value=fact.value,
                source=fact.source,
                observed_at=fact.observed_at,
                evidence_ids=fact.evidence_ids,
                confidence=fact.confidence,
                expires_at=fact.expires_at,
            )
            for fact in facts
        )
        return self.world_state_belief_builder.build(
            observations,
            captured_at=captured_at,
        )

    def _robot_state(
        self,
        robot_id: str,
        presence: dict[str, Any],
    ) -> MissionRobotState:
        entry = self.registry.get(robot_id)
        gateway_state = _mapping(presence.get("state"))
        robot_state = _mapping(gateway_state.get("robot_state"))
        environment_state = _mapping(gateway_state.get("environment_state"))
        emergency_stop = _mapping(gateway_state.get("emergency_stop"))
        task_capacity = _task_capacity(gateway_state.get("task_capacity"))
        active_tasks = gateway_state.get("active_tasks")
        active_task_ids = ()
        if isinstance(active_tasks, list):
            active_task_ids = _bounded_strings(
                [
                    item.get("task_id")
                    for item in active_tasks
                    if isinstance(item, dict)
                ],
                limit=64,
            )
        return MissionRobotState(
            robot_id=robot_id,
            online=presence.get("online") is True,
            stale=presence.get("stale") is True,
            last_seen_at=_optional_string(presence.get("last_seen_at")),
            capabilities=tuple(entry.capabilities) if entry is not None else (),
            zone=entry.zone if entry is not None else None,
            mode=_optional_string(robot_state.get("mode")),
            dry_run=_optional_bool(robot_state.get("dry_run")),
            battery_percent=_optional_float(robot_state.get("battery_percent")),
            current_floor=_optional_int(robot_state.get("current_floor")),
            available_sensors=_bounded_strings(
                robot_state.get("available_sensors"),
                limit=64,
            ),
            supports_real_execution=_optional_bool(
                robot_state.get("supports_real_execution")
            ),
            reachable_floors=_positive_ints(
                environment_state.get("reachable_floors"),
                limit=128,
            ),
            hazards=_bounded_strings(environment_state.get("hazards"), limit=64),
            victims_by_floor=_victims_by_floor(
                environment_state.get("victims_by_floor")
            ),
            task_capacity=task_capacity,
            active_task_ids=active_task_ids,
            emergency_stop_active=emergency_stop.get("active") is True,
        )

    def _task_states(self, mission_id: str) -> list[MissionTaskState]:
        if self.task_registry is None:
            return []
        records = self.task_registry.list_records()
        relevant = [
            record
            for record in records
            if (
                record.task_id == mission_id
                or record.requester_session_id == mission_id
                or record.parent_task_id == mission_id
            )
        ]
        relevant.sort(
            key=lambda record: (
                record.last_event_at or record.ended_at or record.started_at or record.created_at,
                record.task_id,
            ),
            reverse=True,
        )
        return [
            MissionTaskState(
                task_id=record.task_id,
                status=record.status,
                delivery_status=record.delivery_status,
                owner_id=record.owner_id,
                parent_task_id=record.parent_task_id,
                created_at=record.created_at,
                started_at=record.started_at,
                ended_at=record.ended_at,
                last_event_at=record.last_event_at,
                terminal_outcome=record.terminal_outcome,
            )
            for record in relevant[: self.max_tasks]
        ]

    def _provided_items(
        self,
        provider: Callable[[str], list[Any]] | None,
        mission_id: str,
        item_type: type,
        item_label: str,
    ) -> list[Any]:
        if provider is None:
            return []
        items = provider(mission_id)
        if not isinstance(items, list):
            raise TypeError(f"{item_label} provider must return a list")
        bounded = items[: self.max_external_items]
        if any(not isinstance(item, item_type) for item in bounded):
            raise TypeError(f"{item_label} provider returned an invalid item")
        return bounded


class MissionStateSnapshotValidator:
    """Reject malformed or internally inconsistent planner state."""

    def validate(self, snapshot: MissionStateSnapshot) -> list[str]:
        errors: list[str] = []
        if not snapshot.snapshot_id:
            errors.append("Mission state snapshot_id must not be empty.")
        if not snapshot.mission_id:
            errors.append("Mission state mission_id must not be empty.")
        if snapshot.version < 1:
            errors.append("Mission state version must be at least 1.")
        if snapshot.version > 1 and not snapshot.previous_snapshot_id:
            errors.append("Mission state version greater than 1 requires previous_snapshot_id.")
        if snapshot.previous_snapshot_id == snapshot.snapshot_id:
            errors.append("Mission state snapshot cannot reference itself as previous.")
        if not _valid_timestamp(snapshot.captured_at):
            errors.append("Mission state captured_at must be an ISO-8601 timestamp.")

        errors.extend(self._duplicate_errors("robot", [item.robot_id for item in snapshot.robots]))
        errors.extend(self._duplicate_errors("task", [item.task_id for item in snapshot.tasks]))
        errors.extend(
            self._duplicate_errors("environment fact", [item.fact_id for item in snapshot.environment_facts])
        )
        errors.extend(
            self._duplicate_errors(
                "environment belief",
                [item.belief_id for item in snapshot.environment_beliefs],
            )
        )
        errors.extend(
            self._duplicate_errors(
                "resource reservation",
                [item.resource_id for item in snapshot.resource_reservations],
            )
        )

        for robot in snapshot.robots:
            if not robot.robot_id:
                errors.append("Mission robot state robot_id must not be empty.")
            if robot.battery_percent is not None and not 0 <= robot.battery_percent <= 100:
                errors.append(
                    f"Mission robot state {robot.robot_id!r} battery_percent must be between 0 and 100."
                )
            if robot.current_floor is not None and robot.current_floor <= 0:
                errors.append(
                    f"Mission robot state {robot.robot_id!r} current_floor must be positive."
                )
        for fact in snapshot.environment_facts:
            if not fact.fact_id or not fact.kind or not fact.source:
                errors.append("Mission environment fact identifiers and source must not be empty.")
            if fact.subject_id is not None and not fact.subject_id.strip():
                errors.append(
                    f"Mission environment fact {fact.fact_id!r} subject_id must not be empty."
                )
            if not fact.evidence_ids:
                errors.append(
                    f"Mission environment fact {fact.fact_id!r} requires evidence_ids."
                )
            if fact.confidence is not None and not 0 <= fact.confidence <= 1:
                errors.append(
                    f"Mission environment fact {fact.fact_id!r} confidence must be between 0 and 1."
                )
            if isinstance(fact.value, float) and not isfinite(fact.value):
                errors.append(
                    f"Mission environment fact {fact.fact_id!r} value must be finite."
                )
            if not _valid_timestamp(fact.observed_at):
                errors.append(
                    f"Mission environment fact {fact.fact_id!r} observed_at is invalid."
                )
            if fact.expires_at is not None and not _valid_timestamp(fact.expires_at):
                errors.append(
                    f"Mission environment fact {fact.fact_id!r} expires_at is invalid."
                )

        if snapshot.belief_projection_version not in {0, 1}:
            errors.append(
                "Mission state belief_projection_version must be 0 or 1."
            )
        if (
            snapshot.belief_projection_version == 1
            and snapshot.environment_facts
            and not snapshot.environment_beliefs
        ):
            errors.append(
                "Mission state belief projection must represent environment facts."
            )
        known_fact_ids = {
            fact.fact_id for fact in snapshot.environment_facts
        }
        projected_fact_ids: set[str] = set()
        belief_keys: set[tuple[str, str]] = set()
        for belief in snapshot.environment_beliefs:
            if not belief.belief_id or not belief.subject_id or not belief.kind:
                errors.append(
                    "Mission environment belief identifiers must not be empty."
                )
            key = (belief.subject_id, belief.kind)
            if key in belief_keys:
                errors.append(
                    "Mission state has duplicate environment belief key "
                    f"{key!r}."
                )
            belief_keys.add(key)
            if belief.status not in BELIEF_STATUSES:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} has invalid status."
                )
            if not 0 <= belief.confidence <= 1:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} confidence must be between 0 and 1."
                )
            if not _valid_timestamp(belief.observed_at):
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} observed_at is invalid."
                )
            referenced_fact_ids = {
                *belief.supporting_observation_ids,
                *belief.conflicting_observation_ids,
                *belief.stale_observation_ids,
                *belief.superseded_observation_ids,
                *(
                    observation_id
                    for candidate in belief.candidates
                    for observation_id in candidate.observation_ids
                ),
            }
            projected_fact_ids.update(referenced_fact_ids)
            unknown_fact_ids = referenced_fact_ids - known_fact_ids
            if unknown_fact_ids:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} references unknown observations."
                )
            if belief.status in {"conflicted", "stale"} and belief.value is not None:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} must not expose a resolved value for status {belief.status!r}."
                )
            if belief.status == "conflicted" and len(belief.candidates) < 2:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} requires at least two conflict candidates."
                )
            if belief.status == "stale" and belief.candidates:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} must not expose fresh candidates."
                )
            if belief.status in {"confirmed", "uncertain"} and not belief.candidates:
                errors.append(
                    f"Mission environment belief {belief.belief_id!r} requires a leading candidate."
                )
            for candidate in belief.candidates:
                if not 0 <= candidate.confidence <= 1:
                    errors.append(
                        f"Mission environment belief {belief.belief_id!r} candidate confidence must be between 0 and 1."
                    )
                if not candidate.observation_ids or not candidate.sources:
                    errors.append(
                        f"Mission environment belief {belief.belief_id!r} candidate requires observations and sources."
                    )
        if (
            snapshot.belief_projection_version == 1
            and projected_fact_ids != known_fact_ids
        ):
            errors.append(
                "Mission state belief projection does not cover every environment fact."
            )

        valid_reservation_statuses = {"held", "pending", "released", "expired"}
        for reservation in snapshot.resource_reservations:
            if (
                not reservation.resource_id
                or not reservation.owner_task_id
                or reservation.status not in valid_reservation_statuses
            ):
                errors.append(
                    f"Mission resource reservation {reservation.resource_id!r} is invalid."
                )
            if not _valid_timestamp(reservation.acquired_at):
                errors.append(
                    f"Mission resource reservation {reservation.resource_id!r} acquired_at is invalid."
                )
        return list(dict.fromkeys(errors))

    def _duplicate_errors(self, label: str, identifiers: list[str]) -> list[str]:
        seen: set[str] = set()
        errors: list[str] = []
        for identifier in identifiers:
            if identifier in seen:
                errors.append(f"Mission state has duplicate {label} id {identifier!r}.")
            seen.add(identifier)
        return errors


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:256] if normalized else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _bounded_strings(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(
        dict.fromkeys(
            normalized
            for item in list(value)[:limit]
            if (normalized := _optional_string(item)) is not None
        )
    )


def _positive_ints(value: Any, *, limit: int) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(
        dict.fromkeys(
            item
            for item in list(value)[:limit]
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        )
    )


def _victims_by_floor(value: Any) -> dict[int, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, int] = {}
    for raw_floor, raw_count in list(value.items())[:128]:
        try:
            floor = int(raw_floor)
        except (TypeError, ValueError):
            continue
        if (
            floor > 0
            and isinstance(raw_count, int)
            and not isinstance(raw_count, bool)
            and raw_count >= 0
        ):
            result[floor] = raw_count
    return result


def _task_capacity(value: Any) -> RobotTaskCapacity | None:
    capacity = _mapping(value)
    active = _optional_int(capacity.get("active_execution_tasks"))
    maximum = _optional_int(capacity.get("max_active_execution_tasks"))
    available = _optional_int(capacity.get("available_execution_slots"))
    if active is None or maximum is None or available is None:
        return None
    return RobotTaskCapacity(
        active_execution_tasks=active,
        max_active_execution_tasks=maximum,
        available_execution_slots=available,
    )


def _valid_timestamp(value: str | None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
