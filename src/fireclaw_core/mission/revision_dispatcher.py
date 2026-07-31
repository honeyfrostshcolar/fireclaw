from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from fireclaw_core.mission.execution_event import MissionExecutionEvent
from fireclaw_core.mission.task_graph import MissionTaskGraph, MissionTaskNode


SUCCEEDED_NODE_STATUSES = frozenset({"succeeded", "completed", "carried"})
ACTIVE_NODE_STATUSES = frozenset({
    "accepted",
    "queued",
    "received",
    "planned",
    "running",
    "cancel_requested",
})
VALID_NODE_RUNTIME_STATUSES = frozenset({
    "pending",
    "accepted",
    "queued",
    "received",
    "planned",
    "running",
    "succeeded",
    "completed",
    "carried",
    "failed",
    "block",
    "blocked",
    "escalated",
    "timed_out",
    "denied",
    "lost",
    "cancel_requested",
    "cancelled",
    "fenced",
})
VALID_NODE_RECOVERY_ACTIONS = frozenset({
    "recheck",
    "retry",
    "reassign",
    "replan",
    "escalate",
    "abort",
})


@dataclass(frozen=True)
class MissionNodeExecution:
    plan_id: str
    node_id: str
    robot_id: str
    status: str
    task_id: str | None = None
    node_spec_hash: str | None = None
    updated_at: str = ""
    completion_evidence: dict[str, Any] | None = None
    dispatch_belief_gate: dict[str, Any] | None = None
    recovery_attempt: int = 0
    recovery_action: str | None = None

    def __post_init__(self) -> None:
        for name in ("plan_id", "node_id", "robot_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.status not in VALID_NODE_RUNTIME_STATUSES:
            raise ValueError(f"Unsupported node runtime status: {self.status}")
        if (
            not isinstance(self.recovery_attempt, int)
            or isinstance(self.recovery_attempt, bool)
            or self.recovery_attempt < 0
        ):
            raise ValueError("recovery_attempt must be non-negative")
        if (
            self.recovery_action is not None
            and self.recovery_action not in VALID_NODE_RECOVERY_ACTIONS
        ):
            raise ValueError(
                f"Unsupported node recovery action: {self.recovery_action}"
            )

    @property
    def is_succeeded(self) -> bool:
        return self.status in SUCCEEDED_NODE_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_NODE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "robot_id": self.robot_id,
            "status": self.status,
            "updated_at": self.updated_at,
            "recovery_attempt": self.recovery_attempt,
        }
        if self.task_id is not None:
            result["task_id"] = self.task_id
        if self.node_spec_hash is not None:
            result["node_spec_hash"] = self.node_spec_hash
        if self.completion_evidence is not None:
            result["completion_evidence"] = dict(self.completion_evidence)
        if self.dispatch_belief_gate is not None:
            result["dispatch_belief_gate"] = dict(
                self.dispatch_belief_gate
            )
        if self.recovery_action is not None:
            result["recovery_action"] = self.recovery_action
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionNodeExecution:
        return cls(
            plan_id=str(value.get("plan_id") or ""),
            node_id=str(value.get("node_id") or ""),
            robot_id=str(value.get("robot_id") or ""),
            status=str(value.get("status") or ""),
            task_id=_string_or_none(value.get("task_id")),
            node_spec_hash=_string_or_none(value.get("node_spec_hash")),
            updated_at=str(value.get("updated_at") or ""),
            completion_evidence=(
                dict(value["completion_evidence"])
                if isinstance(value.get("completion_evidence"), dict)
                else None
            ),
            dispatch_belief_gate=(
                dict(value["dispatch_belief_gate"])
                if isinstance(value.get("dispatch_belief_gate"), dict)
                else None
            ),
            recovery_attempt=_non_negative_int(
                value.get("recovery_attempt"),
                "recovery_attempt",
            ),
            recovery_action=_string_or_none(value.get("recovery_action")),
        )


@dataclass(frozen=True)
class RevisionDispatchDecision:
    current_plan_id: str
    revised_plan_id: str
    carried_node_ids: tuple[str, ...]
    preserved_node_ids: tuple[str, ...]
    pending_node_ids: tuple[str, ...]
    cancel_executions: tuple[MissionNodeExecution, ...]
    fenced_task_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_plan_id": self.current_plan_id,
            "revised_plan_id": self.revised_plan_id,
            "carried_node_ids": list(self.carried_node_ids),
            "preserved_node_ids": list(self.preserved_node_ids),
            "pending_node_ids": list(self.pending_node_ids),
            "cancel_executions": [
                execution.to_dict() for execution in self.cancel_executions
            ],
            "fenced_task_ids": list(self.fenced_task_ids),
        }


class RevisionDispatchReconciler:
    """Reconcile physical executions with one validated replacement graph."""

    def reconcile(
        self,
        *,
        current_graph: MissionTaskGraph,
        revised_graph: MissionTaskGraph,
        executions: Iterable[MissionNodeExecution],
        invalidation_event: MissionExecutionEvent,
    ) -> RevisionDispatchDecision:
        if current_graph.mission_id != revised_graph.mission_id:
            raise ValueError("Revised graph mission does not match current graph")
        if invalidation_event.mission_id != current_graph.mission_id:
            raise ValueError("Invalidation event mission does not match graph")
        if revised_graph.supersedes_plan_id != current_graph.plan_id:
            raise ValueError("Revised graph does not supersede current graph")
        if revised_graph.revision != current_graph.revision + 1:
            raise ValueError("Revised graph revision must increment by one")

        current_nodes = {node.node_id: node for node in current_graph.nodes}
        revised_nodes = {node.node_id: node for node in revised_graph.nodes}
        latest_execution = {
            execution.node_id: execution
            for execution in executions
            if execution.plan_id == current_graph.plan_id
        }

        carried: list[str] = []
        preserved: list[str] = []
        pending: list[str] = []
        cancel_by_task_id: dict[str, MissionNodeExecution] = {}
        fenced_task_ids: set[str] = set()

        for node_id, revised_node in revised_nodes.items():
            current_node = current_nodes.get(node_id)
            execution = latest_execution.get(node_id)
            if (
                current_node is not None
                and execution is not None
                and execution.is_succeeded
                and node_id != invalidation_event.node_id
                and _completion_contract_hash(current_node)
                == _completion_contract_hash(revised_node)
            ):
                carried.append(node_id)
                continue
            if (
                current_node is not None
                and execution is not None
                and execution.is_active
                and _execution_spec_hash(current_node)
                == _execution_spec_hash(revised_node)
            ):
                preserved.append(node_id)
                continue

            pending.append(node_id)
            if execution is not None and execution.task_id is not None:
                fenced_task_ids.add(execution.task_id)
                if execution.is_active:
                    cancel_by_task_id[execution.task_id] = execution

        for node_id, execution in latest_execution.items():
            if node_id in revised_nodes:
                continue
            if execution.task_id is not None:
                fenced_task_ids.add(execution.task_id)
                if execution.is_active:
                    cancel_by_task_id[execution.task_id] = execution

        return RevisionDispatchDecision(
            current_plan_id=current_graph.plan_id,
            revised_plan_id=revised_graph.plan_id,
            carried_node_ids=tuple(sorted(carried)),
            preserved_node_ids=tuple(sorted(preserved)),
            pending_node_ids=tuple(sorted(pending)),
            cancel_executions=tuple(
                cancel_by_task_id[task_id]
                for task_id in sorted(cancel_by_task_id)
            ),
            fenced_task_ids=tuple(sorted(fenced_task_ids)),
        )


@dataclass(frozen=True)
class MissionDispatchCheckpoint:
    mission_id: str
    active_plan_id: str
    revision: int
    node_executions: tuple[MissionNodeExecution, ...]
    task_graph: MissionTaskGraph | None = None
    fenced_task_ids: tuple[str, ...] = ()
    superseded_executions: tuple[MissionNodeExecution, ...] = ()
    supersedes_plan_id: str | None = None
    invalidation_event_id: str | None = None
    memory_command_event_id: str | None = None
    memory_plan_event_id: str | None = None
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.mission_id:
            raise ValueError("mission_id must not be empty")
        if not self.active_plan_id:
            raise ValueError("active_plan_id must not be empty")
        if self.revision < 1:
            raise ValueError("revision must be at least 1")
        node_ids = [execution.node_id for execution in self.node_executions]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("checkpoint cannot contain duplicate node executions")
        superseded_task_ids = [
            execution.task_id
            for execution in self.superseded_executions
            if execution.task_id is not None
        ]
        if len(superseded_task_ids) != len(set(superseded_task_ids)):
            raise ValueError(
                "checkpoint cannot contain duplicate superseded executions"
            )
        if any(
            execution.task_id is None
            for execution in self.superseded_executions
        ):
            raise ValueError(
                "superseded checkpoint executions require task IDs"
            )
        if any(
            execution.task_id not in self.fenced_task_ids
            for execution in self.superseded_executions
        ):
            raise ValueError(
                "superseded checkpoint executions must be fenced"
            )
        if self.task_graph is not None:
            if self.task_graph.mission_id != self.mission_id:
                raise ValueError("checkpoint task graph mission does not match")
            if self.task_graph.plan_id != self.active_plan_id:
                raise ValueError("checkpoint task graph plan does not match")
            if self.task_graph.revision != self.revision:
                raise ValueError("checkpoint task graph revision does not match")
            graph_node_ids = {
                node.node_id for node in self.task_graph.nodes
            }
            if graph_node_ids != set(node_ids):
                raise ValueError(
                    "checkpoint task graph nodes do not match executions"
                )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mission_id": self.mission_id,
            "active_plan_id": self.active_plan_id,
            "revision": self.revision,
            "node_executions": [
                execution.to_dict() for execution in self.node_executions
            ],
            "fenced_task_ids": list(self.fenced_task_ids),
            "superseded_executions": [
                execution.to_dict()
                for execution in self.superseded_executions
            ],
            "updated_at": self.updated_at,
        }
        if self.task_graph is not None:
            result["task_graph"] = self.task_graph.to_dict()
        if self.supersedes_plan_id is not None:
            result["supersedes_plan_id"] = self.supersedes_plan_id
        if self.invalidation_event_id is not None:
            result["invalidation_event_id"] = self.invalidation_event_id
        if self.memory_command_event_id is not None:
            result["memory_command_event_id"] = self.memory_command_event_id
        if self.memory_plan_event_id is not None:
            result["memory_plan_event_id"] = self.memory_plan_event_id
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionDispatchCheckpoint:
        executions = value.get("node_executions")
        if not isinstance(executions, list):
            raise ValueError("node_executions must be a list")
        fenced = value.get("fenced_task_ids", [])
        if not isinstance(fenced, list):
            raise ValueError("fenced_task_ids must be a list")
        superseded = value.get("superseded_executions", [])
        if not isinstance(superseded, list) or any(
            not isinstance(item, dict) for item in superseded
        ):
            raise ValueError(
                "superseded_executions must be a list of objects"
            )
        graph_value = value.get("task_graph")
        if graph_value is not None and not isinstance(graph_value, dict):
            raise ValueError("task_graph must be an object")
        return cls(
            mission_id=str(value.get("mission_id") or ""),
            active_plan_id=str(value.get("active_plan_id") or ""),
            revision=int(value.get("revision") or 0),
            node_executions=tuple(
                MissionNodeExecution.from_dict(item)
                for item in executions
                if isinstance(item, dict)
            ),
            task_graph=(
                MissionTaskGraph.from_dict(graph_value)
                if isinstance(graph_value, dict)
                else None
            ),
            fenced_task_ids=tuple(
                item for item in fenced
                if isinstance(item, str) and item
            ),
            superseded_executions=tuple(
                MissionNodeExecution.from_dict(item)
                for item in superseded
            ),
            supersedes_plan_id=_string_or_none(
                value.get("supersedes_plan_id")
            ),
            invalidation_event_id=_string_or_none(
                value.get("invalidation_event_id")
            ),
            memory_command_event_id=_string_or_none(
                value.get("memory_command_event_id")
            ),
            memory_plan_event_id=_string_or_none(
                value.get("memory_plan_event_id")
            ),
            updated_at=str(value.get("updated_at") or ""),
        )


class JsonlMissionDispatchStore:
    """Append-only checkpoints for revision dispatch recovery and audit."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def append(self, checkpoint: MissionDispatchCheckpoint) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        checkpoint.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                handle.write("\n")

    def latest(self, mission_id: str) -> MissionDispatchCheckpoint | None:
        return self.latest_by_mission().get(mission_id)

    def latest_by_mission(
        self,
    ) -> dict[str, MissionDispatchCheckpoint]:
        with self._lock:
            if not self.path.exists():
                return {}
            latest: dict[str, MissionDispatchCheckpoint] = {}
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(value, dict):
                        continue
                    try:
                        checkpoint = MissionDispatchCheckpoint.from_dict(
                            value
                        )
                    except (TypeError, ValueError):
                        continue
                    latest[checkpoint.mission_id] = checkpoint
            return latest

    def recoverable_latest_by_mission(
        self,
    ) -> dict[str, MissionDispatchCheckpoint]:
        """Read recovery state without silently falling back past corruption."""
        with self._lock:
            if not self.path.exists():
                return {}
            lines = self.path.read_text(encoding="utf-8").splitlines(
                keepends=True
            )
            latest: dict[str, MissionDispatchCheckpoint] = {}
            for index, line in enumerate(lines, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    is_unterminated_tail = (
                        index == len(lines) and not line.endswith("\n")
                    )
                    if is_unterminated_tail:
                        continue
                    raise ValueError(
                        f"Malformed dispatch checkpoint at line {index}."
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Invalid dispatch checkpoint at line {index}."
                    )
                try:
                    checkpoint = MissionDispatchCheckpoint.from_dict(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Invalid dispatch checkpoint at line {index}: {exc}"
                    ) from exc
                latest[checkpoint.mission_id] = checkpoint
            return latest


def checkpoint_for_graph(
    graph: MissionTaskGraph,
    executions: Iterable[MissionNodeExecution],
    *,
    fenced_task_ids: Iterable[str] = (),
    superseded_executions: Iterable[MissionNodeExecution] = (),
    invalidation_event_id: str | None = None,
    memory_command_event_id: str | None = None,
    memory_plan_event_id: str | None = None,
) -> MissionDispatchCheckpoint:
    by_node_id = {execution.node_id: execution for execution in executions}
    now = datetime.now(timezone.utc).isoformat()
    normalized: list[MissionNodeExecution] = []
    for node in graph.nodes:
        execution = by_node_id.get(node.node_id)
        if execution is None:
            execution = MissionNodeExecution(
                plan_id=graph.plan_id,
                node_id=node.node_id,
                robot_id=node.robot_id,
                status="pending",
                node_spec_hash=_execution_spec_hash(node),
                updated_at=now,
            )
        normalized.append(execution)
    return MissionDispatchCheckpoint(
        mission_id=graph.mission_id,
        active_plan_id=graph.plan_id,
        revision=graph.revision,
        task_graph=graph,
        supersedes_plan_id=graph.supersedes_plan_id,
        invalidation_event_id=invalidation_event_id,
        memory_command_event_id=memory_command_event_id,
        memory_plan_event_id=memory_plan_event_id,
        node_executions=tuple(normalized),
        fenced_task_ids=tuple(sorted(set(fenced_task_ids))),
        superseded_executions=tuple(superseded_executions),
        updated_at=now,
    )


def execution_spec_hash(node: MissionTaskNode) -> str:
    return _execution_spec_hash(node)


def _execution_spec_hash(node: MissionTaskNode) -> str:
    return _stable_hash(node.to_dict())


def _completion_contract_hash(node: MissionTaskNode) -> str:
    return _stable_hash({
        "node_id": node.node_id,
        "task_type": node.task_type,
        "command": node.command,
        "capability_required": node.capability_required,
        "target": node.target.to_dict(),
        "completion_goal": node.completion_goal,
        "expected_effects": list(node.expected_effects),
        "success_evidence": [
            evidence.to_dict() for evidence in node.success_evidence
        ],
        "risk_level": node.risk_level,
    })


def _stable_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: Any, name: str) -> int:
    if value is None:
        return 0
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(f"{name} must be a non-negative integer")
    return value
