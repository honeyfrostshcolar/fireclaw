from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from fireclaw_core.approval_store import JsonlApprovalStore
from fireclaw_core.llm_planner import LLMMissionPlanner
from fireclaw_core.llm_trace import LLMTraceStore
from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission_planner import MissionPlanner
from fireclaw_core.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.provider import OpenAICompatProvider


SUCCESS_STATUSES = {"accepted", "duplicate", "running", "succeeded"}
CANCEL_SUCCESS_STATUSES = {"cancel_requested", "already_terminal", "empty"}


def main() -> int:
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
    )


def _build_planner(args: argparse.Namespace) -> Any:
    """Build the appropriate planner based on CLI flags."""
    if args.planner == "llm":
        if not args.provider_base_url or not args.provider_api_key or not args.model:
            raise SystemExit("--provider-base-url, --provider-api-key, and --model are required when --planner=llm")
        provider = OpenAICompatProvider(base_url=args.provider_base_url, api_key=args.provider_api_key)
        trace_store = LLMTraceStore(args.llm_trace_path) if args.llm_trace_path else None
        return LLMMissionPlanner(provider=provider, model_id=args.model, trace_store=trace_store)
    return MissionPlanner()


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
