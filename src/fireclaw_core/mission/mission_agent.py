from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Protocol
from uuid import uuid4
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.gateway.control import ControlPolicy, OperatorContext
from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.mission_memory_facade import MemoryAccessContext
from fireclaw_core.memory.mission_memory_tools import (
    CURRENT_CONTEXT_TOOL,
    MissionMemoryTools,
)
from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
)
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
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
from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.task.task_contract import MemoryLineage, structured_task_from_mission_subtask
from fireclaw_core.task.task_flow_registry import JsonlTaskFlowRegistryStore, TaskFlowRecord


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
        self.control_policy = control_policy
        self.operator = operator
        self.mission_memory = mission_memory
        self.memory_retriever = memory_retriever
        self.approval_store = approval_store
        self.plugin_runtime = plugin_runtime
        self.task_registry = task_registry
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

    def _authorize(self, action: str) -> dict[str, Any] | None:
        """Check mission-level authorization. Returns deny dict if denied, None if allowed."""
        if self.control_policy is None or self.operator is None:
            return None
        decision = self.control_policy.evaluate(self.operator, action)
        if decision.status == "deny":
            return {
                "status": "denied",
                "message": f"Operator {self.operator.operator_id} lacks required scope: {action}",
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

    def _operator_source_id(self, operator: dict[str, Any] | None = None) -> str:
        if isinstance(operator, dict):
            value = operator.get("operator_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        if self.operator is not None and self.operator.operator_id.strip():
            return self.operator.operator_id.strip()
        return "unknown-operator"

    def _planner_method_id(self) -> str:
        planner_type = type(self.planner)
        return f"{planner_type.__module__}.{planner_type.__qualname__}"

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
                "message": "Robot subagent is not registered.",
                "subtasks": [],
            }
        if not entry.enabled:
            return {
                "status": "disabled",
                "robot_id": robot_id,
                "message": "Robot subagent is disabled.",
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
                capability_skill_chains=self.profile_skill_chains_by_robot.get(robot_id),
            ).to_dict()
        else:
            floor = RuleBasedPlanner()._extract_floor(command)
            capability = _capability_from_entry(entry)
            if floor is not None and capability != "unknown":
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

        subagent_result = self.subagent_client.submit_task(
            entry,
            command=command,
            session_id=session_id,
            dedupe_key=dedupe_key,
            operator=operator,
            mission=mission or {"mission_id": mission_id},
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
        trace = self.mission_registry.mission_trace(mission_id)
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
        """
        memories: list[dict[str, Any]] = []
        corrections: list[dict[str, Any]] = []

        try:
            if self.memory_retriever is not None:
                retrieved = self.memory_retriever.retrieve(command, limit=max_memories)
                memories = [redact_dict(_memory_result_to_dict(r)) for r in retrieved]
            elif self.mission_memory is not None:
                # Search for relevant outcome records matching the command
                outcome_records = self.mission_memory.search(
                    record_type="outcome",
                    keyword=command.strip()[:50] if command.strip() else None,
                    limit=max_memories,
                )
                memories = [
                    redact_dict(r.to_dict())
                    for r in outcome_records
                ]
        except Exception:
            logger.warning("Failed to retrieve planner memories", exc_info=True)

        if (
            mission_id is not None
            and self.embodied_working_memory is not None
            and self.embodied_runtime_mode is not None
        ):
            try:
                snapshot = self.embodied_working_memory.snapshot(
                    runtime_mode=self.embodied_runtime_mode,
                    mission_id=mission_id,
                    event_types=frozenset({
                        "correction",
                        "observation",
                        "outcome",
                        "safety_decision",
                    }),
                    limit=max_memories,
                )
                memories.extend(
                    {
                        "memory_tier": "working",
                        "fresh_at": snapshot.reference_at,
                        "event": redact_dict(event.to_mission_record().to_dict()),
                    }
                    for event in snapshot.events
                )
                memories = memories[-max_memories:]
            except Exception:
                logger.warning("Failed to project working memory into planner context", exc_info=True)

        if mission_id is not None and self.mission_memory_tools is not None:
            try:
                operator_scopes = frozenset(
                    self.operator.control_scopes if self.operator is not None else {"state.read"}
                )
                context_memory = self.call_memory_tool(
                    mission_id=mission_id,
                    name=CURRENT_CONTEXT_TOOL,
                    arguments={"limit": max_memories},
                    requester_id=(
                        self.operator.operator_id if self.operator is not None else "mission-agent"
                    ),
                    scopes=operator_scopes,
                )
                memories.append({"memory_tier": "mission_facade", **context_memory})
                memories = memories[-max_memories:]
            except Exception:
                logger.warning("Failed to read planner context through memory facade", exc_info=True)

        try:
            if self.mission_memory is not None:
                # Search for all recent operator corrections (not keyword-filtered,
                # since corrections may reference different commands than the current one)
                correction_records = self.mission_memory.search(
                    record_type="correction",
                    limit=max_corrections,
                )
                corrections = [
                    redact_dict(r.to_dict())
                    for r in correction_records
                ]
        except Exception:
            logger.warning("Failed to retrieve operator corrections", exc_info=True)

        # Apply memory hooks from plugin runtime (filter + rerank)
        if self.plugin_runtime is not None and memories:
            for effect in self.plugin_runtime.run_memory_hooks(
                "filter",
                {"command": command, "memories": memories},
            ):
                filtered = effect.get("effect", {}).get("memories")
                if isinstance(filtered, list):
                    memories = filtered
            for effect in self.plugin_runtime.run_memory_hooks(
                "rerank",
                {"command": command, "memories": memories},
            ):
                reranked = effect.get("effect", {}).get("memories")
                if isinstance(reranked, list):
                    memories = reranked

        return memories, corrections

    def plan_and_submit(
        self,
        command: str,
        *,
        session_id: str | None = None,
        operator: dict[str, Any] | None = None,
        use_scheduler: bool = True,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.plan")
        if deny is not None:
            return {**deny, "subtask_results": []}
        mission_id = _mission_id(session_id)
        command_event_id = self._record_embodied_memory(
            mission_id,
            "command",
            "operator_assertion",
            {"command": command},
            source_type="operator",
            source_id=self._operator_source_id(operator),
        )
        if self.planner is None:
            return {
                "status": "no_planner",
                "message": "No mission planner configured.",
                "subtask_results": [],
            }
        # Check fleet presence before planning
        presence = self.check_fleet_presence()
        online_robot_ids = {rid for rid, info in presence.items() if info.get("online")}

        # Retrieve memories and corrections for planner context
        memories, corrections = self._retrieve_planner_context(
            command,
            mission_id=mission_id,
        )

        # Apply provider context hooks from plugin runtime
        if self.plugin_runtime is not None:
            for effect in self.plugin_runtime.run_provider_hooks(
                "enrich_context",
                {"command": command, "retrieved_memories": memories, "operator_corrections": corrections},
            ):
                payload = effect.get("effect", {})
                extra_memories = payload.get("retrieved_memories", [])
                if isinstance(extra_memories, list):
                    memories.extend(redact_dict(m) for m in extra_memories if isinstance(m, dict))
                extra_corrections = payload.get("operator_corrections", [])
                if isinstance(extra_corrections, list):
                    corrections.extend(redact_dict(c) for c in extra_corrections if isinstance(c, dict))

        context = MissionPlannerContext(
            available_robots=[e for e in self.registry.enabled_entries() if e.robot_id in online_robot_ids],
            retrieved_memories=memories,
            operator_corrections=corrections,
        )
        planning_result = self.planner.plan(command, context=context)

        # Primitive fallback: when planner returns "clarify", try primitive composition
        if planning_result.status == "clarify" and planning_result.plan is None:
            fallback = self._try_primitive_fallback(
                command,
                online_robot_ids,
                mission_id,
                memory_command_event_id=command_event_id,
            )
            if fallback is not None:
                return fallback

        if planning_result.status != "planned" or planning_result.plan is None:
            audit_error = self._record_mission_planning_audit(planning_result.audit_record)
            response = {
                "status": planning_result.status,
                "message": planning_result.message,
                "subtask_results": [],
            }
            if audit_error is not None:
                response["audit_warning"] = audit_error
            return response
        plan_event_id = self._record_embodied_memory(
            mission_id,
            "plan",
            "cognitive_artifact",
            {
                "status": planning_result.status,
                "message": planning_result.message,
                "intent": planning_result.intent,
                "plan": planning_result.plan.to_dict(),
            },
            source_type="planner",
            method_id=self._planner_method_id(),
            derived_from=(command_event_id,) if command_event_id is not None else (),
        )
        self._add_embodied_relation(
            mission_id,
            plan_event_id,
            command_event_id,
            "caused_by",
        )
        validation_errors = MissionPlanValidator().validate(planning_result.plan, self.registry)
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
                "subtask_results": [],
            }
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

            return {
                "status": scheduler_result.get("status", planning_result.status),
                "message": scheduler_result.get("message", planning_result.message),
                "mission_id": mission_id,
                "intent": planning_result.intent,
                "plan": planning_result.plan.to_dict(),
                "subtask_results": subtask_results,
                "group_results": scheduler_result.get("group_results", []),
                "failure_decisions": scheduler_result.get("failure_decisions", []),
            }

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
    ) -> dict[str, Any]:
        """Record an operator correction for a mission."""
        deny = self._authorize("mission.correct")
        if deny is not None:
            return {**deny, "status": "denied"}

        content: dict[str, Any] = {"correction": correction}
        if context is not None:
            content["context"] = context

        operator_id = self.operator.operator_id if self.operator else None
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
            source_id=self._operator_source_id(),
            robot_id=robot_id,
            subtask_id=subtask_id,
        )
        return {"status": "recorded", "mission_id": mission_id, "correction": correction}

    def cancel_mission(
        self,
        mission_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.cancel")
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
    ) -> dict[str, Any]:
        """Create an approval request for a high-risk mission action."""
        if self.approval_store is None:
            return {"status": "not_configured"}
        operator_id = self.operator.operator_id if self.operator else "unknown"
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
    ) -> dict[str, Any]:
        """Decide (approve/deny) a pending approval request."""
        if self.approval_store is None:
            return {"status": "not_configured"}
        deny = self._authorize("mission.approve")
        if deny is not None:
            return {**deny, "status": "denied"}
        operator_id = self.operator.operator_id if self.operator else "unknown"
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


_NON_TERMINAL_TOP_STATUSES = {"unknown", "running", "cancel_requested"}

_TERMINAL_EVENT_TYPES = {"task.completed", "task.cancelled", "task.failed"}

_EVENT_TO_MISSION_STATUS = {
    "task.completed": "completed",
    "task.cancelled": "cancelled",
    "task.failed": "failed",
}


def _status_from_robot_trace(trace: dict[str, Any]) -> str | None:
    """Extract a mission-level terminal status from a robot trace.

    Priority order (highest first):
    1. queue_record.status
    2. events — scan reversed for task.completed / task.cancelled / task.failed
    3. trace["status"] — top-level (filter out non-terminal values)
    4. result.status — inner agent result status
    """
    # 1. queue_record.status (robot-local terminal wrapper)
    queue_record = trace.get("queue_record")
    if isinstance(queue_record, dict):
        qr_status = queue_record.get("status")
        if isinstance(qr_status, str) and qr_status in TERMINAL_SUBTASK_STATUSES:
            return qr_status

    # 2. Events — scan reversed for terminal lifecycle events
    events = trace.get("events")
    if isinstance(events, list):
        for event in reversed(events):
            if isinstance(event, dict):
                event_type = event.get("type")
                if isinstance(event_type, str) and event_type in _TERMINAL_EVENT_TYPES:
                    return _EVENT_TO_MISSION_STATUS[event_type]

    # 3. Top-level trace status (filter non-terminal values)
    status = trace.get("status")
    if isinstance(status, str) and status not in _NON_TERMINAL_TOP_STATUSES:
        if status in TERMINAL_SUBTASK_STATUSES:
            return status

    # 4. result.status (inner agent result — lowest priority)
    result = trace.get("result")
    if isinstance(result, dict):
        result_status = result.get("status")
        if isinstance(result_status, str) and result_status in TERMINAL_SUBTASK_STATUSES:
            return result_status

    return None
