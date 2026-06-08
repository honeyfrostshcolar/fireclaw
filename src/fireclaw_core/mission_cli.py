from __future__ import annotations

import argparse
import json
from typing import Any

from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import load_robot_registry


SUCCESS_STATUSES = {"accepted", "duplicate", "running", "succeeded"}


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
    parser.error(f"Unknown command: {args.command_name}")
    return 1


def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--robot-registry", required=True, help="Path to robot registry JSON.")
    parser.add_argument("--mission-registry", required=True, help="Path to mission registry JSONL.")


def _build_mission_agent(args: argparse.Namespace) -> MissionAgent:
    return MissionAgent(
        registry=load_robot_registry(args.robot_registry),
        mission_registry=JsonlMissionRegistry(args.mission_registry),
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
