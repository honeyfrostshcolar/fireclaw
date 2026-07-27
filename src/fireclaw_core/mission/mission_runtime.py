from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetriever
from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
from fireclaw_core.memory.entity_memory import EntityMemoryService
from fireclaw_core.memory.mission_memory_facade import MissionMemoryFacade
from fireclaw_core.memory.mission_memory_tools import MissionMemoryTools
from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
from fireclaw_core.memory.planner_memory_context import PlannerMemoryContextBuilder
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryHydrationReport
from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_memory import MissionMemoryStore
from fireclaw_core.mission.mission_planning_audit import JsonlMissionPlanningAuditSink
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.agent.robot_registry import load_robot_registry
from fireclaw_core.infra.session_lineage import JsonlSessionLineageStore
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_flow_registry import JsonlTaskFlowRegistryStore
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
from fireclaw_core.safety.validation_sidecar import ValidationSidecar


@dataclass(frozen=True)
class MissionRuntimePaths:
    robot_registry: Path
    mission_registry: Path
    robot_profiles: tuple[Path, ...] = ()
    mission_memory: Path | None = None
    memory_index: Path | None = None
    task_registry: Path | None = None
    subagent_registry: Path | None = None
    session_lineage: Path | None = None
    task_flow: Path | None = None
    approvals: Path | None = None
    mission_planning_audit: Path | None = None
    memory_lifecycle: Path | None = None
    memory_audit_dir: Path | None = None
    reusable_knowledge: Path | None = None
    embodied_runtime_mode: str | None = None
    consolidation_jobs: Path | None = None
    consolidation_state: Path | None = None
    consolidation_lock: Path | None = None


def build_operator_context(
    *,
    operator_id: str,
    role: str,
    scopes: Iterable[str] | None = None,
    source: str = "runtime",
) -> OperatorContext:
    return OperatorContext(
        operator_id=operator_id,
        role=role,
        control_scopes=set(scopes) if scopes is not None else scopes_for_role(role),
        source=source,
    )


def build_mission_agent_from_paths(
    paths: MissionRuntimePaths,
    *,
    operator_id: str,
    role: str,
    scopes: Iterable[str] | None = None,
    planner=None,
    plugin_runtime=None,
    source: str = "runtime",
) -> MissionAgent:
    embodied_memory_producer = None
    approval_memory_producer = None
    embodied_working_memory = None
    embodied_store = None
    facade = None
    if paths.embodied_runtime_mode is not None:
        if paths.mission_memory is None:
            raise ValueError("embodied_runtime_mode requires mission_memory")
        embodied_store = EmbodiedMemoryStore(
            paths.mission_memory,
            index_path=paths.memory_index,
        )
        memory_store = embodied_store.evidence_store
        embodied_working_memory = EmbodiedWorkingMemory()
        embodied_memory_producer = EmbodiedMemoryProducer(
            embodied_store,
            producer_type="mission_agent",
            producer_id="mission-agent-runtime",
            working_memory=embodied_working_memory,
        )
        approval_memory_producer = EmbodiedMemoryProducer(
            embodied_store,
            producer_type="approval_runtime",
            producer_id="mission-approval-runtime",
            working_memory=embodied_working_memory,
        )
    else:
        memory_store = MissionMemoryStore(paths.mission_memory) if paths.mission_memory else None
    memory_retriever = None
    if paths.memory_index is not None:
        memory_retriever = MemoryRetriever(index=SqliteMemoryIndex(paths.memory_index))
    task_registry = JsonlTaskRegistryStore(paths.task_registry) if paths.task_registry else None
    subagent_registry = JsonlSubagentRegistry(paths.subagent_registry) if paths.subagent_registry else None
    mission_planning_audit_sink = (
        JsonlMissionPlanningAuditSink(paths.mission_planning_audit)
        if paths.mission_planning_audit
        else None
    )

    if paths.robot_profiles:
        from fireclaw_core.agent.robot_profile import load_robot_capability_profiles
        from fireclaw_core.agent.robot_registry import robot_registry_from_profiles

        profiles = load_robot_capability_profiles(list(paths.robot_profiles))
        registry = robot_registry_from_profiles(profiles)
        profile_skill_chains_by_robot = {
            profile.robot_id: profile.capability_skill_chains
            for profile in profiles
        }
        primitive_skills_by_robot = {
            profile.robot_id: profile.primitive_skills
            for profile in profiles
            if profile.primitive_skills
        }
    else:
        registry = load_robot_registry(paths.robot_registry)
        profile_skill_chains_by_robot = {}
        primitive_skills_by_robot = {}

    mission_memory_tools = None
    memory_lifecycle = None
    if embodied_store is not None and paths.embodied_runtime_mode is not None:
        if any(
            value is not None
            for value in (
                paths.memory_lifecycle,
                paths.memory_audit_dir,
                paths.reusable_knowledge,
            )
        ):
            if not all(
                value is not None
                for value in (
                    paths.memory_lifecycle,
                    paths.memory_audit_dir,
                    paths.reusable_knowledge,
                )
            ):
                raise ValueError(
                    "memory_lifecycle, memory_audit_dir, and reusable_knowledge "
                    "must be configured together"
                )
            memory_lifecycle = MissionMemoryLifecycleStore(
                store=embodied_store,
                lifecycle_path=paths.memory_lifecycle,
                audit_dir=paths.memory_audit_dir,
                knowledge_path=paths.reusable_knowledge,
                runtime_mode=paths.embodied_runtime_mode,
            )
        entity_memory = EntityMemoryService(
            store=embodied_store,
            resolver_producer=EmbodiedMemoryProducer(
                embodied_store,
                producer_type="entity_resolver",
                producer_id="mission-memory-facade:entity-reader",
                working_memory=embodied_working_memory,
            ),
            runtime_mode=paths.embodied_runtime_mode,
        )
        facade = MissionMemoryFacade(
            store=embodied_store,
            runtime_mode=paths.embodied_runtime_mode,
            working_memory=embodied_working_memory,
            entity_memory=entity_memory,
            robot_state_provider=lambda: _registry_state(registry),
            lifecycle=memory_lifecycle,
        )
        mission_memory_tools = MissionMemoryTools(facade)

    # Construct consolidation coordinator when embodied memory is enabled
    consolidation_coordinator = None
    if embodied_store is not None and paths.embodied_runtime_mode is not None:
        from fireclaw_core.memory.consolidation_jobs import ConsolidationJobStore

        evidence_path = embodied_store.evidence_store.path
        jobs_path = paths.consolidation_jobs or evidence_path.with_name(
            f"{evidence_path.name}.consolidation-jobs.jsonl"
        )
        state_path = paths.consolidation_state or evidence_path.with_name(
            f"{evidence_path.name}.consolidation-state.jsonl"
        )
        lock_path = paths.consolidation_lock or evidence_path.with_name(
            f"{evidence_path.name}.consolidation.lock"
        )
        consolidator_producer = EmbodiedMemoryProducer(
            embodied_store,
            producer_type="memory_consolidator",
            producer_id="mission-runtime-consolidator",
            working_memory=embodied_working_memory,
        )
        consolidation_engine = FireClawConsolidationEngine(
            store=embodied_store,
            producer=consolidator_producer,
            job_store=ConsolidationJobStore(jobs_path),
        )
        consolidation_state_store = ConsolidationStateStore(state_path)
        consolidation_coordinator = MemoryConsolidationCoordinator(
            engine=consolidation_engine,
            state_store=consolidation_state_store,
            store=embodied_store,
            lock_path=lock_path,
        )

    planner_memory_context_builder = PlannerMemoryContextBuilder(
        memory_retriever=memory_retriever,
        mission_memory=memory_store,
        facade=facade,
        lifecycle=memory_lifecycle,
        plugin_runtime=plugin_runtime,
    )

    # Hydrate working memory from authoritative store at startup
    mission_registry = JsonlMissionRegistry(paths.mission_registry)
    hydration_report = WorkingMemoryHydrationReport()
    if embodied_store is not None and embodied_working_memory is not None:
        from datetime import datetime, timezone
        try:
            hydration_report = embodied_working_memory.hydrate_recent(
                store=embodied_store,
                registry=mission_registry,
                runtime_mode=paths.embodied_runtime_mode,
                reference_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

    return MissionAgent(
        registry=registry,
        mission_registry=mission_registry,
        planner=planner,
        control_policy=ControlPolicy(),
        operator=build_operator_context(operator_id=operator_id, role=role, scopes=scopes, source=source),
        mission_memory=memory_store,
        memory_retriever=memory_retriever,
        approval_store=JsonlApprovalStore(paths.approvals) if paths.approvals else None,
        plugin_runtime=plugin_runtime,
        task_registry=task_registry,
        subagent_registry=subagent_registry,
        session_lineage_store=JsonlSessionLineageStore(str(paths.session_lineage)) if paths.session_lineage else None,
        task_flow_store=JsonlTaskFlowRegistryStore(paths.task_flow) if paths.task_flow else None,
        profile_skill_chains_by_robot=profile_skill_chains_by_robot,
        primitive_skills_by_robot=primitive_skills_by_robot,
        mission_planning_audit_sink=mission_planning_audit_sink,
        embodied_memory_producer=embodied_memory_producer,
        approval_memory_producer=approval_memory_producer,
        embodied_runtime_mode=paths.embodied_runtime_mode,
        embodied_working_memory=embodied_working_memory,
        mission_memory_tools=mission_memory_tools,
        memory_lifecycle=memory_lifecycle,
        planner_memory_context_builder=planner_memory_context_builder,
        consolidation_coordinator=consolidation_coordinator,
        working_memory_hydration_report=hydration_report,
    )


def _registry_state(registry: Any) -> dict[str, Any]:
    return {
        "entries": [
            {
                "robot_id": entry.robot_id,
                "capabilities": list(entry.capabilities),
                "zone": entry.zone,
                "enabled": entry.enabled,
                "is_online": registry.is_online(entry.robot_id),
                "is_stale": registry.is_stale(entry.robot_id),
                "last_seen_at": registry.get_last_seen_at(entry.robot_id),
            }
            for entry in registry.enabled_entries(include_stale=True)
        ]
    }


def build_validation_sidecar(
    output_dir: str | Path,
    run: Callable[[Path], dict[str, Any]],
) -> ValidationSidecar:
    """Build a :class:`ValidationSidecar` for explicit post-ready validation.

    The sidecar is **not** auto-run on gateway startup; callers must invoke
    ``sidecar.run_once()`` explicitly when validation is desired.
    """
    return ValidationSidecar(output_dir=Path(output_dir), run=run)
