from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from fireclaw_core.approval_store import JsonlApprovalStore
from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
from fireclaw_core.memory_index import SqliteMemoryIndex
from fireclaw_core.memory_retrieval import MemoryRetriever
from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_memory import MissionMemoryStore
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import load_robot_registry
from fireclaw_core.session_lineage import JsonlSessionLineageStore
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_flow_registry import JsonlTaskFlowRegistryStore
from fireclaw_core.task_registry import JsonlTaskRegistryStore
from fireclaw_core.validation_sidecar import ValidationSidecar


@dataclass(frozen=True)
class MissionRuntimePaths:
    robot_registry: Path
    mission_registry: Path
    mission_memory: Path | None = None
    memory_index: Path | None = None
    task_registry: Path | None = None
    subagent_registry: Path | None = None
    session_lineage: Path | None = None
    task_flow: Path | None = None
    approvals: Path | None = None


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
    memory_store = MissionMemoryStore(paths.mission_memory) if paths.mission_memory else None
    memory_retriever = None
    if paths.memory_index is not None:
        memory_retriever = MemoryRetriever(index=SqliteMemoryIndex(paths.memory_index))
    task_registry = JsonlTaskRegistryStore(paths.task_registry) if paths.task_registry else None
    subagent_registry = JsonlSubagentRegistry(paths.subagent_registry) if paths.subagent_registry else None
    return MissionAgent(
        registry=load_robot_registry(paths.robot_registry),
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
