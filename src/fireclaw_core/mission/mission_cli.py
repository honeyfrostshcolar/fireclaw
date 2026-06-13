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


SUCCESS_STATUSES = {"accepted", "duplicate", "running", "succeeded"}
CANCEL_SUCCESS_STATUSES = {"cancel_requested", "already_terminal", "empty"}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "robot-gateway":
        from fireclaw_core.gateway.gateway import main as gateway_main
        return gateway_main(sys.argv[2:])

    parser = argparse.ArgumentParser(description="Run FireClaw mission-control commands.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    submit = subparsers.add_parser("submit-subtask", help="Submit an explicit subtask to a robot subagent.")
    submit.add_argument("--robot", required=True, help="Target robot_id from the robot registry.")
    submit.add_argument("--command", required=True, help="Natural-language command for the robot subagent.")
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

    events = subparsers.add_parser("events", help="Aggregate and list mission events from robot subagents.")
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

    lifecycle = subparsers.add_parser("lifecycle-check", help="Check task/subagent lifecycle consistency.")
    lifecycle.add_argument("--task-registry", required=True, help="Path to task registry JSONL.")
    lifecycle.add_argument("--subagent-registry", required=True, help="Path to subagent registry JSONL.")
    lifecycle.add_argument("--stale-threshold-seconds", type=float, default=300.0, help="Seconds before a task is considered stale.")

    serve = subparsers.add_parser("serve", help="Start a persistent MissionGateway server.")
    serve.add_argument("--config", type=Path, default=None, help="Path to fireclaw.toml config file.")
    serve.add_argument("--adapter", default=None, help="Robot adapter label for deployment metadata.")
    serve.add_argument("--ros1-config", default=None, help="Path to ROS1 adapter config for robot gateways.")
    serve.add_argument("--host", default=None, help="Host to bind.")
    serve.add_argument("--port", type=int, default=None, help="Port to bind.")
    serve.add_argument("--data-dir", type=Path, default=None, help="Persistent data directory.")
    serve.add_argument("--planner", choices=["deterministic", "llm"], default=None, help="Planner backend.")
    serve.add_argument("--provider-base-url", default=None, help="LLM provider base URL.")
    serve.add_argument("--provider-api-key", default=None, help="LLM provider API key.")
    serve.add_argument("--model", default=None, help="LLM model id.")
    serve.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
    serve.add_argument("--robot-agent", action="store_true", default=None, help="Enable robot-local agent planning.")
    serve.add_argument("--robot-agent-planner", choices=["deterministic", "llm"], default=None, help="Robot-local agent planner backend.")
    serve.add_argument("--robot-agent-provider-base-url", default=None, help="Robot-local agent LLM provider base URL.")
    serve.add_argument("--robot-agent-provider-api-key", default=None, help="Robot-local agent LLM provider API key.")
    serve.add_argument("--robot-agent-model", default=None, help="Robot-local agent LLM model id.")

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
            "llm_trace_path": args.llm_trace_path,
            "robot_agent_enabled": args.robot_agent if args.robot_agent else None,
            "robot_agent_planner": args.robot_agent_planner,
            "robot_agent_provider_base_url": args.robot_agent_provider_base_url,
            "robot_agent_provider_api_key": args.robot_agent_provider_api_key,
            "robot_agent_model": args.robot_agent_model,
        })
        run_server_blocking(
            adapter=str(merged.get("adapter", "simulator")),
            ros1_config=merged.get("ros1_config"),
            host=str(merged.get("host", "127.0.0.1")),
            port=int(merged.get("port", 8766)),
            planner_type=str(merged.get("planner_type", "deterministic")),
            provider_base_url=merged.get("provider_base_url"),
            provider_api_key=merged.get("provider_api_key"),
            model=merged.get("model"),
            llm_trace_path=merged.get("llm_trace_path"),
            data_dir=Path(str(merged.get("data_dir", "data"))),
            robot_agent_enabled=bool(merged.get("robot_agent_enabled", False)),
            robot_agent_planner=str(merged.get("robot_agent_planner", "deterministic")),
            robot_agent_provider_base_url=merged.get("robot_agent_provider_base_url"),
            robot_agent_provider_api_key=merged.get("robot_agent_provider_api_key"),
            robot_agent_model=merged.get("robot_agent_model"),
            robot_profiles=tuple(merged["mission_robot_profiles"]) if merged.get("mission_robot_profiles") else None,
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
    print(f"Error: unknown robot-profile subcommand: {args.robot_profile_command}", file=sys.stderr)
    return 1


def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--robot-registry", required=True, help="Path to robot registry JSON.")
    parser.add_argument("--mission-registry", required=True, help="Path to mission registry JSONL.")
    parser.add_argument("--operator-id", default="mission-agent", help="Operator ID for authorization.")
    parser.add_argument("--role", default="operator", help="Operator role (observer, operator, supervisor, admin).")
    parser.add_argument("--scopes", nargs="*", default=None, help="Explicit operator scopes (overrides role defaults).")


def _add_runtime_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--memory-path", default=None, help="Path to mission memory JSONL.")
    parser.add_argument("--memory-index", default=None, help="Path to SQLite memory index.")
    parser.add_argument("--task-registry", default=None, help="Path to task registry JSONL.")
    parser.add_argument("--subagent-registry", default=None, help="Path to subagent registry JSONL.")
    parser.add_argument("--session-lineage", default=None, help="Path to session lineage JSONL.")
    parser.add_argument("--task-flow", default=None, help="Path to task-flow registry JSONL.")
    parser.add_argument("--approval-path", default=None, help="Path to approval store JSONL.")


def _build_mission_runtime_paths(args: argparse.Namespace) -> MissionRuntimePaths:
    return MissionRuntimePaths(
        robot_registry=args.robot_registry,
        mission_registry=args.mission_registry,
        mission_memory=getattr(args, "memory_path", None),
        memory_index=getattr(args, "memory_index", None),
        task_registry=getattr(args, "task_registry", None),
        subagent_registry=getattr(args, "subagent_registry", None),
        session_lineage=getattr(args, "session_lineage", None),
        task_flow=getattr(args, "task_flow", None),
        approvals=getattr(args, "approval_path", None),
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
