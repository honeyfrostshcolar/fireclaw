from __future__ import annotations

import logging
import time
from dataclasses import asdict, is_dataclass, replace
from typing import Any, Protocol
from uuid import uuid4
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.agent.loop_checkpoint import AgentLoopCheckpointStore
from fireclaw_core.gateway.control import (
    ControlPolicy,
    OperatorContext,
    operator_from_payload,
)
from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.mission_memory_facade import MemoryAccessContext
from fireclaw_core.memory.mission_memory_tools import (
    CURRENT_CONTEXT_TOOL,
    MissionMemoryTools,
)
from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
from fireclaw_core.memory.planner_memory_context import (
    PlannerMemoryContextBuilder,
    PlannerMemoryContextRequest,
    PlannerMemoryContextResult,
)
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
)
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.active_observation import (
    ActiveObservationError,
    MissionActiveObservationLimits,
    MissionObservationCompiler,
    TERMINAL_OBSERVATION_FAILURE_STATUSES,
    TERMINAL_OBSERVATION_SUCCESS_STATUSES,
    environment_fact_from_observation_trace,
    observation_trace_status,
)
from fireclaw_core.mission.mission_planning_audit import (
    GuardDecision,
    MissionPlanningAuditRecord,
    MissionPlanningAuditSink,
    append_guard_decision,
)
from fireclaw_core.mission.mission_planner import MissionPlannerContext, MissionPlanningResult, MissionSubtask
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission.mission_registry import TERMINAL_SUBTASK_STATUSES
from fireclaw_core.planner.planner import RuleBasedPlanner
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.infra.session_lineage import JsonlSessionLineageStore, MissionSessionLineage
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationResult,
    MissionDeliberationRuntime,
    PlannerDeliberationPolicy,
)
from fireclaw_core.mission.execution_event import MissionExecutionEvent
from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission.mission_plan_revision import (
    MissionPlanRevisionCoordinator,
)
from fireclaw_core.mission.mission_state import (
    MissionStateSnapshot,
    MissionStateSnapshotBuilder,
    MissionStateSnapshotValidator,
)
from fireclaw_core.mission.task_graph import (
    MissionTaskGraph,
    MissionTaskGraphValidator,
)
from fireclaw_core.task.task_contract import MemoryLineage, structured_task_from_mission_subtask
from fireclaw_core.task.task_flow_registry import JsonlTaskFlowRegistryStore, TaskFlowRecord
from fireclaw_core.task.terminal_outcome import (
    robot_task_status_from_trace,
)


class SubagentClient(Protocol):
    def submit_task(self, entry: RobotRegistryEntry, **kwargs: Any) -> dict[str, Any]:
        ...

    def get_task_trace(self, entry: RobotRegistryEntry, task_id: str) -> dict[str, Any]:
        ...

    def cancel_task(
        self,
        entry: RobotRegistryEntry,
        task_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def get_events(
        self,
        entry: RobotRegistryEntry,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    def check_presence(self, entry: RobotRegistryEntry) -> dict[str, Any]:
        ...


class MissionAgent:
    def __init__(
        self,
        *,
        registry: RobotRegistry,
        subagent_client: SubagentClient | None = None,
        mission_registry: JsonlMissionRegistry | None = None,
        planner: Any | None = None,
        control_policy: ControlPolicy | None = None,
        operator: OperatorContext | None = None,
        mission_memory: MissionMemoryStore | None = None,
        memory_retriever: Any | None = None,
        approval_store: JsonlApprovalStore | None = None,
        plugin_runtime: Any | None = None,
        task_registry: Any | None = None,
        subagent_registry: Any | None = None,
        session_lineage_store: JsonlSessionLineageStore | None = None,
        task_flow_store: JsonlTaskFlowRegistryStore | None = None,
        profile_skill_chains_by_robot: dict[str, dict[str, tuple[str, ...]]] | None = None,
        primitive_skills_by_robot: dict[str, tuple[str, ...]] | None = None,
        mission_planning_audit_sink: MissionPlanningAuditSink | None = None,
        embodied_memory_producer: EmbodiedMemoryProducer | None = None,
        approval_memory_producer: EmbodiedMemoryProducer | None = None,
        embodied_runtime_mode: str | None = None,
        embodied_working_memory: EmbodiedWorkingMemory | None = None,
        mission_memory_tools: MissionMemoryTools | None = None,
        memory_lifecycle: MissionMemoryLifecycleStore | None = None,
        planner_memory_context_builder: PlannerMemoryContextBuilder | None = None,
        mission_state_snapshot_builder: MissionStateSnapshotBuilder | None = None,
        mission_deliberation_runtime: MissionDeliberationRuntime | None = None,
        agent_loop_checkpoint_store: (
            AgentLoopCheckpointStore | None
        ) = None,
        mission_plan_revision_coordinator: MissionPlanRevisionCoordinator | None = None,
        active_observation_limits: MissionActiveObservationLimits | None = None,
        consolidation_coordinator: Any | None = None,
        working_memory_hydration_report: Any | None = None,
    ) -> None:
        self.registry = registry
        self.profile_skill_chains_by_robot = profile_skill_chains_by_robot or {}
        self.primitive_skills_by_robot = primitive_skills_by_robot or {}
        if subagent_client is not None:
            self.subagent_client = subagent_client
        else:
            from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
            client_kwargs: dict[str, Any] = {}
            if subagent_registry is not None:
                client_kwargs["registry"] = subagent_registry
            self.subagent_client = RobotSubagentClient(**client_kwargs)
        self.mission_registry = mission_registry
        self.planner = planner
        planner_policy = (
            planner
            if (
                planner is not None
                and getattr(
                    planner,
                    "supports_mission_deliberation",
                    False,
                )
                is True
            )
            else PlannerDeliberationPolicy(planner)
            if planner is not None
            else None
        )
        self.mission_deliberation_runtime = (
            mission_deliberation_runtime
            or (
                MissionDeliberationRuntime(
                    registry=registry,
                    policy=planner_policy,
                    checkpoint_store=agent_loop_checkpoint_store,
                    agent_tool_runtime=getattr(
                        planner_policy,
                        "agent_tool_runtime",
                        None,
                    ),
                )
                if planner_policy is not None
                else None
            )
        )
        self.control_policy = control_policy
        self.operator = operator
        self.mission_memory = mission_memory
        self.memory_retriever = memory_retriever
        self.approval_store = approval_store
        self.plugin_runtime = plugin_runtime
        self.task_registry = task_registry
        self.mission_state_snapshot_builder = (
            mission_state_snapshot_builder
            or MissionStateSnapshotBuilder(
                registry=registry,
                task_registry=task_registry,
            )
        )
        self.mission_plan_revision_coordinator = (
            mission_plan_revision_coordinator
            or (
                MissionPlanRevisionCoordinator(
                    registry=registry,
                    state_snapshot_builder=self.mission_state_snapshot_builder,
                    deliberation_runtime=self.mission_deliberation_runtime,
                )
                if self.mission_deliberation_runtime is not None
                else None
            )
        )
        self.active_observation_limits = (
            active_observation_limits or MissionActiveObservationLimits()
        )
        self.mission_observation_compiler = MissionObservationCompiler(
            registry
        )
        self._latest_mission_state_refs: dict[str, tuple[int, str]] = {}
        self._active_task_graphs: dict[str, MissionTaskGraph] = {}
        self._revision_event_memory_refs: dict[tuple[str, str], str | None] = {}
        self._mission_reports: dict[str, dict[str, Any]] = {}
        self.dispatch_recovery_report: list[dict[str, Any]] = []
        self.subagent_registry = subagent_registry
        self._session_lineage_store = session_lineage_store
        self._task_flow_store = task_flow_store
        self.mission_planning_audit_sink = mission_planning_audit_sink
        if embodied_memory_producer is not None:
            if embodied_memory_producer.producer_type != "mission_agent":
                raise ValueError("MissionAgent requires a mission_agent embodied-memory producer")
            if embodied_runtime_mode not in MEMORY_RUNTIME_MODES:
                raise ValueError(
                    "MissionAgent requires embodied_runtime_mode to be one of: "
                    f"{sorted(MEMORY_RUNTIME_MODES)}"
                )
        if approval_memory_producer is not None:
            if approval_memory_producer.producer_type != "approval_runtime":
                raise ValueError("MissionAgent requires an approval_runtime memory producer")
            if embodied_runtime_mode not in MEMORY_RUNTIME_MODES:
                raise ValueError(
                    "MissionAgent requires embodied_runtime_mode to be one of: "
                    f"{sorted(MEMORY_RUNTIME_MODES)}"
                )
        self.embodied_memory_producer = embodied_memory_producer
        self.approval_memory_producer = approval_memory_producer
        self.embodied_runtime_mode = embodied_runtime_mode
        self.embodied_working_memory = embodied_working_memory
        self.mission_memory_tools = mission_memory_tools
        self.memory_lifecycle = memory_lifecycle
        self.consolidation_coordinator = consolidation_coordinator
        self.planner_memory_context_builder = (
            planner_memory_context_builder
            or PlannerMemoryContextBuilder(
                memory_retriever=memory_retriever,
                mission_memory=mission_memory,
                facade=(
                    mission_memory_tools.facade
                    if mission_memory_tools is not None
                    else None
                ),
                lifecycle=memory_lifecycle,
                plugin_runtime=plugin_runtime,
            )
        )
        self.working_memory_hydration_report = working_memory_hydration_report

    @property
    def session_lineage_store(self) -> JsonlSessionLineageStore | None:
        return self._session_lineage_store

    @property
    def embodied_memory_store(self) -> EmbodiedMemoryStore | None:
        if self.embodied_memory_producer is None:
            return None
        return self.embodied_memory_producer.store

    def memory_tool_definitions(self) -> list[dict[str, Any]]:
        if self.mission_memory_tools is None:
            return []
        return self.mission_memory_tools.tool_schemas()

    def call_memory_tool(
        self,
        *,
        mission_id: str,
        name: str,
        arguments: dict[str, Any],
        requester_id: str,
        scopes: frozenset[str],
    ) -> dict[str, Any]:
        if self.mission_memory_tools is None or self.embodied_runtime_mode is None:
            return {
                "status": "not_configured",
                "mission_id": mission_id,
                "advisory_only": True,
            }
        access = MemoryAccessContext(
            mission_id=mission_id,
            runtime_mode=self.embodied_runtime_mode,
            requester_id=requester_id,
            scopes=scopes,
        )
        return self.mission_memory_tools.execute(name, arguments, access=access)

    def _authorize(
        self,
        action: str,
        *,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any] | None:
        """Check mission-level authorization. Returns deny dict if denied, None if allowed."""
        resolved_operator = operator or self.operator
        if self.control_policy is None or resolved_operator is None:
            return None
        decision = self.control_policy.evaluate(resolved_operator, action)
        if decision.status == "deny":
            return {
                "status": "denied",
                "message": (
                    f"Operator {resolved_operator.operator_id} lacks required scope: "
                    f"{action}"
                ),
                "decision": decision.to_dict(),
            }
        return None

    def _record_mission_memory(
        self,
        mission_id: str,
        record_type: str,
        content: dict[str, Any],
        *,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        force: bool = False,
    ) -> None:
        """Record a memory entry if mission_memory is configured."""
        if self.mission_memory is None or (self.embodied_memory_producer is not None and not force):
            return
        record = MissionMemoryRecord(
            record_id=uuid4().hex[:12],
            mission_id=mission_id,
            record_type=record_type,
            content=content,
            robot_id=robot_id,
            subtask_id=subtask_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            self.mission_memory.append(record)
        except Exception:
            logger.warning("Failed to write mission memory record", exc_info=True)

    def _record_embodied_memory(
        self,
        mission_id: str,
        event_type: str,
        evidence_kind: str,
        content: dict[str, Any],
        *,
        source_type: str,
        source_id: str | None = None,
        method_id: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        derived_from: tuple[str, ...] = (),
        observed_at: str | None = None,
    ) -> str | None:
        """Record a policy-checked embodied event without blocking mission execution."""
        if self.embodied_memory_producer is None or self.embodied_runtime_mode is None:
            return None
        try:
            event = self.embodied_memory_producer.record_event(
                mission_id=mission_id,
                event_type=event_type,
                evidence_kind=evidence_kind,
                payload=content,
                runtime_mode=self.embodied_runtime_mode,
                source_type=source_type,
                source_id=source_id,
                method_id=method_id,
                robot_id=robot_id,
                subtask_id=subtask_id,
                derived_from=derived_from,
                observed_at=observed_at,
            )
        except Exception:
            logger.warning("Failed to write embodied mission memory event", exc_info=True)
            self._record_mission_memory(
                mission_id,
                event_type,
                content,
                robot_id=robot_id,
                subtask_id=subtask_id,
                force=True,
            )
            return None
        return event.event_id

    def _add_embodied_relation(
        self,
        mission_id: str,
        source_event_id: str | None,
        target_event_id: str | None,
        relation_type: str,
    ) -> None:
        if (
            self.embodied_memory_producer is None
            or self.embodied_runtime_mode is None
            or source_event_id is None
            or target_event_id is None
        ):
            return
        try:
            self.embodied_memory_producer.add_relation(
                mission_id=mission_id,
                source_record_id=source_event_id,
                target_record_id=target_event_id,
                relation_type=relation_type,
                runtime_mode=self.embodied_runtime_mode,
            )
        except Exception:
            logger.warning("Failed to write embodied mission memory relation", exc_info=True)

    def _record_terminal_outcome(
        self,
        mission_id: str,
        *,
        terminal_event_id: str,
        terminal_status: str,
        robot_id: str | None = None,
        subtask_id: str | None = None,
    ) -> None:
        """Record a terminal outcome event and queue a consolidation boundary.

        Uses a deterministic key so repeated observation is idempotent.
        """
        if self.embodied_memory_producer is None or self.embodied_runtime_mode is None:
            return
        if self.consolidation_coordinator is None:
            return
        try:
            # Append a terminal outcome event
            outcome_event_id = self._record_embodied_memory(
                mission_id,
                "outcome",
                "runtime_evidence",
                {
                    "terminal_status": terminal_status,
                    "terminal_event_id": terminal_event_id,
                    "robot_id": robot_id,
                    "subtask_id": subtask_id,
                },
                source_type="terminal_transition",
                robot_id=robot_id,
                subtask_id=subtask_id,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            if outcome_event_id is None:
                return
            # Get the current sequence from the store
            store = self.embodied_memory_producer.store
            all_events = store.list_events(mission_id=mission_id)
            through_sequence = len(all_events)
            # Queue a boundary
            self.consolidation_coordinator.request_terminal_boundary(
                mission_id=mission_id,
                runtime_mode=self.embodied_runtime_mode,
                scope_kind="subtask" if subtask_id is not None else "mission",
                robot_id=robot_id,
                subtask_id=subtask_id,
                terminal_event_id=terminal_event_id,
                terminal_status=terminal_status,
                through_sequence=through_sequence,
            )
        except Exception:
            logger.warning("Failed to record terminal outcome or queue boundary", exc_info=True)

    def _operator_source_id(self, operator: dict[str, Any] | None = None) -> str:
        if isinstance(operator, dict):
            value = operator.get("operator_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        if self.operator is not None and self.operator.operator_id.strip():
            return self.operator.operator_id.strip()
        return "unknown-operator"

    def _planner_method_id(self) -> str:
        planner_owner = (
            self.planner
            if self.planner is not None
            else self.mission_deliberation_runtime
        )
        planner_type = type(planner_owner)
        return f"{planner_type.__module__}.{planner_type.__qualname__}"

    def _latest_mission_state_ref(self, mission_id: str) -> tuple[int, str] | None:
        latest = self._latest_mission_state_refs.get(mission_id)
        if self.mission_memory is None:
            return latest
        try:
            records = self.mission_memory.list_records(
                mission_id=mission_id,
                record_type="observation",
            )
        except Exception:
            logger.warning("Failed to read prior mission state snapshots", exc_info=True)
            return latest
        for record in records:
            content = record.content if isinstance(record.content, dict) else {}
            if content.get("artifact_type") != "mission_state_snapshot":
                continue
            snapshot = content.get("snapshot")
            if not isinstance(snapshot, dict):
                continue
            version = snapshot.get("version")
            snapshot_id = snapshot.get("snapshot_id")
            if (
                isinstance(version, int)
                and version > 0
                and isinstance(snapshot_id, str)
                and snapshot_id
                and (latest is None or version > latest[0])
            ):
                latest = (version, snapshot_id)
        if latest is not None:
            self._latest_mission_state_refs[mission_id] = latest
        return latest

    def _build_mission_state_snapshot(
        self,
        mission_id: str,
        presence: dict[str, dict[str, Any]],
    ) -> MissionStateSnapshot:
        previous = self._latest_mission_state_ref(mission_id)
        return self.mission_state_snapshot_builder.build(
            mission_id=mission_id,
            presence=presence,
            version=1 if previous is None else previous[0] + 1,
            previous_snapshot_id=None if previous is None else previous[1],
        )

    def refresh_mission_state_snapshot(
        self,
        mission_id: str,
    ) -> MissionStateSnapshot:
        """Capture, validate, and persist current state for dispatch gates."""
        snapshot = self._build_mission_state_snapshot(
            mission_id,
            self.check_fleet_presence(),
        )
        errors = MissionStateSnapshotValidator().validate(snapshot)
        if errors:
            raise ValueError(
                "Dispatch state snapshot failed validation: "
                + "; ".join(errors)
            )
        self._record_mission_state_snapshot(snapshot)
        return snapshot

    def _record_mission_state_snapshot(
        self,
        snapshot: MissionStateSnapshot,
    ) -> str | None:
        payload = {
            "artifact_type": "mission_state_snapshot",
            "snapshot": snapshot.to_dict(),
        }
        event_id = self._record_embodied_memory(
            snapshot.mission_id,
            "observation",
            "runtime_evidence",
            payload,
            source_type="mission_state_projection",
            observed_at=snapshot.captured_at,
        )
        if self.embodied_memory_producer is None:
            self._record_mission_memory(
                snapshot.mission_id,
                "observation",
                payload,
            )
        self._latest_mission_state_refs[snapshot.mission_id] = (
            snapshot.version,
            snapshot.snapshot_id,
        )
        return event_id

    def _record_mission_deliberation(
        self,
        result: MissionDeliberationResult,
        *,
        derived_from: tuple[str, ...],
    ) -> str | None:
        payload = {
            "artifact_type": "mission_deliberation_trace",
            "deliberation": result.to_dict(),
        }
        event_id = self._record_embodied_memory(
            result.mission_id,
            "plan",
            "cognitive_artifact",
            payload,
            source_type="mission_deliberation_runtime",
            method_id=self._planner_method_id(),
            derived_from=derived_from,
        )
        if self.embodied_memory_producer is None:
            self._record_mission_memory(
                result.mission_id,
                "plan",
                payload,
            )
        return event_id

    def _run_active_observation_loop(
        self,
        *,
        mission_id: str,
        command: str,
        operator: dict[str, Any] | None,
        memory_context_result: PlannerMemoryContextResult,
        command_event_id: str | None,
        state_snapshot: MissionStateSnapshot,
        state_snapshot_event_id: str | None,
        deliberation_result: MissionDeliberationResult,
    ) -> tuple[
        MissionDeliberationResult,
        MissionStateSnapshot,
        str | None,
        set[str],
        list[dict[str, Any]],
        str | None,
    ]:
        rounds: list[dict[str, Any]] = []
        online_robot_ids = {
            robot.robot_id
            for robot in state_snapshot.robots
            if robot.online and not robot.stale
        }
        while deliberation_result.status == "observation_required":
            deliberation_event_id = self._record_mission_deliberation(
                deliberation_result,
                derived_from=tuple(
                    item
                    for item in (
                        command_event_id,
                        state_snapshot_event_id,
                    )
                    if item is not None
                ),
            )
            if len(rounds) >= self.active_observation_limits.max_rounds:
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    "Mission planning exceeded its active observation limit.",
                )
            observation_request = deliberation_result.observation_request
            if observation_request is None:
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    "Mission deliberation returned no active observation request.",
                )
            try:
                compiled = self.mission_observation_compiler.compile(
                    observation_request,
                    snapshot=state_snapshot,
                )
            except ActiveObservationError as exc:
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    str(exc),
                )
            round_number = len(rounds) + 1
            dispatch = self.submit_subtask(
                compiled.robot_id,
                compiled.subtask.command,
                session_id=mission_id,
                dedupe_key=(
                    f"{mission_id}-active-observation-"
                    f"{round_number}-{compiled.subtask.node_id}"
                ),
                operator=operator,
                mission={
                    "mission_id": mission_id,
                    "purpose": "active_observation",
                    "belief_id": observation_request.belief_id,
                },
                mission_subtask=compiled.subtask,
                mission_node_id=compiled.subtask.node_id,
                memory_command_event_id=command_event_id,
                memory_plan_event_id=deliberation_event_id,
            )
            task_id = dispatch.get("task_id")
            round_record: dict[str, Any] = {
                "round": round_number,
                "snapshot_id": state_snapshot.snapshot_id,
                "request": observation_request.to_dict(),
                "compiled": compiled.to_dict(),
                "dispatch": dispatch,
            }
            rounds.append(round_record)
            if not isinstance(task_id, str) or not task_id:
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    "Active observation dispatch returned no task_id.",
                )
            trace = dispatch.get("subagent_result")
            if not isinstance(trace, dict):
                trace = {}
            status = observation_trace_status(trace)
            deadline = (
                time.monotonic()
                + self.active_observation_limits.timeout_seconds
            )
            entry = self.registry.get(compiled.robot_id)
            if entry is None:
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    "Active observation robot is no longer registered.",
                )
            while (
                status not in TERMINAL_OBSERVATION_SUCCESS_STATUSES
                and status not in TERMINAL_OBSERVATION_FAILURE_STATUSES
                and time.monotonic() < deadline
            ):
                try:
                    trace = self.subagent_client.get_task_trace(
                        entry,
                        task_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to poll active observation task"
                    )
                    trace = {"status": "unknown"}
                status = observation_trace_status(trace)
                if (
                    status not in TERMINAL_OBSERVATION_SUCCESS_STATUSES
                    and status
                    not in TERMINAL_OBSERVATION_FAILURE_STATUSES
                ):
                    time.sleep(
                        self.active_observation_limits.poll_interval_seconds
                    )
            round_record["terminal_status"] = status
            if status not in TERMINAL_OBSERVATION_SUCCESS_STATUSES:
                reason = (
                    "Active observation task timed out."
                    if status not in TERMINAL_OBSERVATION_FAILURE_STATUSES
                    else f"Active observation task ended with status {status}."
                )
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    reason,
                )
            try:
                fact = environment_fact_from_observation_trace(
                    trace,
                    compiled=compiled,
                    mission_id=mission_id,
                    task_id=task_id,
                )
            except ActiveObservationError as exc:
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    str(exc),
                )
            round_record["environment_fact"] = fact.to_dict()
            presence = self.check_fleet_presence()
            online_robot_ids = {
                robot_id
                for robot_id, info in presence.items()
                if info.get("online")
            }
            try:
                refreshed = self._build_mission_state_snapshot(
                    mission_id,
                    presence,
                )
                facts_by_id = {
                    item.fact_id: item
                    for item in (
                        *state_snapshot.environment_facts,
                        *refreshed.environment_facts,
                        fact,
                    )
                }
                state_snapshot = (
                    self.mission_state_snapshot_builder.with_environment_facts(
                        refreshed,
                        tuple(facts_by_id.values()),
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to refresh state after active observation"
                )
                return (
                    deliberation_result,
                    state_snapshot,
                    state_snapshot_event_id,
                    online_robot_ids,
                    rounds,
                    "Active observation succeeded but the new mission state "
                    "snapshot could not be built.",
                )
            state_snapshot_event_id = (
                self._record_mission_state_snapshot(state_snapshot)
            )
            round_record["next_snapshot_id"] = state_snapshot.snapshot_id
            context = MissionPlannerContext(
                available_robots=[
                    entry
                    for entry in self.registry.enabled_entries()
                    if entry.robot_id in online_robot_ids
                ],
                state_snapshot=state_snapshot.to_dict(),
                retrieved_memories=list(
                    memory_context_result.memories
                ),
                operator_corrections=list(
                    memory_context_result.corrections
                ),
                external_knowledge=list(
                    memory_context_result.external_knowledge
                ),
            )
            assert self.mission_deliberation_runtime is not None
            deliberation_result = (
                self.mission_deliberation_runtime.deliberate(
                    mission_id=mission_id,
                    command=command,
                    state_snapshot=state_snapshot,
                    planner_context=context,
                )
            )
        return (
            deliberation_result,
            state_snapshot,
            state_snapshot_event_id,
            online_robot_ids,
            rounds,
            None,
        )

    def active_task_graph(self, mission_id: str) -> MissionTaskGraph | None:
        return self._active_task_graphs.get(mission_id)

    def restore_active_task_graph(
        self,
        graph: MissionTaskGraph,
    ) -> list[str]:
        """Hydrate one validated graph without invoking the planner."""
        errors = MissionTaskGraphValidator().validate(graph, self.registry)
        existing = self._active_task_graphs.get(graph.mission_id)
        if existing is not None:
            if existing.revision > graph.revision:
                errors.append(
                    "Persisted task graph is older than the active graph."
                )
            elif (
                existing.revision == graph.revision
                and existing.plan_id != graph.plan_id
            ):
                errors.append(
                    "Persisted task graph conflicts with the active revision."
                )
        if errors:
            return list(dict.fromkeys(errors))
        self._active_task_graphs[graph.mission_id] = graph
        return []

    def resume_pending_dispatches(self) -> list[dict[str, Any]]:
        """Recover persisted dispatches under the runtime operator authority."""
        from fireclaw_core.mission.mission_scheduler import MissionScheduler

        operator = self.operator.to_dict() if self.operator is not None else None
        try:
            report = MissionScheduler(
                mission_agent=self,
            ).resume_pending_dispatches(operator=operator)
        except Exception as exc:
            logger.exception("Mission dispatch startup recovery failed")
            report = [{
                "status": "blocked",
                "message": (
                    "Mission dispatch startup recovery failed: "
                    f"{exc}"
                ),
                "recovered": False,
            }]
        self.dispatch_recovery_report = report
        return report

    def handle_execution_event(
        self,
        event: MissionExecutionEvent,
    ) -> dict[str, Any]:
        """Classify one trusted execution event and produce a safe plan revision."""
        if not isinstance(event, MissionExecutionEvent):
            return {
                "status": "blocked",
                "message": "Execution event must use MissionExecutionEvent.",
            }
        graph = self._active_task_graphs.get(event.mission_id)
        if graph is None:
            return {
                "status": "blocked",
                "message": "No active mission task graph is available for revision.",
                "event": event.to_dict(),
            }
        if self.mission_plan_revision_coordinator is None:
            return {
                "status": "blocked",
                "message": "Mission plan revision coordinator is not configured.",
                "event": event.to_dict(),
            }

        event_key = (event.mission_id, event.event_id)
        if event_key not in self._revision_event_memory_refs:
            event_event_id = self._record_embodied_memory(
                event.mission_id,
                "outcome",
                "runtime_evidence",
                {
                    "artifact_type": "mission_execution_event",
                    "event": event.to_dict(),
                },
                source_type=event.source_type,
                robot_id=event.robot_id,
                subtask_id=event.task_id,
                observed_at=event.observed_at,
            )
            if self.embodied_memory_producer is None:
                self._record_mission_memory(
                    event.mission_id,
                    "outcome",
                    {
                        "artifact_type": "mission_execution_event",
                        "event": event.to_dict(),
                    },
                    robot_id=event.robot_id,
                    subtask_id=event.task_id,
                )
            self._revision_event_memory_refs[event_key] = event_event_id
        event_event_id = self._revision_event_memory_refs[event_key]

        previous = self._latest_mission_state_ref(event.mission_id)
        if previous is None:
            return {
                "status": "blocked",
                "message": "Active plan has no recorded state snapshot lineage.",
                "event": event.to_dict(),
            }
        presence = self.check_fleet_presence()
        memory_context_result = self._build_planner_memory_context(
            graph.command,
            mission_id=event.mission_id,
        )
        planner_context = MissionPlannerContext(
            retrieved_memories=list(memory_context_result.memories),
            operator_corrections=list(memory_context_result.corrections),
            external_knowledge=list(memory_context_result.external_knowledge),
        )
        result = self.mission_plan_revision_coordinator.coordinate(
            event=event,
            current_graph=graph,
            presence=presence,
            planner_context=planner_context,
            snapshot_version=previous[0] + 1,
            previous_snapshot_id=previous[1],
        )
        snapshot_event_id: str | None = None
        if result.state_snapshot is not None:
            snapshot_event_id = self._record_mission_state_snapshot(
                result.state_snapshot
            )
        if result.deliberation_result is not None:
            deliberation_event_id = self._record_mission_deliberation(
                result.deliberation_result,
                derived_from=tuple(
                    item
                    for item in (event_event_id, snapshot_event_id)
                    if item is not None
                ),
            )
        else:
            deliberation_event_id = None
        revision_event_id: str | None = None
        if result.status == "revised" and result.revised_task_graph is not None:
            planning_result = (
                result.deliberation_result.planning_result
                if result.deliberation_result is not None
                else None
            )
            audit_record = (
                planning_result.audit_record
                if planning_result is not None
                else None
            )
            if audit_record is not None:
                audit_record = append_guard_decision(
                    audit_record,
                    memory_context_result.guard_decision(),
                    final_status=audit_record.final_status,
                    final_message=audit_record.final_message,
                    mission_id=event.mission_id,
                )
                audit_record = self._with_validator_decision(
                    audit_record,
                    validation_errors=[],
                    final_status=(
                        planning_result.status
                        if planning_result is not None
                        else "planned"
                    ),
                    final_message=result.message,
                    mission_id=event.mission_id,
                )
            audit_error = self._record_mission_planning_audit(audit_record)
            if audit_error is not None:
                response = result.to_dict()
                response["status"] = "blocked"
                response["message"] = audit_error
                return response

            revision_payload = {
                "artifact_type": "mission_plan_revision",
                "event": event.to_dict(),
                "supersedes_plan_id": graph.plan_id,
                "task_graph": result.revised_task_graph.to_dict(),
                "deliberation_run_id": (
                    result.deliberation_result.run_id
                    if result.deliberation_result is not None
                    else None
                ),
            }
            revision_event_id = self._record_embodied_memory(
                event.mission_id,
                "plan",
                "cognitive_artifact",
                revision_payload,
                source_type="mission_plan_revision_coordinator",
                method_id=self._planner_method_id(),
                derived_from=tuple(
                    item
                    for item in (
                        event_event_id,
                        snapshot_event_id,
                        deliberation_event_id,
                    )
                    if item is not None
                ),
            )
            if self.embodied_memory_producer is None:
                self._record_mission_memory(
                    event.mission_id,
                    "plan",
                    revision_payload,
                )
            self._add_embodied_relation(
                event.mission_id,
                revision_event_id,
                event_event_id,
                "caused_by",
            )
            self._active_task_graphs[event.mission_id] = (
                result.revised_task_graph
            )
        response = result.to_dict()
        if revision_event_id is not None:
            response["revision_memory_event_id"] = revision_event_id
        return response

    def _record_approval_memory(
        self,
        mission_id: str,
        content: dict[str, Any],
        *,
        evidence_kind: str,
        source_id: str | None = None,
        observed_at: str | None = None,
    ) -> str | None:
        if self.approval_memory_producer is None or self.embodied_runtime_mode is None:
            return None
        try:
            event = self.approval_memory_producer.record_event(
                mission_id=mission_id,
                event_type="safety_decision",
                evidence_kind=evidence_kind,
                payload=content,
                runtime_mode=self.embodied_runtime_mode,
                source_type="approval_runtime",
                source_id=source_id,
                observed_at=observed_at,
            )
        except Exception:
            logger.warning("Failed to write embodied approval memory event", exc_info=True)
            self._record_mission_memory(
                mission_id,
                "safety_decision",
                content,
                force=True,
            )
            return None
        return event.event_id

    def _record_mission_planning_audit(
        self,
        record: MissionPlanningAuditRecord | None,
    ) -> str | None:
        if record is None or self.mission_planning_audit_sink is None:
            return None
        try:
            self.mission_planning_audit_sink.record(record)
            return None
        except Exception:
            logger.warning("Failed to record mission planning audit", exc_info=True)
            return "Mission planning audit could not be recorded."

    def _with_validator_decision(
        self,
        record: MissionPlanningAuditRecord | None,
        *,
        validation_errors: list[str],
        final_status: str,
        final_message: str,
        mission_id: str | None,
    ) -> MissionPlanningAuditRecord | None:
        if record is None:
            return None
        if validation_errors:
            decision = GuardDecision(
                layer="validator",
                status="block",
                reason="mission_plan_invalid",
                message="Mission plan failed deterministic validation.",
                details={"errors": list(validation_errors)},
            )
        else:
            decision = GuardDecision(
                layer="validator",
                status="allow",
                reason="mission_plan_valid",
                message="Mission plan passed deterministic validation.",
            )
        return append_guard_decision(
            record,
            decision,
            final_status=final_status,
            final_message=final_message,
            mission_id=mission_id,
        )

    def check_fleet_presence(self) -> dict[str, dict[str, Any]]:
        """Check presence of all enabled robots. Updates registry with last_seen_at.

        For robots that don't respond, marks them as stale if their heartbeat
        has expired.
        """
        results = {}
        for entry in self.registry.enabled_entries(include_stale=True):
            result = self.subagent_client.check_presence(entry)
            if not result["online"]:
                result["stale"] = self.registry.is_stale(entry.robot_id)
            results[entry.robot_id] = result
            if result["online"]:
                self.registry.update_presence(entry.robot_id, result["last_seen_at"])
        return results

    def submit_subtask(
        self,
        robot_id: str,
        command: str,
        *,
        session_id: str | None = None,
        dedupe_key: str | None = None,
        operator: dict[str, Any] | None = None,
        mission: dict[str, Any] | None = None,
        mission_subtask: MissionSubtask | None = None,
        mission_node_id: str | None = None,
        memory_command_event_id: str | None = None,
        memory_plan_event_id: str | None = None,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.submit")
        if deny is not None:
            return {**deny, "robot_id": robot_id, "subtasks": []}
        entry = self.registry.get(robot_id)
        if entry is None:
            return {
                "status": "not_found",
                "robot_id": robot_id,
                "message": "Robot Agent is not registered.",
                "subtasks": [],
            }
        if not entry.enabled:
            return {
                "status": "disabled",
                "robot_id": robot_id,
                "message": "Robot Agent is disabled.",
                "subtasks": [],
            }
        mission_id = _mission_id(session_id)
        created_at = datetime.now(timezone.utc).isoformat()
        if self.mission_registry is not None and self.mission_registry.get_mission(mission_id) is None:
            self.mission_registry.create_mission(
                mission_id=mission_id,
                session_id=session_id,
                command=command,
                created_at=created_at,
            )
        # Generate structured task from MissionSubtask if provided,
        # otherwise fall back to command text re-parsing.
        structured_task = None
        if mission_subtask is not None:
            structured_task = structured_task_from_mission_subtask(
                mission_id=mission_id,
                subtask=mission_subtask,
                operator_id=(operator or {}).get("operator_id") if isinstance(operator, dict) else None,
                task_id=mission_node_id,
                capability_skill_chains=self.profile_skill_chains_by_robot.get(robot_id),
            ).to_dict()
        else:
            planner = RuleBasedPlanner()
            point = planner._extract_point(command)
            floor = planner._extract_floor(command)
            capability = _capability_from_entry(entry)
            if point is not None and capability != "unknown":
                pose = dict(point)
                frame_id = str(pose.pop("frame_id", "map"))
                generated_subtask = MissionSubtask(
                    robot_id=robot_id,
                    command=command,
                    floor=None,
                    capability_required=capability,
                    execution_group=0,
                    target={"frame_id": frame_id, "pose": pose},
                )
                structured_task = structured_task_from_mission_subtask(
                    mission_id=mission_id,
                    subtask=generated_subtask,
                    operator_id=(operator or {}).get("operator_id") if isinstance(operator, dict) else None,
                    task_id=mission_node_id,
                    capability_skill_chains=self.profile_skill_chains_by_robot.get(robot_id),
                ).to_dict()
            elif floor is not None and capability != "unknown":
                generated_subtask = MissionSubtask(
                    robot_id=robot_id,
                    command=command,
                    floor=floor,
                    capability_required=capability,
                    execution_group=0,
                )
                structured_task = structured_task_from_mission_subtask(
                    mission_id=mission_id,
                    subtask=generated_subtask,
                    operator_id=(operator or {}).get("operator_id") if isinstance(operator, dict) else None,
                    task_id=mission_node_id,
                    capability_skill_chains=self.profile_skill_chains_by_robot.get(robot_id),
                ).to_dict()

        subtask_event_id = self._record_embodied_memory(
            mission_id,
            "subtask",
            "cognitive_artifact",
            {
                "command": command,
                "robot_id": robot_id,
                "structured_task": structured_task,
            },
            source_type="mission_dispatch",
            method_id="fireclaw.mission_agent.submit_subtask:v1",
            robot_id=robot_id,
            observed_at=created_at,
        )
        self._add_embodied_relation(
            mission_id,
            subtask_event_id,
            memory_plan_event_id,
            "subtask_of",
        )
        if structured_task is not None and self.embodied_runtime_mode is not None:
            structured_task["memory_lineage"] = MemoryLineage(
                runtime_mode=self.embodied_runtime_mode,
                command_event_id=memory_command_event_id,
                plan_event_id=memory_plan_event_id,
                subtask_event_id=subtask_event_id,
            ).to_dict()

        mission_payload = dict(mission or {"mission_id": mission_id})
        if mission_node_id is not None:
            mission_payload["subtask_id"] = mission_node_id
        subagent_result = self.subagent_client.submit_task(
            entry,
            command=command,
            session_id=session_id,
            dedupe_key=dedupe_key,
            operator=operator,
            mission=mission_payload,
            structured_task=structured_task,
        )
        task_id = subagent_result.get("task_id")
        status = str(subagent_result.get("status") or "unknown")
        if self.mission_registry is not None and isinstance(task_id, str):
            self.mission_registry.record_subtask(
                mission_id=mission_id,
                robot_id=robot_id,
                task_id=task_id,
                command=command,
                status=status,
                created_at=created_at,
            )
        subtask = {
            "robot_id": robot_id,
            "task_id": task_id,
            "status": status,
            "command": command,
        }

        # Project subtask lifecycle into task registry
        if self.task_registry is not None and isinstance(task_id, str):
            self.task_registry.project_task_state(
                task_id=f"{mission_id}:{task_id}",
                requester_session_id=mission_id,
                owner_id=robot_id,
                command=command,
                runtime="robot_gateway",
                scope_kind="subtask",
                status=status,
                delivery_status="delivered" if status == "accepted" else "pending",
                notify_policy="state_changes",
                created_at=created_at,
                parent_task_id=mission_id,
                child_session_id=task_id,
            )

        self._record_mission_memory(
            mission_id,
            "outcome",
            {"robot_id": robot_id, "task_id": task_id, "command": command, "status": status},
            robot_id=robot_id,
            subtask_id=task_id if isinstance(task_id, str) else None,
        )
        dispatch_outcome_event_id = self._record_embodied_memory(
            mission_id,
            "outcome",
            "runtime_evidence",
            {"robot_id": robot_id, "task_id": task_id, "command": command, "status": status},
            source_type="subagent_dispatch",
            robot_id=robot_id,
            subtask_id=task_id if isinstance(task_id, str) else None,
            derived_from=(subtask_event_id,) if subtask_event_id is not None else (),
        )
        self._add_embodied_relation(
            mission_id,
            dispatch_outcome_event_id,
            subtask_event_id,
            "caused_by",
        )
        return {
            "status": status,
            "mission_id": mission_id,
            "robot_id": robot_id,
            "task_id": task_id,
            "subtasks": [subtask],
            "subagent_result": subagent_result,
        }

    def mission_trace(self, mission_id: str) -> dict[str, Any]:
        deny = self._authorize("mission.read")
        if deny is not None:
            return {**deny, "mission_id": mission_id, "subtasks": []}
        if self.mission_registry is None:
            return {"mission_id": mission_id, "status": "not_configured", "subtasks": []}
        mission = self.mission_registry.get_mission(mission_id)
        if mission is None:
            return {"mission_id": mission_id, "status": "not_found", "subtasks": []}
        robot_traces: dict[tuple[str, str], dict[str, Any]] = {}
        for subtask in mission.subtasks:
            entry = self.registry.get(subtask.robot_id)
            if entry is None:
                continue
            robot_trace = self.subagent_client.get_task_trace(entry, subtask.task_id)
            robot_traces[(subtask.robot_id, subtask.task_id)] = robot_trace
            status = _status_from_robot_trace(robot_trace)
            if status is not None and status != subtask.status:
                self.mission_registry.update_subtask(
                    mission_id=mission_id,
                    robot_id=subtask.robot_id,
                    task_id=subtask.task_id,
                    status=status,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    result=robot_trace.get("result") if isinstance(robot_trace.get("result"), dict) else None,
                )
                if status in TERMINAL_SUBTASK_STATUSES:
                    self._record_terminal_outcome(
                        mission_id,
                        terminal_event_id=f"{mission_id}:{subtask.robot_id}:{subtask.task_id}:terminal",
                        terminal_status=status,
                        robot_id=subtask.robot_id,
                        subtask_id=subtask.task_id,
                    )
        trace = self.mission_registry.mission_trace(mission_id)
        report = self.final_report(mission_id)
        if report is not None:
            trace["final_report"] = report
        enriched_subtasks = []
        for subtask in trace.get("subtasks", []):
            if not isinstance(subtask, dict):
                continue
            key = (str(subtask.get("robot_id") or ""), str(subtask.get("task_id") or ""))
            enriched = dict(subtask)
            if key in robot_traces:
                enriched["robot_trace"] = robot_traces[key]
            enriched_subtasks.append(enriched)
        trace["subtasks"] = enriched_subtasks
        return trace

    def mission_events(
        self,
        mission_id: str,
        *,
        robot_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.read")
        if deny is not None:
            return {**deny, "mission_id": mission_id, "event_count": 0, "events": []}
        if self.mission_registry is None:
            return {"mission_id": mission_id, "status": "not_configured", "event_count": 0, "events": []}
        from fireclaw_core.mission.mission_event_aggregator import MissionEventAggregator
        aggregator = MissionEventAggregator(
            registry=self.registry,
            subagent_client=self.subagent_client,
            mission_registry=self.mission_registry,
            subagent_registry=self.subagent_registry,
            task_registry=self.task_registry,
            task_flow_store=self._task_flow_store,
        )
        return aggregator.aggregate(mission_id, robot_id=robot_id, event_type=event_type, limit=limit)

    def _build_planner_memory_context(
        self,
        command: str,
        *,
        mission_id: str,
        max_memories: int = 5,
        max_corrections: int = 3,
        max_external_knowledge: int = 5,
    ) -> PlannerMemoryContextResult:
        """Build scoped planner memory context via the Builder."""
        scopes = frozenset(
            self.operator.control_scopes
            if self.operator is not None
            else {"state.read"}
        )
        return self.planner_memory_context_builder.build(
            PlannerMemoryContextRequest(
                command=command,
                mission_id=mission_id,
                runtime_mode=self.embodied_runtime_mode,
                requester_id=(
                    self.operator.operator_id
                    if self.operator is not None
                    else "mission-agent"
                ),
                scopes=scopes,
                max_memories=max_memories,
                max_corrections=max_corrections,
                max_external_knowledge=max_external_knowledge,
            )
        )

    def _retrieve_planner_context(
        self,
        command: str,
        *,
        mission_id: str | None = None,
        max_memories: int = 5,
        max_corrections: int = 3,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Retrieve relevant memories and operator corrections for planning context.

        Returns (memories, corrections) with secrets redacted.

        This is a compatibility wrapper that delegates to the
        PlannerMemoryContextBuilder when a mission_id is provided.
        """
        if mission_id is None:
            return [], []
        result = self._build_planner_memory_context(
            command,
            mission_id=mission_id,
            max_memories=max_memories,
            max_corrections=max_corrections,
        )
        return list(result.memories), list(result.corrections)

    def plan_and_submit(
        self,
        command: str,
        *,
        session_id: str | None = None,
        operator: dict[str, Any] | None = None,
        use_scheduler: bool = True,
        run_control: Any | None = None,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.plan")
        if deny is not None:
            return {**deny, "subtask_results": []}
        mission_id = _mission_id(session_id)
        if _run_control_cancelled(run_control):
            return {
                "status": "cancelled",
                "message": "Mission Run was cancelled before planning.",
                "mission_id": mission_id,
                "subtask_results": [],
            }
        command_event_id = self._record_embodied_memory(
            mission_id,
            "command",
            "operator_assertion",
            {"command": command},
            source_type="operator",
            source_id=self._operator_source_id(operator),
        )
        if self.mission_deliberation_runtime is None:
            return {
                "status": "no_planner",
                "message": "No mission planner configured.",
                "subtask_results": [],
            }
        # Check fleet presence before planning
        presence = self.check_fleet_presence()
        online_robot_ids = {rid for rid, info in presence.items() if info.get("online")}
        try:
            state_snapshot = self._build_mission_state_snapshot(mission_id, presence)
        except Exception:
            logger.exception("Failed to build mission state snapshot")
            return {
                "status": "blocked",
                "message": "Mission state snapshot could not be built.",
                "errors": ["Authoritative mission state is unavailable."],
                "subtask_results": [],
            }
        snapshot_errors = MissionStateSnapshotValidator().validate(state_snapshot)
        if snapshot_errors:
            return {
                "status": "blocked",
                "message": "Mission state snapshot failed deterministic validation.",
                "errors": snapshot_errors,
                "state_snapshot": state_snapshot.to_dict(),
                "subtask_results": [],
            }

        # Build scoped memory context via the PlannerMemoryContextBuilder
        memory_context_result = self._build_planner_memory_context(
            command,
            mission_id=mission_id,
        )
        state_snapshot_event_id = self._record_mission_state_snapshot(state_snapshot)
        context = MissionPlannerContext(
            available_robots=[
                entry
                for entry in self.registry.enabled_entries()
                if entry.robot_id in online_robot_ids
            ],
            state_snapshot=state_snapshot.to_dict(),
            retrieved_memories=list(memory_context_result.memories),
            operator_corrections=list(memory_context_result.corrections),
            external_knowledge=list(memory_context_result.external_knowledge),
        )
        deliberation_result = self.mission_deliberation_runtime.deliberate(
            mission_id=mission_id,
            command=command,
            state_snapshot=state_snapshot,
            planner_context=context,
        )
        (
            deliberation_result,
            state_snapshot,
            state_snapshot_event_id,
            online_robot_ids,
            active_observation_rounds,
            active_observation_error,
        ) = self._run_active_observation_loop(
            mission_id=mission_id,
            command=command,
            operator=operator,
            memory_context_result=memory_context_result,
            command_event_id=command_event_id,
            state_snapshot=state_snapshot,
            state_snapshot_event_id=state_snapshot_event_id,
            deliberation_result=deliberation_result,
        )
        if active_observation_error is not None:
            return {
                "status": "blocked",
                "message": active_observation_error,
                "mission_id": mission_id,
                "state_snapshot": state_snapshot.to_dict(),
                "deliberation": deliberation_result.to_dict(),
                "active_observation_rounds": active_observation_rounds,
                "subtask_results": [],
            }
        planning_result = deliberation_result.planning_result
        if planning_result is None:
            planning_result = MissionPlanningResult(
                status="blocked",
                message="Mission deliberation returned no planning decision.",
            )
        deliberation_event_id = self._record_mission_deliberation(
            deliberation_result,
            derived_from=tuple(
                item
                for item in (command_event_id, state_snapshot_event_id)
                if item is not None
            ),
        )

        # Append memory context guard decision before validator decisions.
        # Memory degradation must NOT block planning.
        if planning_result.audit_record is not None:
            planning_result = replace(
                planning_result,
                audit_record=append_guard_decision(
                    planning_result.audit_record,
                    memory_context_result.guard_decision(),
                    final_status=planning_result.audit_record.final_status,
                    final_message=planning_result.audit_record.final_message,
                    mission_id=mission_id,
                ),
            )
        elif memory_context_result.warnings:
            logger.warning(
                "Planner memory context degraded",
                extra={
                    "mission_id": mission_id,
                    "warning_codes": sorted({
                        warning.code for warning in memory_context_result.warnings
                    }),
                },
            )
        if deliberation_result.validation_errors:
            planning_result = replace(
                planning_result,
                audit_record=self._with_validator_decision(
                    planning_result.audit_record,
                    validation_errors=list(deliberation_result.validation_errors),
                    final_status="blocked",
                    final_message="Mission plan failed deterministic validation.",
                    mission_id=mission_id,
                ),
            )

        # Primitive fallback: when planner returns "clarify", try primitive composition
        if (
            planning_result.status == "clarify"
            and planning_result.plan is None
            and deliberation_result.reason_code == "planner_clarification"
        ):
            fallback = self._try_primitive_fallback(
                command,
                online_robot_ids,
                mission_id,
                memory_command_event_id=command_event_id,
            )
            if fallback is not None:
                fallback["state_snapshot"] = state_snapshot.to_dict()
                fallback["deliberation"] = deliberation_result.to_dict()
                fallback["active_observation_rounds"] = (
                    active_observation_rounds
                )
                return fallback

        if planning_result.status != "planned" or planning_result.plan is None:
            audit_error = self._record_mission_planning_audit(planning_result.audit_record)
            response = {
                "status": planning_result.status,
                "message": planning_result.message,
                "mission_id": mission_id,
                "state_snapshot": state_snapshot.to_dict(),
                "deliberation": deliberation_result.to_dict(),
                "active_observation_rounds": active_observation_rounds,
                "subtask_results": [],
            }
            if deliberation_result.validation_errors:
                response["errors"] = list(deliberation_result.validation_errors)
            if audit_error is not None:
                response["audit_warning"] = audit_error
            return response
        task_graph = deliberation_result.task_graph
        if task_graph is None:
            return {
                "status": "blocked",
                "message": "Mission deliberation returned no validated task graph.",
                "state_snapshot": state_snapshot.to_dict(),
                "deliberation": deliberation_result.to_dict(),
                "active_observation_rounds": active_observation_rounds,
                "subtask_results": [],
            }
        plan_artifact = {
            "status": planning_result.status,
            "message": planning_result.message,
            "intent": planning_result.intent,
            "plan": planning_result.plan.to_dict(),
            "task_graph": task_graph.to_dict(),
            "deliberation_run_id": deliberation_result.run_id,
        }
        if planning_result.graph_proposal is not None:
            plan_artifact["graph_proposal"] = (
                planning_result.graph_proposal.to_dict()
            )
        plan_event_id = self._record_embodied_memory(
            mission_id,
            "plan",
            "cognitive_artifact",
            plan_artifact,
            source_type="planner",
            method_id=self._planner_method_id(),
            derived_from=(
                (deliberation_event_id,)
                if deliberation_event_id is not None
                else tuple(
                    item
                    for item in (command_event_id, state_snapshot_event_id)
                    if item is not None
                )
            ),
        )
        self._add_embodied_relation(
            mission_id,
            plan_event_id,
            command_event_id,
            "caused_by",
        )
        validation_errors = MissionPlanValidator().validate(
            planning_result.plan,
            self.registry,
            task_graph=task_graph,
        )
        if validation_errors:
            audit_record = self._with_validator_decision(
                planning_result.audit_record,
                validation_errors=validation_errors,
                final_status="blocked",
                final_message="Mission plan failed deterministic validation.",
                mission_id=mission_id,
            )
            audit_error = self._record_mission_planning_audit(audit_record)
            response = {
                "status": "blocked",
                "message": "Mission plan failed deterministic validation.",
                "errors": validation_errors,
                "state_snapshot": state_snapshot.to_dict(),
                "deliberation": deliberation_result.to_dict(),
                "active_observation_rounds": active_observation_rounds,
                "task_graph": task_graph.to_dict(),
                "subtask_results": [],
            }
            if audit_error is not None:
                response["audit_warning"] = audit_error
            return response
        audit_record = self._with_validator_decision(
            planning_result.audit_record,
            validation_errors=[],
            final_status=planning_result.status,
            final_message=planning_result.message,
            mission_id=mission_id,
        )
        audit_error = self._record_mission_planning_audit(audit_record)
        if audit_error is not None:
            return {
                "status": "blocked",
                "message": audit_error,
                "active_observation_rounds": active_observation_rounds,
                "subtask_results": [],
            }
        self._active_task_graphs[mission_id] = task_graph
        created_at = datetime.now(timezone.utc).isoformat()
        if self.mission_registry is not None and self.mission_registry.get_mission(mission_id) is None:
            self.mission_registry.create_mission(
                mission_id=mission_id,
                session_id=session_id,
                command=command,
                created_at=created_at,
            )

        # Record session lineage for ownership tracking
        if self._session_lineage_store is not None:
            try:
                self._session_lineage_store.upsert(MissionSessionLineage(
                    session_id=mission_id,
                    kind="mission",
                    operator_id=(operator or {}).get("operator_id", "unknown"),
                    parent_session_id=None,
                    spawned_by=None,
                    spawn_depth=0,
                    created_at=created_at,
                    updated_at=created_at,
                ))
            except Exception:
                logger.warning("Failed to write session lineage for %s", mission_id, exc_info=True)

        # Project mission lifecycle into task registry
        if self.task_registry is not None:
            self.task_registry.project_task_state(
                task_id=mission_id,
                requester_session_id=mission_id,
                owner_id="operator",
                command=command,
                runtime="mission_agent",
                scope_kind="mission",
                status="planned",
                delivery_status="delivered",
                notify_policy="state_changes",
                created_at=created_at,
            )

        if use_scheduler:
            from fireclaw_core.mission.mission_scheduler import MissionScheduler
            scheduler = MissionScheduler(mission_agent=self)
            scheduler_result = scheduler.schedule(
                planning_result.plan,
                mission_id=mission_id,
                operator=operator,
                memory_command_event_id=command_event_id,
                memory_plan_event_id=plan_event_id,
                run_control=run_control,
            )
            # Flatten group subtask results into a top-level list for API consistency
            subtask_results: list[dict[str, Any]] = []
            for group in scheduler_result.get("group_results", []):
                subtask_results.extend(group.get("subtask_results", []))

            robot_assignments = [
                {"robot_id": r.get("robot_id", "unknown"), "task_id": r.get("task_id", "")}
                for r in subtask_results
                if r.get("status") not in ("skipped", "error")
            ]
            self._record_mission_memory(
                mission_id,
                "outcome",
                {
                    "command": command,
                    "subtask_count": len(subtask_results),
                    "robot_assignments": robot_assignments,
                    "status": scheduler_result.get("status", planning_result.status),
                },
            )
            mission_outcome_event_id = self._record_embodied_memory(
                mission_id,
                "outcome",
                "runtime_evidence",
                {
                    "command": command,
                    "subtask_count": len(subtask_results),
                    "robot_assignments": robot_assignments,
                    "status": scheduler_result.get("status", planning_result.status),
                },
                source_type="mission_scheduler",
                derived_from=(plan_event_id,) if plan_event_id is not None else (),
            )
            self._add_embodied_relation(
                mission_id,
                mission_outcome_event_id,
                plan_event_id,
                "caused_by",
            )

            # Project task-flow summary
            self._project_task_flow(mission_id, command, subtask_results, created_at)

            response = {
                "status": scheduler_result.get("status", planning_result.status),
                "message": scheduler_result.get("message", planning_result.message),
                "mission_id": mission_id,
                "intent": planning_result.intent,
                "plan": planning_result.plan.to_dict(),
                "state_snapshot": state_snapshot.to_dict(),
                "deliberation": deliberation_result.to_dict(),
                "active_observation_rounds": active_observation_rounds,
                "task_graph": task_graph.to_dict(),
                "subtask_results": subtask_results,
                "group_results": scheduler_result.get("group_results", []),
                "failure_decisions": scheduler_result.get("failure_decisions", []),
            }
            if scheduler_result.get("plan_revision") is not None:
                response["plan_revision"] = scheduler_result["plan_revision"]
            for key in (
                "revision_dispatch",
                "cancellation_results",
                "cancellation_terminal_states",
                "active_plan_id",
                "node_executions",
                "failed_nodes",
            ):
                if scheduler_result.get(key) is not None:
                    response[key] = scheduler_result[key]
            active_graph = self.active_task_graph(mission_id)
            if (
                active_graph is not None
                and active_graph.plan_id != task_graph.plan_id
            ):
                response["active_task_graph"] = active_graph.to_dict()
            return response

        subtask_results: list[dict[str, Any]] = []
        for subtask in planning_result.plan.subtasks:
            # Skip offline robots
            if subtask.robot_id not in online_robot_ids:
                continue
            result = self.submit_subtask(
                subtask.robot_id,
                subtask.command,
                session_id=mission_id,
                dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                operator=operator,
                mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                mission_subtask=subtask,
                memory_command_event_id=command_event_id,
                memory_plan_event_id=plan_event_id,
            )
            subtask_results.append(result)

        robot_assignments = [
            {"robot_id": r.get("robot_id", "unknown"), "task_id": r.get("task_id", "")}
            for r in subtask_results
            if r.get("status") not in ("skipped", "error")
        ]
        self._record_mission_memory(
            mission_id,
            "outcome",
            {
                "command": command,
                "subtask_count": len(subtask_results),
                "robot_assignments": robot_assignments,
                "status": planning_result.status,
            },
        )
        mission_outcome_event_id = self._record_embodied_memory(
            mission_id,
            "outcome",
            "runtime_evidence",
            {
                "command": command,
                "subtask_count": len(subtask_results),
                "robot_assignments": robot_assignments,
                "status": planning_result.status,
            },
            source_type="mission_agent",
            derived_from=(plan_event_id,) if plan_event_id is not None else (),
        )
        self._add_embodied_relation(
            mission_id,
            mission_outcome_event_id,
            plan_event_id,
            "caused_by",
        )

        # Project task-flow summary
        self._project_task_flow(mission_id, command, subtask_results, created_at)

        return {
            "status": planning_result.status,
            "message": planning_result.message,
            "mission_id": mission_id,
            "intent": planning_result.intent,
            "plan": planning_result.plan.to_dict(),
            "state_snapshot": state_snapshot.to_dict(),
            "deliberation": deliberation_result.to_dict(),
            "active_observation_rounds": active_observation_rounds,
            "task_graph": task_graph.to_dict(),
            "subtask_results": subtask_results,
        }

    def _get_robot_primitive_skills(self, robot_id: str) -> tuple[str, ...]:
        """Get primitive skills for a robot from its profile configuration."""
        return self.primitive_skills_by_robot.get(robot_id, ())

    def _try_primitive_fallback(
        self,
        command: str,
        online_robot_ids: set[str],
        session_id: str | None,
        *,
        memory_command_event_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Try to dispatch a primitive composition task when no composite matches."""
        # Safety check: block high-risk commands
        high_risk_keywords = ("灭火", "破拆", "进入危险区域", "开阀", "爆炸", "有毒")
        for keyword in high_risk_keywords:
            if keyword in command:
                return {
                    "status": "clarify",
                    "message": "该任务需要专用复合技能或人工确认，不能仅靠 primitive skills 自动执行。",
                    "subtask_results": [],
                }

        # Find an online robot with primitive skills
        for entry in self.registry.enabled_entries():
            if entry.robot_id not in online_robot_ids:
                continue
            primitive_skills = self._get_robot_primitive_skills(entry.robot_id)
            if not primitive_skills:
                continue

            # Create a primitive composition task
            mission_id = _mission_id(session_id)
            created_at = datetime.now(timezone.utc).isoformat()
            task_id = f"{mission_id}:{entry.robot_id}:primitive-composition"
            structured_task = {
                "task_id": task_id,
                "mission_id": mission_id,
                "robot_id": entry.robot_id,
                "task_type": "primitive_composition",
                "command": command,
                "target": {},
                "required_skills": [],
                "allowed_skills": list(primitive_skills),
                "risk_level": "low",
                "constraints": {"source": "mission_primitive_fallback"},
            }
            subtask_event_id = self._record_embodied_memory(
                mission_id,
                "subtask",
                "cognitive_artifact",
                {
                    "command": command,
                    "robot_id": entry.robot_id,
                    "structured_task": structured_task,
                    "dispatch_mode": "primitive_fallback",
                },
                source_type="mission_primitive_fallback",
                method_id="fireclaw.mission_agent.primitive_fallback:v1",
                robot_id=entry.robot_id,
                observed_at=created_at,
            )
            self._add_embodied_relation(
                mission_id,
                subtask_event_id,
                memory_command_event_id,
                "caused_by",
            )
            if self.embodied_runtime_mode is not None:
                structured_task["memory_lineage"] = MemoryLineage(
                    runtime_mode=self.embodied_runtime_mode,
                    command_event_id=memory_command_event_id,
                    subtask_event_id=subtask_event_id,
                ).to_dict()

            # Submit to robot-gateway
            try:
                result = self.subagent_client.submit_task(
                    entry,
                    command=command,
                    session_id=session_id,
                    mission={"mission_id": mission_id},
                    structured_task=structured_task,
                )
                status = str(result.get("status") or "unknown")

                # Persist mission and subtask records
                if self.mission_registry is not None and self.mission_registry.get_mission(mission_id) is None:
                    self.mission_registry.create_mission(
                        mission_id=mission_id,
                        session_id=session_id,
                        command=command,
                        created_at=created_at,
                    )
                    self.mission_registry.record_subtask(
                        mission_id=mission_id,
                        robot_id=entry.robot_id,
                        task_id=result.get("task_id", task_id),
                        command=command,
                        status=status,
                        created_at=created_at,
                    )

                self._record_mission_memory(
                    mission_id,
                    "outcome",
                    {
                        "robot_id": entry.robot_id,
                        "task_id": result.get("task_id", task_id),
                        "command": command,
                        "status": status,
                        "dispatch_mode": "primitive_fallback",
                    },
                    robot_id=entry.robot_id,
                )
                outcome_event_id = self._record_embodied_memory(
                    mission_id,
                    "outcome",
                    "runtime_evidence",
                    {
                        "robot_id": entry.robot_id,
                        "task_id": result.get("task_id", task_id),
                        "command": command,
                        "status": status,
                        "dispatch_mode": "primitive_fallback",
                    },
                    source_type="subagent_dispatch",
                    robot_id=entry.robot_id,
                    subtask_id=str(result.get("task_id", task_id)),
                    derived_from=(subtask_event_id,) if subtask_event_id is not None else (),
                )
                self._add_embodied_relation(
                    mission_id,
                    outcome_event_id,
                    subtask_event_id,
                    "caused_by",
                )

                return {
                    "status": "accepted",
                    "message": f"Primitive composition task dispatched to {entry.robot_id}",
                    "mission_id": mission_id,
                    "subtask_results": [result],
                }
            except Exception as exc:
                logger.warning("Primitive fallback dispatch failed for %s: %s", entry.robot_id, exc)
                continue

        return None

    def _project_task_flow(
        self,
        mission_id: str,
        command: str,
        subtask_results: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        """Upsert a TaskFlowRecord for this mission if a task-flow store is configured."""
        if self._task_flow_store is None:
            return
        try:
            self._task_flow_store.upsert(TaskFlowRecord(
                flow_id=mission_id,
                mission_id=mission_id,
                command=command,
                status="running",
                task_ids=tuple(
                    r.get("task_id", "")
                    for r in subtask_results
                    if r.get("task_id") and r.get("status") not in ("skipped", "error")
                ),
                robot_ids=tuple(
                    r.get("robot_id", "unknown")
                    for r in subtask_results
                    if r.get("status") not in ("skipped", "error")
                ),
                created_at=created_at,
                updated_at=created_at,
            ))
        except Exception:
            logger.warning("Failed to write task flow for %s", mission_id, exc_info=True)

    def record_correction(
        self,
        mission_id: str,
        *,
        correction: str,
        context: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        operator: OperatorContext | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an operator correction for a mission."""
        resolved_operator = _operator_context(operator) if operator is not None else None
        deny = self._authorize("mission.correct", operator=resolved_operator)
        if deny is not None:
            return {**deny, "status": "denied"}

        content: dict[str, Any] = {"correction": correction}
        if context is not None:
            content["context"] = context

        effective_operator = resolved_operator or self.operator
        operator_id = effective_operator.operator_id if effective_operator else None
        if operator_id:
            content["operator_id"] = operator_id

        self._record_mission_memory(
            mission_id,
            "correction",
            content,
            robot_id=robot_id,
            subtask_id=subtask_id,
        )
        self._record_embodied_memory(
            mission_id,
            "correction",
            "operator_assertion",
            content,
            source_type="operator",
            source_id=(operator_id or self._operator_source_id()),
            robot_id=robot_id,
            subtask_id=subtask_id,
        )
        return {"status": "recorded", "mission_id": mission_id, "correction": correction}

    def record_final_report(
        self,
        mission_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the advisory report after all Robot results are collected."""
        value = dict(report)
        self._mission_reports[mission_id] = value
        if self.mission_registry is not None:
            try:
                self.mission_registry.record_final_report(
                    mission_id=mission_id,
                    report=value,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            except (KeyError, OSError, ValueError):
                logger.warning(
                    "Failed to persist final Mission report for %s",
                    mission_id,
                    exc_info=True,
                )
        self._record_mission_memory(
            mission_id,
            "outcome",
            {"final_report": value},
        )
        self._record_embodied_memory(
            mission_id,
            "outcome",
            "runtime_evidence",
            {"final_report": value},
            source_type="mission_report",
        )
        return value

    def final_report(self, mission_id: str) -> dict[str, Any] | None:
        if mission_id in self._mission_reports:
            return dict(self._mission_reports[mission_id])
        if self.mission_registry is None:
            return None
        mission = self.mission_registry.get_mission(mission_id)
        if mission is None:
            return None
        value = getattr(mission, "final_report", None)
        if isinstance(value, dict):
            self._mission_reports[mission_id] = dict(value)
            return dict(value)
        return None

    def cancel_mission(
        self,
        mission_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_operator = _operator_context(operator) if operator is not None else None
        deny = self._authorize("mission.cancel", operator=resolved_operator)
        if deny is not None:
            return {**deny, "mission_id": mission_id, "subtasks": []}
        if self.mission_registry is None:
            return {"mission_id": mission_id, "status": "not_configured", "subtasks": []}
        mission = self.mission_registry.get_mission(mission_id)
        if mission is None:
            return {"mission_id": mission_id, "status": "not_found", "subtasks": []}
        cancelled_subtasks = []
        skipped_subtasks = []
        now = datetime.now(timezone.utc).isoformat()
        for subtask in mission.subtasks:
            if subtask.status in TERMINAL_SUBTASK_STATUSES:
                skipped_subtasks.append(
                    {
                        "robot_id": subtask.robot_id,
                        "task_id": subtask.task_id,
                        "status": subtask.status,
                    }
                )
                continue
            entry = self.registry.get(subtask.robot_id)
            if entry is None:
                skipped_subtasks.append(
                    {
                        "robot_id": subtask.robot_id,
                        "task_id": subtask.task_id,
                        "status": "not_found",
                    }
                )
                continue
            cancel_result = self.subagent_client.cancel_task(entry, subtask.task_id, operator=operator)
            status = str(cancel_result.get("status") or "cancel_requested")
            self.mission_registry.update_subtask(
                mission_id=mission_id,
                robot_id=subtask.robot_id,
                task_id=subtask.task_id,
                status=status,
                updated_at=now,
                result=cancel_result,
            )
            cancelled_subtasks.append(
                {
                    "robot_id": subtask.robot_id,
                    "task_id": subtask.task_id,
                    "status": status,
                    "cancel_result": cancel_result,
                }
            )
        if cancelled_subtasks:
            status = "cancel_requested"
        elif skipped_subtasks:
            status = "already_terminal"
        else:
            status = "empty"
        self._record_mission_memory(
            mission_id,
            "outcome",
            {
                "status": status,
                "cancelled_subtask_count": len(cancelled_subtasks),
                "skipped_subtask_count": len(skipped_subtasks),
            },
        )
        self._record_embodied_memory(
            mission_id,
            "outcome",
            "runtime_evidence",
            {
                "status": status,
                "cancelled_subtask_count": len(cancelled_subtasks),
                "skipped_subtask_count": len(skipped_subtasks),
            },
            source_type="mission_cancel",
            observed_at=now,
        )
        return {
            "mission_id": mission_id,
            "status": status,
            "cancelled_subtask_count": len(cancelled_subtasks),
            "skipped_subtask_count": len(skipped_subtasks),
            "subtasks": cancelled_subtasks,
            "skipped_subtasks": skipped_subtasks,
        }

    def request_approval(
        self,
        mission_id: str,
        *,
        action: str,
        risk_level: str,
        command: str,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        """Create an approval request for a high-risk mission action."""
        if self.approval_store is None:
            return {"status": "not_configured"}
        resolved_operator = operator or self.operator
        operator_id = (
            resolved_operator.operator_id if resolved_operator is not None else "unknown"
        )
        request = self.approval_store.create(
            mission_id=mission_id,
            action=action,
            risk_level=risk_level,
            command=command,
            requested_by=operator_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._record_approval_memory(
            mission_id,
            {
                "decision": "require_confirmation",
                "approval_status": request.status,
                "request_id": request.request_id,
                "action": request.action,
                "risk_level": request.risk_level,
                "requested_by": request.requested_by,
            },
            evidence_kind="runtime_evidence",
            observed_at=request.created_at,
        )
        return {"status": "pending", "request": request.to_dict()}

    def replay_incident(self, mission_id: str) -> dict[str, Any]:
        """Reconstruct a mission timeline from persistent registry + memory data."""
        if self.mission_registry is None:
            return {"mission_id": mission_id, "status": "not_configured", "timeline": [], "summary": {}}
        from fireclaw_core.monitoring.incident_replay import IncidentReplay
        replay = IncidentReplay(mission_registry=self.mission_registry, mission_memory=self.mission_memory)
        try:
            return replay.replay(mission_id)
        except KeyError:
            return {"mission_id": mission_id, "status": "not_found", "timeline": [], "summary": {}}

    def decide_approval(
        self,
        request_id: str,
        *,
        decision: str,
        reason: str | None = None,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        """Decide (approve/deny) a pending approval request."""
        if self.approval_store is None:
            return {"status": "not_configured"}
        deny = self._authorize("mission.approve", operator=operator)
        if deny is not None:
            return {**deny, "status": "denied"}
        resolved_operator = operator or self.operator
        operator_id = (
            resolved_operator.operator_id if resolved_operator is not None else "unknown"
        )
        now = datetime.now(timezone.utc).isoformat()
        if decision == "approve":
            result = self.approval_store.approve(request_id, decided_by=operator_id, decided_at=now)
        elif decision == "deny":
            result = self.approval_store.deny(request_id, decided_by=operator_id, reason=reason, decided_at=now)
        else:
            return {"status": "error", "message": f"Invalid decision: {decision}"}
        if result is None:
            return {"status": "not_found", "request_id": request_id}
        self._record_approval_memory(
            result.mission_id,
            {
                "decision": result.status,
                "approval_status": result.status,
                "request_id": result.request_id,
                "action": result.action,
                "risk_level": result.risk_level,
                "reason": result.reason,
            },
            evidence_kind="operator_assertion",
            source_id=operator_id,
            observed_at=result.decided_at,
        )
        return {"status": "decided", "request": result.to_dict()}


def _capability_from_entry(entry: RobotRegistryEntry) -> str:
    if "search_for_victims" in entry.capabilities:
        return "search_for_victims"
    return entry.capabilities[0] if entry.capabilities else "unknown"


def _mission_id(session_id: str | None) -> str:
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return f"mission-{uuid4().hex}"


def _memory_result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict") and callable(result.to_dict):
        value = result.to_dict()
    elif is_dataclass(result):
        value = asdict(result)
    elif isinstance(result, dict):
        value = dict(result)
    else:
        raise TypeError(f"Unsupported retrieved memory result: {type(result)!r}")
    if not isinstance(value, dict):
        raise TypeError("Retrieved memory result must serialize to a dict.")
    return value


def _status_from_robot_trace(trace: dict[str, Any]) -> str | None:
    """Extract a canonical Robot Agent runtime status from a trace."""

    return robot_task_status_from_trace(trace)


def _run_control_cancelled(run_control: Any | None) -> bool:
    checker = getattr(run_control, "is_cancel_requested", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return True


def _operator_context(value: OperatorContext | dict[str, Any]) -> OperatorContext:
    if isinstance(value, OperatorContext):
        return value
    return operator_from_payload(value)
