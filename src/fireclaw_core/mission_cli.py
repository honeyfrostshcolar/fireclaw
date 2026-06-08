from __future__ import annotations

import argparse
import json
from typing import Any

from fireclaw_core.mission_agent import MissionAgent
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
    parser.error(f"Unknown command: {args.command_name}")
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
