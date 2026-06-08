from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission_planner import MissionPlanner
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import load_robot_registry


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

    cancel = subparsers.add_parser("cancel", help="Cancel all active subtasks for a mission.")
    cancel.add_argument("mission_id", help="Mission id to cancel.")
    _add_shared_paths(cancel)

    plan = subparsers.add_parser("plan-mission", help="Plan and submit mission subtasks from a natural-language command.")
    plan.add_argument("--command", required=True, help="Natural-language mission command.")
    plan.add_argument("--session-id", default=None, help="Mission/session id.")
    _add_shared_paths(plan)

    events = subparsers.add_parser("events", help="Aggregate and list mission events from robot subagents.")
    events.add_argument("mission_id", help="Mission id to collect events for.")
    events.add_argument("--robot-id", default=None, help="Filter events by robot id.")
    events.add_argument("--type", dest="event_type", default=None, help="Filter events by event type.")
    events.add_argument("--limit", type=int, default=200, help="Maximum number of events to return.")
    _add_shared_paths(events)

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
        agent = _build_mission_agent_with_planner(args)
        result = agent.plan_and_submit(args.command, session_id=args.session_id, operator=_mission_operator())
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
    if args.command_name == "corrections":
        return _handle_corrections(args)
    if args.command_name == "memory":
        return _handle_memory(args)
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


def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--robot-registry", required=True, help="Path to robot registry JSON.")
    parser.add_argument("--mission-registry", required=True, help="Path to mission registry JSONL.")
    parser.add_argument("--operator-id", default="mission-agent", help="Operator ID for authorization.")
    parser.add_argument("--role", default="operator", help="Operator role (observer, operator, supervisor, admin).")
    parser.add_argument("--scopes", nargs="*", default=None, help="Explicit operator scopes (overrides role defaults).")


def _build_mission_agent(args: argparse.Namespace) -> MissionAgent:
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    scopes = set(args.scopes) if args.scopes else scopes_for_role(args.role)
    operator = OperatorContext(
        operator_id=args.operator_id,
        role=args.role,
        control_scopes=scopes,
        source="mission_cli",
    )
    return MissionAgent(
        registry=load_robot_registry(args.robot_registry),
        mission_registry=JsonlMissionRegistry(args.mission_registry),
        control_policy=ControlPolicy(),
        operator=operator,
    )


def _build_mission_agent_with_planner(args: argparse.Namespace) -> MissionAgent:
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    scopes = set(args.scopes) if args.scopes else scopes_for_role(args.role)
    operator = OperatorContext(
        operator_id=args.operator_id,
        role=args.role,
        control_scopes=scopes,
        source="mission_cli",
    )
    return MissionAgent(
        registry=load_robot_registry(args.robot_registry),
        mission_registry=JsonlMissionRegistry(args.mission_registry),
        planner=MissionPlanner(),
        control_policy=ControlPolicy(),
        operator=operator,
    )


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
