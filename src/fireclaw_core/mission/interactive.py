from __future__ import annotations

import sys
import time
from typing import Any

from fireclaw_core.gateway.auth import resolve_gateway_api_token
from fireclaw_core.gateway.transport import GatewayTlsClientConfig
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient

TERMINAL_MISSION_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "completed",
    "escalated",
}


def parse_builtin_command(input_text: str) -> tuple[str, str | None]:
    text = input_text.strip()
    if not text:
        return "help", None
    if text in {"help", "quit", "exit", "status"}:
        return text, None
    if text.startswith("cancel "):
        mission_id = text[len("cancel "):].strip()
        return "cancel", mission_id
    return "submit", text


def display_event(event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or event.get("type") or "unknown")
    robot_id = str(event.get("robot_id") or "")
    task_id = str(event.get("task_id") or "")
    status = str(event.get("status") or event_type)
    if event_type in {
        "task.completed",
        "task.blocked",
        "task.escalated",
        "task.failed",
        "task.timed_out",
        "task.cancelled",
        "task.lost",
    }:
        print(f"[events] {robot_id}: {task_id} -> {event_type.split('.')[-1]}")
    else:
        print(f"[events] {robot_id}: {status}")


def run_interactive(
    server_url: str = "http://127.0.0.1:8766",
    timeout: float = 30.0,
    api_token: str | None = None,
    tls: GatewayTlsClientConfig | None = None,
) -> None:
    client = MissionGatewayClient(
        server_url,
        timeout=timeout,
        api_token=resolve_gateway_api_token(api_token),
        tls=tls,
    )
    print("FireClaw Mission Console")
    print(f"Connected to {server_url}")
    print("Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            raw = input("fireclaw> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        action, arg = parse_builtin_command(raw)
        if action in {"quit", "exit"}:
            return
        if action == "help":
            print("Commands: help, status, cancel <mission_id>, quit, or a natural-language mission.")
            continue
        if action == "status":
            print(client.get_fleet_state())
            continue
        if action == "cancel" and arg:
            print(client.cancel_mission(arg))
            continue
        if action == "submit" and arg:
            _submit_and_poll(client, arg)


def _submit_and_poll(client: MissionGatewayClient, command: str) -> None:
    result = client.submit_mission(command)
    mission_id = result.get("mission_id")
    print(result)
    if not isinstance(mission_id, str) or not mission_id:
        return
    seen = 0
    while True:
        try:
            events_body = client.get_mission_events(mission_id)
            events = events_body.get("events", [])
            if isinstance(events, list):
                for event in events[seen:]:
                    if isinstance(event, dict):
                        display_event(event)
                    seen += 1
            trace = client.get_mission_trace(mission_id)
            status = trace.get("status") or trace.get("mission_status")
            if status in TERMINAL_MISSION_STATUSES:
                print(f"[done] {status}")
                return
            time.sleep(0.5)
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return
