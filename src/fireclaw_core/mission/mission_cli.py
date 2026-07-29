from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.planner.planner_builder import build_planner as _build_planner_shared
from fireclaw_core.rag.runtime_retrieval import RagRuntimeConfig


SUCCESS_STATUSES = {"accepted", "duplicate", "running", "succeeded"}
CANCEL_SUCCESS_STATUSES = {"cancel_requested", "already_terminal", "empty"}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "robot-gateway":
        from fireclaw_core.gateway.gateway import main as gateway_main
        return gateway_main(sys.argv[2:])

    parser = argparse.ArgumentParser(description="Run FireClaw mission-control commands.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    submit = subparsers.add_parser("submit-subtask", help="Submit an explicit subtask to a Robot Agent.")
    submit.add_argument("--robot", required=True, help="Target robot_id from the robot registry.")
    submit.add_argument("--command", required=True, help="Natural-language command for the Robot Agent.")
    submit.add_argument("--session-id", default=None, help="Mission/session id.")
    submit.add_argument("--dedupe-key", default=None, help="Idempotency key for robot task submission.")
    _add_shared_paths(submit)

    trace = subparsers.add_parser("trace", help="Read and aggregate a mission trace.")

    trace.add_argument("mission_id", help="Mission id to inspect.")
    _add_shared_paths(trace)
    _add_runtime_paths(trace)

    cancel = subparsers.add_parser("cancel", help="Cancel all active subtasks for a mission.")
    cancel.add_argument("mission_id", help="Mission id to cancel.")
    _add_shared_paths(cancel)
    _add_runtime_paths(cancel)

    plan = subparsers.add_parser("plan-mission", help="Plan and submit mission subtasks from a natural-language command.")
    plan.add_argument("--command", required=True, help="Natural-language mission command.")
    plan.add_argument("--session-id", default=None, help="Mission/session id.")
    plan.add_argument("--planner", choices=["deterministic", "llm"], default="deterministic",
                       help="Planner backend: 'deterministic' (regex rules) or 'llm' (LLM with tool calling).")
    plan.add_argument("--provider-base-url", default=None, help="LLM provider base URL (required when --planner=llm).")
    plan.add_argument("--provider-api-key", default=None, help="LLM provider API key (required when --planner=llm).")
    plan.add_argument("--model", default=None, help="LLM model id (required when --planner=llm).")
    plan.add_argument("--catalog", default=None, help="Path to model catalog JSON file.")
    plan.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
    plan.add_argument("--use-scheduler", dest="use_scheduler", action="store_true", default=True,
                       help="Use MissionScheduler for execution group ordering (default).")
    plan.add_argument("--no-use-scheduler", dest="use_scheduler", action="store_false",
                       help="Disable MissionScheduler, submit subtasks directly.")
    _add_shared_paths(plan)
    _add_runtime_paths(plan)

    events = subparsers.add_parser("events", help="Aggregate and list mission events from Robot Agents.")
    events.add_argument("mission_id", help="Mission id to collect events for.")
    events.add_argument("--robot-id", default=None, help="Filter events by robot id.")
    events.add_argument("--type", dest="event_type", default=None, help="Filter events by event type.")
    events.add_argument("--limit", type=int, default=200, help="Maximum number of events to return.")
    _add_shared_paths(events)
    _add_runtime_paths(events)

    corrections = subparsers.add_parser("corrections", help="List operator correction records for a mission.")
    corrections.add_argument("mission_id", help="Mission id to list corrections for.")
    corrections.add_argument("--memory-path", default="mission_memory.jsonl", help="Path to mission memory JSONL file.")
    corrections.add_argument("--limit", type=int, default=10, help="Maximum number of corrections to return.")

    memory = subparsers.add_parser("memory", help="Manage mission memory records.")
    memory.add_argument("--memory-path", default="mission_memory.jsonl", help="Path to mission memory JSONL file.")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)

    mem_list = memory_sub.add_parser("list", help="List mission memory records.")
    mem_list.add_argument("--mission-id", required=True, help="Filter by mission id.")
    mem_list.add_argument("--type", dest="record_type", default=None, help="Filter by record type (outcome, observation, correction, lesson).")
    mem_list.add_argument("--limit", type=int, default=None, help="Max records to return.")

    mem_add = memory_sub.add_parser("add", help="Add a mission memory record.")
    mem_add.add_argument("--mission-id", required=True, help="Mission id.")
    mem_add.add_argument("--type", dest="record_type", required=True, help="Record type (outcome, observation, correction, lesson).")
    mem_add.add_argument("--content", required=True, help="JSON content string.")
    mem_add.add_argument("--robot-id", default=None, help="Robot id.")
    mem_add.add_argument("--subtask-id", default=None, help="Subtask id.")

    mem_summary = memory_sub.add_parser("summary", help="Show mission memory summary.")
    mem_summary.add_argument("--mission-id", default=None, help="Filter by mission id.")

    replay = subparsers.add_parser("replay", help="Reconstruct a mission timeline from persistent data.")
    replay.add_argument("mission_id", help="Mission id to replay.")
    _add_shared_paths(replay)
    _add_runtime_paths(replay)

    approval = subparsers.add_parser("approval", help="Manage approval requests.")
    approval.add_argument("--approval-path", default="mission_approvals.jsonl", help="Path to approval store JSONL file.")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)

    app_list = approval_sub.add_parser("list", help="List approval requests.")
    app_list.add_argument("--mission-id", default=None, help="Filter by mission id.")
    app_list.add_argument("--status", default=None, help="Filter by status (pending, approved, denied, expired).")

    app_request = approval_sub.add_parser("request", help="Create an approval request.")
    app_request.add_argument("--mission-id", required=True, help="Mission id.")
    app_request.add_argument("--action", required=True, help="Action requiring approval.")
    app_request.add_argument("--risk-level", required=True, help="Risk level (low, medium, high, critical).")
    app_request.add_argument("--command", required=True, help="Command to execute.")

    app_decide = approval_sub.add_parser("decide", help="Approve or deny an approval request.")
    app_decide.add_argument("request_id", help="Request id to decide on.")
    app_decide.add_argument("--decision", required=True, choices=["approve", "deny"], help="Decision: approve or deny.")
    app_decide.add_argument("--reason", default=None, help="Reason for the decision.")

    lifecycle = subparsers.add_parser("lifecycle-check", help="Check task/Robot Agent lifecycle consistency.")
    lifecycle.add_argument("--task-registry", required=True, help="Path to task registry JSONL.")
    lifecycle.add_argument(
        "--subagent-registry",
        required=True,
        help="Path to the legacy-named Robot Agent run registry JSONL.",
    )
    lifecycle.add_argument("--stale-threshold-seconds", type=float, default=300.0, help="Seconds before a task is considered stale.")

    serve = subparsers.add_parser("serve", help="Start a persistent MissionGateway server.")
    serve.add_argument("--config", type=Path, default=None, help="Path to fireclaw.toml config file.")
    serve.add_argument("--adapter", default=None, help="Robot adapter label for deployment metadata.")
    serve.add_argument("--ros1-config", default=None, help="Path to ROS1 adapter config for robot gateways.")
    serve.add_argument("--host", default=None, help="Host to bind.")
    serve.add_argument("--port", type=int, default=None, help="Port to bind.")
    serve.add_argument("--data-dir", type=Path, default=None, help="Persistent data directory.")
    serve.add_argument(
        "--embodied-runtime-mode",
        choices=("real", "simulation", "replay"),
        default=None,
        help="Enable embodied memory in an explicit runtime domain.",
    )
    _add_memory_rag_options(serve, optional_defaults=True)
    _add_external_knowledge_rag_options(serve, optional_defaults=True)
    serve.add_argument("--planner", choices=["deterministic", "llm"], default=None, help="Planner backend.")
    serve.add_argument("--provider-base-url", default=None, help="LLM provider base URL.")
    serve.add_argument("--provider-api-key", default=None, help="LLM provider API key.")
    serve.add_argument("--model", default=None, help="LLM model id.")
    serve.add_argument("--catalog", default=None, help="Path to model catalog JSON file.")
    serve.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
    serve.add_argument("--robot-agent", action="store_true", default=None, help="Enable robot-local agent planning.")
    serve.add_argument("--robot-agent-planner", choices=["deterministic", "llm"], default=None, help="Robot-local agent planner backend.")
    serve.add_argument("--robot-agent-provider-base-url", default=None, help="Robot-local agent LLM provider base URL.")
    serve.add_argument("--robot-agent-provider-api-key", default=None, help="Robot-local agent LLM provider API key.")
    serve.add_argument("--robot-agent-model", default=None, help="Robot-local agent LLM model id.")
    serve.add_argument("--robot-agent-catalog", default=None, help="Path to robot-local model catalog JSON file.")
    serve.add_argument(
        "--robot-profile",
        action="append",
        default=None,
        help="Path to robot profile TOML. Repeat for multiple robots.",
    )

    mission = subparsers.add_parser("mission", help="Open the interactive mission console.")
    mission.add_argument("--server", default="http://127.0.0.1:8766", help="MissionGateway base URL.")
    mission.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")

    robot_gateway = subparsers.add_parser("robot-gateway", help="Start a robot-local FireClawGateway.")
    robot_gateway.add_argument("robot_gateway_args", nargs=argparse.REMAINDER)

    robot_profile = subparsers.add_parser("robot-profile", help="Manage robot capability profiles.")
    robot_profile_sub = robot_profile.add_subparsers(dest="robot_profile_command", required=True)
    profile_export = robot_profile_sub.add_parser("export", help="Export a profile to robots.json.")
    profile_export.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_export.add_argument("--output", required=True, help="Path to robots.json output.")

    profile_discover = robot_profile_sub.add_parser(
        "discover",
        help="Discover ROS1 sensor topics and write suggested profile rules.",
    )
    profile_discover.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_discover.add_argument("--output", default=None, help="Path to suggested TOML output. Required unless --write-profile is given.")
    profile_discover.add_argument(
        "--write-profile",
        action="store_true",
        help="Explicitly write suggested discovery rules to the profile instead of --output.",
    )

    profile_diff = robot_profile_sub.add_parser(
        "diff-discovery",
        help="Compare current ROS1 sensor discovery against profile rules and fingerprint.",
    )
    profile_diff.add_argument("--profile", required=True, help="Path to robot profile TOML.")

    profile_confirm = robot_profile_sub.add_parser(
        "confirm-discovery",
        help="Confirm current ROS1 sensor discovery and write auditable profile rules.",
    )
    profile_confirm.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_confirm.add_argument("--confirmed-by", required=True, help="Operator ID that reviewed the discovery mapping.")
    profile_confirm.add_argument("--confirmed-at", default=None, help="ISO-8601 confirmation time. Defaults to current UTC time.")

    args = parser.parse_args()
    if args.command_name == "submit-subtask":
        result = _build_mission_agent(args).submit_subtask(
            args.robot,
            args.command,
            session_id=args.session_id,
            dedupe_key=args.dedupe_key,
            operator=_mission_operator(),
        )
        _print_json(result)
        return 0 if result.get("status") in SUCCESS_STATUSES else 1
    if args.command_name == "trace":
        result = _build_mission_agent(args).mission_trace(args.mission_id)
        _print_json(result)
        return 0 if result.get("status") != "not_found" else 1
    if args.command_name == "cancel":
        result = _build_mission_agent(args).cancel_mission(args.mission_id, operator=_mission_operator())
        _print_json(result)
        return 0 if result.get("status") in CANCEL_SUCCESS_STATUSES else 1
    if args.command_name == "plan-mission":
        agent = _build_mission_agent(args, planner=_build_planner(args))
        result = agent.plan_and_submit(args.command, session_id=args.session_id, operator=_mission_operator(), use_scheduler=args.use_scheduler)
        _print_json(result)
        return 0 if result.get("status") == "planned" else 1
    if args.command_name == "events":
        result = _build_mission_agent(args).mission_events(
            args.mission_id,
            robot_id=args.robot_id,
            event_type=args.event_type,
            limit=args.limit,
        )
        _print_json(result)
        return 0
    if args.command_name == "replay":
        result = _build_mission_agent(args).replay_incident(args.mission_id)
        _print_json(result)
        return 0 if result.get("status") not in ("not_found", "not_configured") else 1
    if args.command_name == "corrections":
        return _handle_corrections(args)
    if args.command_name == "memory":
        return _handle_memory(args)
    if args.command_name == "approval":
        return _handle_approval(args)
    if args.command_name == "lifecycle-check":
        from fireclaw_core.lifecycle import LifecycleMaintenanceRunner
        from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
        from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
        report = LifecycleMaintenanceRunner(
            task_registry=JsonlTaskRegistryStore(args.task_registry),
            subagent_registry=JsonlSubagentRegistry(args.subagent_registry),
        ).run(stale_threshold_seconds=args.stale_threshold_seconds)
        _print_json(report)
        return 0 if report["status"] == "ok" else 2
    if args.command_name == "serve":
        from fireclaw_core.gateway.config import find_config, load_config, merge_config
        from fireclaw_core.gateway.serve import run_server_blocking
        cfg: dict[str, object] = {}
        config_path = find_config(args.config)
        if config_path is not None:
            cfg = load_config(config_path)
        merged = merge_config(cfg, {
            "adapter": args.adapter,
            "ros1_config": args.ros1_config,
            "host": args.host,
            "port": args.port,
            "data_dir": str(args.data_dir) if args.data_dir else None,
            "planner_type": args.planner,
            "provider_base_url": args.provider_base_url,
            "provider_api_key": args.provider_api_key,
            "model": args.model,
            "model_catalog_path": args.catalog,
            "llm_trace_path": args.llm_trace_path,
            "robot_agent_enabled": args.robot_agent if args.robot_agent else None,
            "robot_agent_planner": args.robot_agent_planner,
            "robot_agent_provider_base_url": args.robot_agent_provider_base_url,
            "robot_agent_provider_api_key": args.robot_agent_provider_api_key,
            "robot_agent_model": args.robot_agent_model,
            "robot_agent_model_catalog_path": args.robot_agent_catalog,
            "mission_robot_profiles": args.robot_profile,
            "embodied_runtime_mode": args.embodied_runtime_mode,
            "memory_rag_backend": args.memory_rag_backend,
            "memory_rag_bm25_index_dir": args.memory_rag_bm25_index_dir,
            "memory_rag_dense_index_dir": args.memory_rag_dense_index_dir,
            "memory_rag_generation_root": args.memory_rag_generation_root,
            "memory_rag_embedding_provider": args.memory_rag_embedding_provider,
            "memory_rag_embedding_model_path": args.memory_rag_embedding_model_path,
            "memory_rag_reranker_provider": args.memory_rag_reranker_provider,
            "memory_rag_reranker_model_path": args.memory_rag_reranker_model_path,
            "memory_rag_device": args.memory_rag_device,
            "memory_rag_candidate_multiplier": args.memory_rag_candidate_multiplier,
            "memory_rag_rrf_k": args.memory_rag_rrf_k,
            "knowledge_rag_backend": args.knowledge_rag_backend,
            "knowledge_rag_bm25_index_dir": args.knowledge_rag_bm25_index_dir,
            "knowledge_rag_dense_index_dir": args.knowledge_rag_dense_index_dir,
            "knowledge_rag_generation_root": args.knowledge_rag_generation_root,
            "knowledge_rag_embedding_provider": args.knowledge_rag_embedding_provider,
            "knowledge_rag_embedding_model_path": args.knowledge_rag_embedding_model_path,
            "knowledge_rag_reranker_provider": args.knowledge_rag_reranker_provider,
            "knowledge_rag_reranker_model_path": args.knowledge_rag_reranker_model_path,
            "knowledge_rag_device": args.knowledge_rag_device,
            "knowledge_rag_candidate_multiplier": args.knowledge_rag_candidate_multiplier,
            "knowledge_rag_rrf_k": args.knowledge_rag_rrf_k,
        })
        memory_rag = _build_memory_rag_config(
            argparse.Namespace(**{
                key: merged.get(key)
                for key in (
                    "memory_rag_backend",
                    "memory_rag_bm25_index_dir",
                    "memory_rag_dense_index_dir",
                    "memory_rag_generation_root",
                    "memory_rag_embedding_provider",
                    "memory_rag_embedding_model_path",
                    "memory_rag_reranker_provider",
                    "memory_rag_reranker_model_path",
                    "memory_rag_device",
                    "memory_rag_candidate_multiplier",
                    "memory_rag_rrf_k",
                )
            })
        )
        external_knowledge_rag = _build_external_knowledge_rag_config(
            argparse.Namespace(**{
                key: merged.get(key)
                for key in (
                    "knowledge_rag_backend",
                    "knowledge_rag_bm25_index_dir",
                    "knowledge_rag_dense_index_dir",
                    "knowledge_rag_generation_root",
                    "knowledge_rag_embedding_provider",
                    "knowledge_rag_embedding_model_path",
                    "knowledge_rag_reranker_provider",
                    "knowledge_rag_reranker_model_path",
                    "knowledge_rag_device",
                    "knowledge_rag_candidate_multiplier",
                    "knowledge_rag_rrf_k",
                )
            })
        )
        run_server_blocking(
            adapter=str(merged.get("adapter", "simulator")),
            ros1_config=merged.get("ros1_config"),
            host=str(merged.get("host", "127.0.0.1")),
            port=int(merged.get("port", 8766)),
            planner_type=str(merged.get("planner_type", "deterministic")),
            provider_base_url=merged.get("provider_base_url"),
            provider_api_key=merged.get("provider_api_key"),
            model=merged.get("model"),
            model_catalog_path=merged.get("model_catalog_path"),
            llm_trace_path=merged.get("llm_trace_path"),
            data_dir=Path(str(merged.get("data_dir", "data"))),
            robot_agent_enabled=bool(merged.get("robot_agent_enabled", False)),
            robot_agent_planner=str(merged.get("robot_agent_planner", "deterministic")),
            robot_agent_provider_base_url=merged.get("robot_agent_provider_base_url"),
            robot_agent_provider_api_key=merged.get("robot_agent_provider_api_key"),
            robot_agent_model=merged.get("robot_agent_model"),
            robot_agent_model_catalog_path=merged.get("robot_agent_model_catalog_path"),
            robot_profiles=tuple(merged["mission_robot_profiles"]) if merged.get("mission_robot_profiles") else None,
            embodied_runtime_mode=(
                str(merged["embodied_runtime_mode"])
                if merged.get("embodied_runtime_mode") is not None
                else None
            ),
            memory_rag=memory_rag,
            external_knowledge_rag=external_knowledge_rag,
        )
        return 0
    if args.command_name == "mission":
        from fireclaw_core.mission.interactive import run_interactive
        run_interactive(server_url=args.server, timeout=args.timeout)
        return 0
    if args.command_name == "robot-gateway":
        from fireclaw_core.gateway.gateway import main as gateway_main
        return gateway_main(args.robot_gateway_args)
    if args.command_name == "robot-profile":
        return _handle_robot_profile(args)
    parser.error(f"Unknown command: {args.command_name}")
    return 1


def _handle_corrections(args: argparse.Namespace) -> int:
    store = MissionMemoryStore(args.memory_path)
    records = store.search(mission_id=args.mission_id, record_type="correction", limit=args.limit)
    _print_json({"mission_id": args.mission_id, "corrections": [r.to_dict() for r in records]})
    return 0


def _handle_memory(args: argparse.Namespace) -> int:
    import uuid
    from datetime import datetime, timezone

    store = MissionMemoryStore(args.memory_path)

    if args.memory_command == "list":
        records = store.list_records(mission_id=args.mission_id, record_type=args.record_type)
        if args.limit is not None:
            records = records[: args.limit]
        print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))
        return 0

    if args.memory_command == "add":
        try:
            content = json.loads(args.content)
        except json.JSONDecodeError as exc:
            print(f"Error: --content is not valid JSON: {exc}", file=sys.stderr)
            return 1
        record = MissionMemoryRecord(
            record_id=f"mem-{uuid.uuid4().hex[:8]}",
            mission_id=args.mission_id,
            record_type=args.record_type,
            content=content,
            robot_id=args.robot_id,
            subtask_id=args.subtask_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.append(record)
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.memory_command == "summary":
        result = store.summary(mission_id=args.mission_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"Error: unknown memory subcommand: {args.memory_command}", file=sys.stderr)
    return 1


def _handle_approval(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    store = JsonlApprovalStore(args.approval_path)

    if args.approval_command == "list":
        requests = store.list_requests(mission_id=args.mission_id, status=args.status)
        print(json.dumps([r.to_dict() for r in requests], ensure_ascii=False, indent=2))
        return 0

    if args.approval_command == "request":
        request = store.create(
            mission_id=args.mission_id,
            action=args.action,
            risk_level=args.risk_level,
            command=args.command,
            requested_by="mission_cli",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _print_json(request.to_dict())
        return 0

    if args.approval_command == "decide":
        now = datetime.now(timezone.utc).isoformat()
        if args.decision == "approve":
            result = store.approve(args.request_id, decided_by="mission_cli", decided_at=now)
        else:
            result = store.deny(args.request_id, decided_by="mission_cli", reason=args.reason, decided_at=now)
        if result is None:
            print(f"Error: request {args.request_id} not found or already decided", file=sys.stderr)
            return 1
        _print_json(result.to_dict())
        return 0

    print(f"Error: unknown approval subcommand: {args.approval_command}", file=sys.stderr)
    return 1


def _handle_robot_profile(args: argparse.Namespace) -> int:
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    if args.robot_profile_command == "export":
        profile = load_robot_capability_profile(args.profile)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"robots": [profile.to_robot_registry_entry()]}
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_json({"status": "written", "output": str(output), "robot_id": profile.robot_id})
        return 0
    if args.robot_profile_command == "discover":
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        if not args.write_profile and not args.output:
            print("Error: --output is required unless --write-profile is given.", file=sys.stderr)
            return 1

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()

        def _toml_escape(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        def _fingerprint_lines() -> list[str]:
            if report.runtime_fingerprint is None:
                return []
            fingerprint = report.runtime_fingerprint
            lines = [
                "[robot.discovery_fingerprint]",
                f'source = "{_toml_escape(fingerprint.source)}"',
                f'topics_hash = "{_toml_escape(fingerprint.topics_hash)}"',
            ]
            if fingerprint.nodes_hash is not None:
                lines.append(f'nodes_hash = "{_toml_escape(fingerprint.nodes_hash)}"')
            lines.append("")
            return lines

        def _replace_table_block(existing: str, table_header: str, replacement_lines: list[str]) -> str:
            if table_header not in existing:
                return existing.rstrip() + "\n\n" + "\n".join(replacement_lines).rstrip() + "\n"
            lines = existing.splitlines()
            result: list[str] = []
            index = 0
            while index < len(lines):
                if lines[index].strip() == table_header:
                    result.extend(replacement_lines)
                    index += 1
                    while index < len(lines):
                        stripped = lines[index].strip()
                        if stripped.startswith("[") and stripped.endswith("]"):
                            break
                        index += 1
                    continue
                result.append(lines[index])
                index += 1
            return "\n".join(result).rstrip() + "\n"

        fingerprint_lines = _fingerprint_lines()
        header_lines: list[str] = [
            "# Suggested FireClaw sensor discovery rules.",
            "# Review before copying into the robot profile.",
            "",
        ]
        header_lines.extend(fingerprint_lines)
        header_lines.extend([
            "[robot.sensor_discovery]",
            "enabled = true",
            f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
            "",
        ])
        rule_lines: list[str] = []
        for finding in report.findings:
            if finding.status not in {"verified", "degraded"}:
                continue
            rule_lines.extend([
                "[[robot.sensor_discovery.rules]]",
                f'topic_pattern = "{_toml_escape(finding.topic)}"',
                f'message_type = "{_toml_escape(finding.message_type)}"',
                f'sensor = "{_toml_escape(finding.sensor)}"',
                f"confidence = {finding.confidence:.2f}",
                "confirmed = false",
                "",
            ])
        text = "\n".join(header_lines + rule_lines)
        output = Path(args.profile if args.write_profile else args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.write_profile:
            existing = output.read_text(encoding="utf-8")
            updated = existing
            if fingerprint_lines:
                updated = _replace_table_block(updated, "[robot.discovery_fingerprint]", fingerprint_lines)
            if "[robot.sensor_discovery]" in updated:
                addition = "\n".join(rule_lines)
            else:
                addition = "\n".join([
                    "[robot.sensor_discovery]",
                    "enabled = true",
                    f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
                    "",
                    *rule_lines,
                ])
            output.write_text(updated.rstrip() + "\n\n" + addition.rstrip() + "\n", encoding="utf-8")
        else:
            output.write_text(text, encoding="utf-8")
        _print_json({
            "status": "written",
            "output": str(output),
            "verified_sensors": report.verified_sensors(),
        })
        return 0
    if args.robot_profile_command == "diff-discovery":
        from fireclaw_core.agent.profile_discovery import build_discovery_diff
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()
        diff = build_discovery_diff(report, profile.sensor_discovery.rules)
        payload = diff.to_dict()
        payload["verified_sensors"] = report.verified_sensors()
        payload["findings"] = [finding.to_dict() for finding in report.findings]
        _print_json(payload)
        return 0
    if args.robot_profile_command == "confirm-discovery":
        from datetime import datetime, timezone

        from fireclaw_core.agent.profile_discovery import (
            build_discovery_diff,
            render_confirmed_discovery_blocks,
            replace_robot_table_block,
        )
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()
        if report.runtime_fingerprint is None:
            print("Error: runtime fingerprint unavailable; refusing to confirm discovery.", file=sys.stderr)
            return 1
        if not report.verified_sensors():
            print("Error: no verified sensors discovered; refusing to confirm discovery.", file=sys.stderr)
            return 1
        confirmed_at = args.confirmed_at or datetime.now(timezone.utc).isoformat()
        block_text = render_confirmed_discovery_blocks(
            report=report,
            message_timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            confirmed_by=args.confirmed_by,
            confirmed_at=confirmed_at,
        )
        replacement_lines = block_text.splitlines()
        profile_path = Path(args.profile)
        existing = profile_path.read_text(encoding="utf-8")
        updated = replace_robot_table_block(existing, "[robot.discovery_fingerprint]", [])
        updated = replace_robot_table_block(updated, "[robot.sensor_discovery]", replacement_lines)
        profile_path.write_text(updated, encoding="utf-8")
        diff = build_discovery_diff(report, profile.sensor_discovery.rules)
        _print_json({
            "status": "confirmed",
            "profile": str(profile_path),
            "confirmed_by": args.confirmed_by,
            "confirmed_at": confirmed_at,
            "verified_sensors": report.verified_sensors(),
            "diff": diff.to_dict(),
        })
        return 0
    print(f"Error: unknown robot-profile subcommand: {args.robot_profile_command}", file=sys.stderr)
    return 1


def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--robot-registry", default=None, help="Path to robot registry JSON. Required when --robot-profile is not provided.")
    parser.add_argument("--robot-profile", action="append", default=None, help="Path to robot profile TOML. Repeat for multiple robots.")
    parser.add_argument("--mission-registry", required=True, help="Path to mission registry JSONL.")
    parser.add_argument("--operator-id", default="mission-agent", help="Operator ID for authorization.")
    parser.add_argument("--role", default="operator", help="Operator role (observer, operator, supervisor, admin).")
    parser.add_argument("--scopes", nargs="*", default=None, help="Explicit operator scopes (overrides role defaults).")


def _add_runtime_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--memory-path", default=None, help="Path to mission memory JSONL.")
    parser.add_argument("--memory-index", default=None, help="Path to SQLite memory index.")
    _add_memory_rag_options(parser)
    _add_external_knowledge_rag_options(parser)
    parser.add_argument(
        "--embodied-runtime-mode",
        choices=("real", "simulation", "replay"),
        default=None,
        help="Enable policy-checked embodied memory in the explicit runtime domain.",
    )
    parser.add_argument("--task-registry", default=None, help="Path to task registry JSONL.")
    parser.add_argument(
        "--subagent-registry",
        default=None,
        help="Path to the legacy-named Robot Agent run registry JSONL.",
    )
    parser.add_argument("--session-lineage", default=None, help="Path to session lineage JSONL.")
    parser.add_argument("--task-flow", default=None, help="Path to task-flow registry JSONL.")
    parser.add_argument("--approval-path", default=None, help="Path to approval store JSONL.")
    parser.add_argument("--mission-planning-audit-path", default=None, help="Path to mission planning audit JSONL.")


def _add_memory_rag_options(
    parser: argparse.ArgumentParser,
    *,
    optional_defaults: bool = False,
) -> None:
    parser.add_argument(
        "--memory-rag-backend",
        choices=("bm25", "dense", "hybrid", "hybrid_rerank"),
        default=None,
        help="Use an existing RAG backend for mission-memory candidate retrieval.",
    )
    parser.add_argument("--memory-rag-bm25-index-dir", default=None)
    parser.add_argument("--memory-rag-dense-index-dir", default=None)
    parser.add_argument(
        "--memory-rag-generation-root",
        default=None,
        help="Managed generation root for automatic validated index refresh.",
    )
    parser.add_argument(
        "--memory-rag-embedding-provider",
        choices=("fake", "bge-m3"),
        default=None,
    )
    parser.add_argument("--memory-rag-embedding-model-path", default=None)
    parser.add_argument(
        "--memory-rag-reranker-provider",
        choices=("fake", "bge-reranker"),
        default=None,
    )
    parser.add_argument("--memory-rag-reranker-model-path", default=None)
    parser.add_argument("--memory-rag-device", default=None)
    parser.add_argument(
        "--memory-rag-candidate-multiplier",
        type=int,
        default=None if optional_defaults else 3,
    )
    parser.add_argument(
        "--memory-rag-rrf-k",
        type=int,
        default=None if optional_defaults else 60,
    )


def _add_external_knowledge_rag_options(
    parser: argparse.ArgumentParser,
    *,
    optional_defaults: bool = False,
) -> None:
    parser.add_argument(
        "--knowledge-rag-backend",
        choices=("bm25", "dense", "hybrid", "hybrid_rerank"),
        default=None,
        help="Ground mission planning with an external firefighting knowledge index.",
    )
    parser.add_argument("--knowledge-rag-bm25-index-dir", default=None)
    parser.add_argument("--knowledge-rag-dense-index-dir", default=None)
    parser.add_argument(
        "--knowledge-rag-generation-root",
        default=None,
        help="Prebuilt managed generation root for hot-reloading external knowledge.",
    )
    parser.add_argument(
        "--knowledge-rag-embedding-provider",
        choices=("fake", "bge-m3"),
        default=None,
    )
    parser.add_argument("--knowledge-rag-embedding-model-path", default=None)
    parser.add_argument(
        "--knowledge-rag-reranker-provider",
        choices=("fake", "bge-reranker"),
        default=None,
    )
    parser.add_argument("--knowledge-rag-reranker-model-path", default=None)
    parser.add_argument("--knowledge-rag-device", default=None)
    parser.add_argument(
        "--knowledge-rag-candidate-multiplier",
        type=int,
        default=None if optional_defaults else 3,
    )
    parser.add_argument(
        "--knowledge-rag-rrf-k",
        type=int,
        default=None if optional_defaults else 60,
    )


def _build_mission_runtime_paths(args: argparse.Namespace) -> MissionRuntimePaths:
    robot_profiles = tuple(Path(p) for p in (getattr(args, "robot_profile", None) or ()))
    robot_registry = getattr(args, "robot_registry", None)
    if robot_registry is None:
        if not robot_profiles:
            raise SystemExit("--robot-registry is required when --robot-profile is not provided")
        robot_registry_path = Path("robots.json")
    else:
        robot_registry_path = Path(robot_registry)
    return MissionRuntimePaths(
        robot_registry=robot_registry_path,
        mission_registry=args.mission_registry,
        mission_memory=getattr(args, "memory_path", None),
        memory_index=getattr(args, "memory_index", None),
        embodied_runtime_mode=getattr(args, "embodied_runtime_mode", None),
        task_registry=getattr(args, "task_registry", None),
        subagent_registry=getattr(args, "subagent_registry", None),
        session_lineage=getattr(args, "session_lineage", None),
        task_flow=getattr(args, "task_flow", None),
        approvals=getattr(args, "approval_path", None),
        robot_profiles=robot_profiles,
        mission_planning_audit=Path(p) if (p := getattr(args, "mission_planning_audit_path", None)) else None,
        memory_rag=_build_memory_rag_config(args),
        external_knowledge_rag=_build_external_knowledge_rag_config(args),
    )


def _build_memory_rag_config(args: argparse.Namespace) -> RagRuntimeConfig | None:
    backend = getattr(args, "memory_rag_backend", None)
    if backend is None:
        return None
    return RagRuntimeConfig(
        backend=backend,
        source_kind="mission_memory",
        bm25_index_dir=(
            Path(value)
            if (value := getattr(args, "memory_rag_bm25_index_dir", None))
            else None
        ),
        dense_index_dir=(
            Path(value)
            if (value := getattr(args, "memory_rag_dense_index_dir", None))
            else None
        ),
        generation_root=(
            Path(value)
            if (value := getattr(args, "memory_rag_generation_root", None))
            else None
        ),
        embedding_provider=getattr(args, "memory_rag_embedding_provider", None),
        embedding_model_path=(
            Path(value)
            if (value := getattr(args, "memory_rag_embedding_model_path", None))
            else None
        ),
        reranker_provider=getattr(args, "memory_rag_reranker_provider", None),
        reranker_model_path=(
            Path(value)
            if (value := getattr(args, "memory_rag_reranker_model_path", None))
            else None
        ),
        device=getattr(args, "memory_rag_device", None),
        candidate_multiplier=(
            getattr(args, "memory_rag_candidate_multiplier", None) or 3
        ),
        rrf_k=getattr(args, "memory_rag_rrf_k", None) or 60,
    )


def _build_external_knowledge_rag_config(
    args: argparse.Namespace,
) -> RagRuntimeConfig | None:
    backend = getattr(args, "knowledge_rag_backend", None)
    if backend is None:
        return None
    return RagRuntimeConfig(
        backend=backend,
        source_kind="external_knowledge",
        bm25_index_dir=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_bm25_index_dir", None))
            else None
        ),
        dense_index_dir=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_dense_index_dir", None))
            else None
        ),
        generation_root=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_generation_root", None))
            else None
        ),
        embedding_provider=getattr(args, "knowledge_rag_embedding_provider", None),
        embedding_model_path=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_embedding_model_path", None))
            else None
        ),
        reranker_provider=getattr(args, "knowledge_rag_reranker_provider", None),
        reranker_model_path=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_reranker_model_path", None))
            else None
        ),
        device=getattr(args, "knowledge_rag_device", None),
        candidate_multiplier=(
            getattr(args, "knowledge_rag_candidate_multiplier", None) or 3
        ),
        rrf_k=getattr(args, "knowledge_rag_rrf_k", None) or 60,
    )


def _build_mission_agent(args: argparse.Namespace, *, planner: Any = None) -> MissionAgent:
    paths = _build_mission_runtime_paths(args)
    return build_mission_agent_from_paths(
        paths,
        operator_id=args.operator_id,
        role=args.role,
        scopes=args.scopes,
        planner=planner,
        source="mission_cli",
    )


def _build_planner(args: argparse.Namespace) -> Any:
    """Build the appropriate planner based on CLI flags."""
    try:
        return _build_planner_shared(
            planner_type=args.planner,
            provider_base_url=args.provider_base_url,
            provider_api_key=args.provider_api_key,
            model=args.model,
            llm_trace_path=args.llm_trace_path,
            model_catalog_path=getattr(args, "catalog", None),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _mission_operator() -> dict[str, Any]:
    return {
        "operator_id": "mission-agent",
        "role": "operator",
        "control_scopes": ["task.submit", "task.cancel", "state.read"],
        "source": "mission_cli",
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
