from __future__ import annotations

import argparse
import sys
import time
from typing import Any, TextIO

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.infra.operator_projection import OperatorEventProjector
from fireclaw_core.execution.runtime_config import ADAPTER_CHOICES


def run_operator_command(
    command: str,
    *,
    gateway: FireClawGateway,
    session_id: str,
    out: TextIO = sys.stdout,
    poll_interval_seconds: float = 0.1,
    projector: OperatorEventProjector | None = None,
) -> dict[str, Any]:
    projector = projector or OperatorEventProjector()
    accepted = gateway.submit_agent(command, session_id=session_id)
    task_id = accepted["task_id"]
    seen_event_ids: set[str] = set()
    while True:
        _emit_new_events(
            gateway,
            session_id=session_id,
            task_id=task_id,
            seen_event_ids=seen_event_ids,
            projector=projector,
            out=out,
        )
        trace = gateway.task_trace(task_id)
        result = trace.get("result")
        if isinstance(result, dict):
            break
        time.sleep(max(0.001, poll_interval_seconds))

    _emit_new_events(
        gateway,
        session_id=session_id,
        task_id=task_id,
        seen_event_ids=seen_event_ids,
        projector=projector,
        out=out,
    )
    return result


def _emit_new_events(
    gateway: FireClawGateway,
    *,
    session_id: str,
    task_id: str | None,
    seen_event_ids: set[str],
    projector: OperatorEventProjector,
    out: TextIO,
) -> str | None:
    events = _events_for_current_task(gateway, session_id=session_id, task_id=task_id)
    for event in events:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        if task_id is None and event.get("type") == "task.received":
            value = event.get("task_id")
            task_id = value if isinstance(value, str) else None
        message = projector.project(event)
        if message:
            print(message, file=out, flush=True)
    return task_id


def _events_for_current_task(
    gateway: FireClawGateway,
    *,
    session_id: str,
    task_id: str | None,
) -> list[dict[str, Any]]:
    if task_id is not None:
        return gateway.events.events_for_task(task_id)
    events = list(reversed(gateway.events.latest_events(session_id=session_id, limit=100)))
    latest_task_id = None
    for event in events:
        if event.get("type") == "task.received":
            value = event.get("task_id")
            latest_task_id = value if isinstance(value, str) else None
    if latest_task_id is None:
        return []
    return gateway.events.events_for_task(latest_task_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a human-facing FireClaw operator console.")
    parser.add_argument(
        "command",
        help="Operator command, for example: 去坐标 (2.0, 1.5) 救人",
    )
    parser.add_argument("--adapter", choices=ADAPTER_CHOICES, default="simulator")
    parser.add_argument("--robot-id", default="fireclaw-operator")
    parser.add_argument("--session-id", default="operator")
    parser.add_argument("--memory-path", default="memory/fireclaw-operator-memory.jsonl")
    parser.add_argument("--event-path", default="memory/fireclaw-operator-events.jsonl")
    parser.add_argument("--skills-dir", default="skills")
    parser.add_argument("--no-workspace-skills", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=0.1)
    parser.add_argument("--real-run", action="store_true")
    args = parser.parse_args()

    gateway = FireClawGateway(
        GatewayConfig(
            adapter=args.adapter,
            robot_id=args.robot_id,
            memory_path=args.memory_path,
            event_path=args.event_path,
            workspace_skills_dir=None if args.no_workspace_skills else args.skills_dir,
            dry_run=not args.real_run,
            default_session_id=args.session_id,
        )
    )
    result = run_operator_command(
        args.command,
        gateway=gateway,
        session_id=args.session_id,
        poll_interval_seconds=args.poll_interval,
    )
    return 0 if result.get("status") in {"succeeded", "awaiting_confirmation", "clarify"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
