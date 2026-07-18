from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetriever
from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
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
    embodied_runtime_mode: str | None = None


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

    return MissionAgent(
        registry=registry,
        mission_registry=JsonlMissionRegistry(paths.mission_registry),
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
    )


def build_validation_sidecar(
    output_dir: str | Path,
    run: Callable[[Path], dict[str, Any]],
) -> ValidationSidecar:
    """Build a :class:`ValidationSidecar` for explicit post-ready validation.

    The sidecar is **not** auto-run on gateway startup; callers must invoke
    ``sidecar.run_once()`` explicitly when validation is desired.
    """
    return ValidationSidecar(output_dir=Path(output_dir), run=run)
