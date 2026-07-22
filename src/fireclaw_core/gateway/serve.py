"""MissionGateway server assembly and lifecycle management."""
from __future__ import annotations

import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.planner.planner_builder import build_planner
from fireclaw_core.agent.robot_registry import load_robot_registry
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.memory.reconciliation import EmbodiedMemoryReconciler

logger = logging.getLogger(__name__)

DEFAULT_ROBOTS_TEMPLATE = {
    "robots": [
        {
            "robot_id": "robot-1",
            "base_url": "http://localhost:8765",
            "capabilities": [
                "navigate",
                "search_for_victims",
                "patrol",
                "firefight",
                "recon",
                "transport",
            ],
        }
    ]
}


def _ensure_data_dir(data_dir: Path, *, create_robot_template: bool = True) -> None:
    """Create data directory and optional robots.json template."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not create_robot_template:
        return
    robots_path = data_dir / "robots.json"
    if not robots_path.exists():
        robots_path.write_text(json.dumps(DEFAULT_ROBOTS_TEMPLATE, indent=2, ensure_ascii=False))
        logger.info("Created robot registry template at %s", robots_path)


def start_server(
    *,
    adapter: str = "simulator",
    ros1_config: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    planner_type: str = "deterministic",
    provider_base_url: str | None = None,
    provider_api_key: str | None = None,
    model: str | None = None,
    llm_trace_path: str | None = None,
    data_dir: Path = Path("data"),
    robot_agent_enabled: bool = False,
    robot_agent_planner: str = "deterministic",
    robot_agent_provider_base_url: str | None = None,
    robot_agent_provider_api_key: str | None = None,
    robot_agent_model: str | None = None,
    robot_profiles: tuple[str, ...] | None = None,
    embodied_runtime_mode: str | None = None,
) -> MissionGateway:
    """Assemble and start the MissionGateway HTTP server.

    Creates all persistent stores under *data_dir*, builds a MissionAgent
    with the configured planner, and starts the gateway.

    Args:
        adapter: Robot adapter type (e.g. ``"simulator"``, ``"ros1"``).
            Forwarded to the CLI entry point; adapter selection happens at
            the robot-local gateway level, not at MissionGateway.
        ros1_config: Path to ROS1 adapter configuration.  Like *adapter*,
            this is a CLI-level parameter kept here for forward-compatibility.

    Returns the running MissionGateway instance. Call ``gw.stop()`` to shut down.
    """
    # Note: adapter and ros1_config are forwarded to the CLI entry point.
    # MissionGateway dispatches to robot gateways; adapter selection happens
    # at the robot-local gateway level, not here.

    _ensure_data_dir(data_dir, create_robot_template=not bool(robot_profiles))

    planner = build_planner(
        planner_type=planner_type,
        provider_base_url=provider_base_url,
        provider_api_key=provider_api_key,
        model=model,
        llm_trace_path=llm_trace_path,
    )

    paths = MissionRuntimePaths(
        robot_registry=data_dir / "robots.json",
        mission_registry=data_dir / "missions.jsonl",
        robot_profiles=tuple(Path(p) for p in robot_profiles) if robot_profiles else (),
        mission_memory=data_dir / "memory.jsonl",
        memory_index=data_dir / "memory.sqlite",
        task_registry=data_dir / "tasks.jsonl",
        subagent_registry=data_dir / "subagents.jsonl",
        session_lineage=data_dir / "lineage.jsonl",
        task_flow=data_dir / "flows.jsonl",
        approvals=data_dir / "approvals.jsonl",
        mission_planning_audit=data_dir / "mission-planning-audit.jsonl",
        memory_lifecycle=data_dir / "memory-lifecycle.jsonl",
        memory_audit_dir=data_dir / "memory-audit",
        reusable_knowledge=data_dir / "reusable-knowledge.jsonl",
        embodied_runtime_mode=embodied_runtime_mode,
    )

    agent = build_mission_agent_from_paths(
        paths,
        operator_id="mission-gateway",
        role="operator",
        planner=planner,
        source="serve",
    )

    # When robot_profiles is set, agent.registry is already built from profiles;
    # otherwise fall back to loading from the JSON file.
    if robot_profiles:
        registry = agent.registry
    else:
        registry = load_robot_registry(paths.robot_registry)
    config = MissionGatewayConfig(host=host, port=port)
    memory_reconciler = None
    if embodied_runtime_mode is not None and agent.embodied_memory_store is not None:
        memory_reconciler = EmbodiedMemoryReconciler(
            destination=agent.embodied_memory_store,
            state_path=data_dir / "memory-reconciliation.jsonl",
            runtime_mode=embodied_runtime_mode,
        )
    gw = MissionGateway(
        config,
        mission_agent=agent,
        registry=registry,
        subagent_client=RobotSubagentClient(),
        task_registry=agent.task_registry,
        subagent_registry=agent.subagent_registry,
        session_lineage_store=agent.session_lineage_store,
        memory_reconciler=memory_reconciler,
    )
    gw.start()
    logger.info("FireClaw MissionGateway started at %s", gw.base_url)
    return gw


def run_server_blocking(
    *,
    adapter: str = "simulator",
    ros1_config: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    planner_type: str = "deterministic",
    provider_base_url: str | None = None,
    provider_api_key: str | None = None,
    model: str | None = None,
    llm_trace_path: str | None = None,
    data_dir: Path = Path("data"),
    robot_agent_enabled: bool = False,
    robot_agent_planner: str = "deterministic",
    robot_agent_provider_base_url: str | None = None,
    robot_agent_provider_api_key: str | None = None,
    robot_agent_model: str | None = None,
    robot_profiles: tuple[str, ...] | None = None,
    embodied_runtime_mode: str | None = None,
) -> None:
    """Start the server and block until interrupted (Ctrl+C)."""
    gw = start_server(
        adapter=adapter,
        ros1_config=ros1_config,
        host=host,
        port=port,
        planner_type=planner_type,
        provider_base_url=provider_base_url,
        provider_api_key=provider_api_key,
        model=model,
        llm_trace_path=llm_trace_path,
        data_dir=data_dir,
        robot_agent_enabled=robot_agent_enabled,
        robot_agent_planner=robot_agent_planner,
        robot_agent_provider_base_url=robot_agent_provider_base_url,
        robot_agent_provider_api_key=robot_agent_provider_api_key,
        robot_agent_model=robot_agent_model,
        robot_profiles=robot_profiles,
        embodied_runtime_mode=embodied_runtime_mode,
    )

    shutdown_requested = False

    def _handle_signal(signum: int, frame: Any) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            sys.exit(1)
        shutdown_requested = True
        gw.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"FireClaw MissionGateway running at {gw.base_url}")
    print(f"   Data directory: {data_dir}")
    print(f"   Planner: {planner_type}")
    print("   Press Ctrl+C to stop.\n")

    try:
        while gw._server is not None:
            gw._thread.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        gw.stop()
        print("Gateway stopped.")
