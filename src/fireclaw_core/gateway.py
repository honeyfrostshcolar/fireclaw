from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fireclaw_core.agent import FireClawAgent
from fireclaw_core.event_ledger import EventLedger
from fireclaw_core.memory import JsonlMemoryStore
from fireclaw_core.runtime_config import ADAPTER_CHOICES, create_robot_adapter


@dataclass(frozen=True)
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    adapter: str = "dry-run"
    robot_id: str = "fireclaw-gateway"
    memory_path: str = "memory/fireclaw-gateway.jsonl"
    event_path: str = "memory/fireclaw-gateway-events.jsonl"
    workspace_skills_dir: str | None = "skills"
    dry_run: bool = True
    available_sensors: tuple[str, ...] = ()
    default_session_id: str = "default"
    max_active_execution_tasks: int = 1


@dataclass
class TaskControl:
    task_id: str
    session_id: str
    command: str
    started_at: str
    cancel_event: threading.Event = field(default_factory=threading.Event)


class FireClawGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.robot = create_robot_adapter(config.adapter, config.robot_id)
        self.memory = JsonlMemoryStore(config.memory_path)
        self.events = EventLedger(config.event_path)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event_lock = threading.Lock()
        self._task_threads: dict[str, threading.Thread] = {}
        self._task_controls: dict[str, TaskControl] = {}
        self._task_lock = threading.Lock()

    @property
    def base_url(self) -> str:
        if self._server is None:
            return f"http://{self.config.host}:{self.config.port}"
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler_class = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        handler_class = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for thread in list(self._task_threads.values()):
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    def run_agent(self, command: str, session_id: str | None = None) -> dict[str, Any]:
        task_id = f"task-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        return self._execute_agent_task(
            command=command,
            session_id=resolved_session_id,
            task_id=task_id,
            record_received=True,
        )

    def submit_agent(self, command: str, session_id: str | None = None) -> dict[str, Any]:
        task_id = f"task-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        started_at = datetime.now(timezone.utc).isoformat()
        control = TaskControl(
            task_id=task_id,
            session_id=resolved_session_id,
            command=command,
            started_at=started_at,
        )
        with self._task_lock:
            if len(self._task_controls) >= self.config.max_active_execution_tasks:
                active = self._active_task_summaries_locked()
                first_active = active[0] if active else {}
                return {
                    "status": "busy",
                    "message": "机器人当前已有任务在执行，请等待当前任务结束或取消后再提交。",
                    "active_task_id": first_active.get("task_id"),
                    "active_tasks": active,
                    "capacity": self._task_capacity_locked(),
                }
            self._task_controls[task_id] = control
        self._append_event(
            task_id=task_id,
            session_id=resolved_session_id,
            type="task.received",
            payload={"command": command},
        )

        def worker() -> None:
            try:
                self._execute_agent_task(
                    command=command,
                    session_id=resolved_session_id,
                    task_id=task_id,
                    record_received=False,
                    cancellation_requested=control.cancel_event.is_set,
                )
            except Exception as exc:
                self._append_event(
                    task_id=task_id,
                    session_id=resolved_session_id,
                    type="task.failed",
                    payload={
                        "status": "failed",
                        "message": str(exc),
                        "result": {
                            "status": "failed",
                            "task_id": task_id,
                            "session_id": resolved_session_id,
                            "message": str(exc),
                        },
                    },
                )
            finally:
                with self._task_lock:
                    self._task_threads.pop(task_id, None)
                    self._task_controls.pop(task_id, None)

        thread = threading.Thread(target=worker, daemon=True, name=f"fireclaw-task-{task_id}")
        with self._task_lock:
            self._task_threads[task_id] = thread
        thread.start()
        return {
            "status": "accepted",
            "task_id": task_id,
            "session_id": resolved_session_id,
            "message": "任务已接收，正在后台执行。",
        }

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._task_lock:
            control = self._task_controls.get(task_id)
        if control is None:
            trace = self.task_trace(task_id)
            if trace.get("result") is not None:
                return {
                    "status": "completed",
                    "task_id": task_id,
                    "message": "任务已经结束，无法取消。",
                }
            return {
                "status": "not_found",
                "task_id": task_id,
                "message": "没有找到正在运行的任务。",
            }
        already_requested = control.cancel_event.is_set()
        control.cancel_event.set()
        if not already_requested:
            self._append_event(
                task_id=task_id,
                session_id=control.session_id,
                type="task.cancel_requested",
                payload={"status": "cancel_requested", "task_id": task_id},
            )
        return {
            "status": "cancel_requested",
            "task_id": task_id,
            "session_id": control.session_id,
            "message": "已请求取消任务，当前 skill 返回后将停止后续步骤。",
        }

    def _execute_agent_task(
        self,
        *,
        command: str,
        session_id: str,
        task_id: str,
        record_received: bool,
        cancellation_requested=None,
    ) -> dict[str, Any]:
        if record_received:
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type="task.received",
                payload={"command": command},
            )
        agent = self._create_agent(
            task_id=task_id,
            session_id=session_id,
            cancellation_requested=cancellation_requested,
        )
        result = agent.run(command)
        result["task_id"] = task_id
        self._record_result_events(task_id, session_id, result)
        return result

    def list_skills(self, session_id: str | None = None) -> dict[str, Any]:
        return self.run_agent("你有哪些技能", session_id=session_id)

    def recent_memory(self, *, session_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        return {
            "records": self.memory.latest_records(limit=limit, session_id=session_id),
            "session_id": session_id,
            "limit": limit,
        }

    def recent_events(self, *, session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {
            "events": self.events.latest_events(limit=limit, session_id=session_id),
            "session_id": session_id,
            "limit": limit,
        }

    def task_trace(self, task_id: str) -> dict[str, Any]:
        events = self.events.events_for_task(task_id)
        result = self._task_result_from_events(events)
        return {
            "task_id": task_id,
            "events": events,
            "result": result,
            "status": self._task_status(task_id, events, result),
        }

    def state(self) -> dict[str, Any]:
        return {
            "robot_state": asdict(self.robot.get_robot_state()),
            "environment_state": asdict(self.robot.get_environment_state()),
            "task_capacity": self.task_capacity(),
            "active_tasks": self.active_tasks(),
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "robot_id": self.config.robot_id,
            "adapter": self.config.adapter,
            "dry_run": self.config.dry_run,
        }

    def task_capacity(self) -> dict[str, Any]:
        with self._task_lock:
            return self._task_capacity_locked()

    def active_tasks(self) -> list[dict[str, Any]]:
        with self._task_lock:
            return self._active_task_summaries_locked()

    def _record_result_events(
        self,
        task_id: str,
        session_id: str,
        result: dict[str, Any],
    ) -> None:
        planning = result.get("planning")
        if planning is not None and not self._has_event_type(task_id, "task.planned"):
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type="task.planned",
                payload=planning,
            )

        safety = result.get("safety")
        if safety is not None and not self._has_event_type(task_id, "safety.decided"):
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type="safety.decided",
                payload=safety,
            )

        confirmation = result.get("confirmation")
        if isinstance(confirmation, dict):
            if confirmation.get("status") == "pending":
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type="confirmation.pending",
                    payload=confirmation,
                )
            elif confirmation.get("status") == "confirmed":
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type="confirmation.confirmed",
                    payload=confirmation,
                )

        execution = result.get("execution") or {}
        if not self._has_live_skill_events(task_id):
            for step in execution.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                event_type = "skill.succeeded" if step.get("status") == "succeeded" else "skill.failed"
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type=event_type,
                    payload={
                        "skill_name": step.get("skill_name"),
                        "status": step.get("status"),
                        "error": step.get("error"),
                        "attempt_count": step.get("attempt_count"),
                    },
                )

        final_type = "task.cancelled" if result.get("status") == "cancelled" else "task.completed"
        self._append_event(
            task_id=task_id,
            session_id=session_id,
            type=final_type,
            payload={
                "status": result.get("status"),
                "message": result.get("message"),
                "result": result,
            },
        )

    def _task_result_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.get("type") in {"task.completed", "task.cancelled", "task.failed"}:
                payload = event.get("payload") or {}
                result = payload.get("result")
                if isinstance(result, dict):
                    return result
        return None

    def _has_live_skill_events(self, task_id: str) -> bool:
        live_types = {"skill.started", "skill.attempted", "skill.succeeded", "skill.failed"}
        return any(event.get("type") in live_types for event in self.events.events_for_task(task_id))

    def _has_event_type(self, task_id: str, event_type: str) -> bool:
        return any(event.get("type") == event_type for event in self.events.events_for_task(task_id))

    def _create_agent(
        self,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        cancellation_requested=None,
    ) -> FireClawAgent:
        resolved_session_id = session_id or self.config.default_session_id
        event_sink = None
        if task_id is not None:
            event_sink = lambda event_type, payload: self._append_event(
                task_id=task_id,
                session_id=resolved_session_id,
                type=event_type,
                payload=payload,
            )
        return FireClawAgent(
            robot=self.robot,
            memory=self.memory,
            workspace_skills_dir=self.config.workspace_skills_dir,
            dry_run=self.config.dry_run,
            available_sensors=set(self.config.available_sensors),
            session_id=resolved_session_id,
            event_sink=event_sink,
            cancellation_requested=cancellation_requested,
        )

    def _task_status(
        self,
        task_id: str,
        events: list[dict[str, Any]],
        result: dict[str, Any] | None,
    ) -> str:
        if result is not None:
            status = result.get("status")
            return status if isinstance(status, str) else "completed"
        if any(event.get("type") == "task.cancel_requested" for event in events):
            return "cancel_requested"
        with self._task_lock:
            if task_id in self._task_controls:
                return "running"
        return "unknown"

    def _task_capacity_locked(self) -> dict[str, Any]:
        return {
            "active_execution_tasks": len(self._task_controls),
            "max_active_execution_tasks": self.config.max_active_execution_tasks,
            "available_execution_slots": max(
                0,
                self.config.max_active_execution_tasks - len(self._task_controls),
            ),
        }

    def _active_task_summaries_locked(self) -> list[dict[str, Any]]:
        summaries = []
        for control in self._task_controls.values():
            summaries.append(
                {
                    "task_id": control.task_id,
                    "session_id": control.session_id,
                    "command": control.command,
                    "started_at": control.started_at,
                    "cancel_requested": control.cancel_event.is_set(),
                }
            )
        return summaries

    def _append_event(
        self,
        *,
        task_id: str,
        session_id: str,
        type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._event_lock:
            return self.events.append(
                task_id=task_id,
                session_id=session_id,
                type=type,
                payload=payload,
            )

    def _handler_class(self):
        gateway = self

        class GatewayRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                gateway._handle_get(self)

            def do_POST(self) -> None:
                gateway._handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        return GatewayRequestHandler

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self._write_json(handler, HTTPStatus.OK, self.health())
            return
        if parsed.path == "/state":
            self._write_json(handler, HTTPStatus.OK, self.state())
            return
        if parsed.path == "/skills":
            self._write_json(handler, HTTPStatus.OK, self.list_skills(_first(query, "session_id")))
            return
        if parsed.path == "/memory/recent":
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.recent_memory(
                    session_id=_first(query, "session_id"),
                    limit=_int_query(query, "limit", 5),
                ),
            )
            return
        if parsed.path == "/events/recent":
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.recent_events(
                    session_id=_first(query, "session_id"),
                    limit=_int_query(query, "limit", 20),
                ),
            )
            return
        task_events_id = _task_events_path(parsed.path)
        if task_events_id is not None:
            self._write_json(
                handler,
                HTTPStatus.OK,
                {"task_id": task_events_id, "events": self.events.events_for_task(task_events_id)},
            )
            return
        task_id = _task_path(parsed.path)
        if task_id is not None:
            self._write_json(handler, HTTPStatus.OK, self.task_trace(task_id))
            return
        self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        try:
            payload = self._read_json(handler)
            task_cancel_id = _task_cancel_path(parsed.path)
            if task_cancel_id is not None:
                result = self.cancel_task(task_cancel_id)
                status = HTTPStatus.OK if result["status"] != "not_found" else HTTPStatus.NOT_FOUND
                self._write_json(handler, status, result)
                return
            if parsed.path == "/tasks":
                command = payload.get("command")
                if not isinstance(command, str) or not command.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'command' is required.")
                    return
                result = self.submit_agent(command, session_id=_payload_session(payload, self.config.default_session_id))
                status = HTTPStatus.CONFLICT if result.get("status") == "busy" else HTTPStatus.ACCEPTED
                self._write_json(handler, status, result)
                return
            if parsed.path == "/confirm":
                result = self.submit_agent(
                    "确认执行",
                    session_id=_payload_session(payload, self.config.default_session_id),
                )
                self._write_json(
                    handler,
                    HTTPStatus.CONFLICT if result.get("status") == "busy" else HTTPStatus.ACCEPTED,
                    result,
                )
                return
            if parsed.path == "/cancel":
                result = self.submit_agent("取消", session_id=_payload_session(payload, self.config.default_session_id))
                self._write_json(
                    handler,
                    HTTPStatus.CONFLICT if result.get("status") == "busy" else HTTPStatus.ACCEPTED,
                    result,
                )
                return
            self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except json.JSONDecodeError:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.")

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = int(handler.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise json.JSONDecodeError("JSON body must be an object.", raw.decode("utf-8"), 0)
        return value

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _write_error(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, message: str) -> None:
        self._write_json(handler, status, {"status": "error", "message": message})


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    value = _first(query, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _payload_session(payload: dict[str, Any], default: str) -> str:
    value = payload.get("session_id")
    if isinstance(value, str) and value.strip():
        return value
    return default


def _task_events_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "events":
        return parts[1]
    return None


def _task_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "tasks":
        return parts[1]
    return None


def _task_cancel_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel":
        return parts[1]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FireClaw local HTTP gateway.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8765, help="HTTP bind port.")
    parser.add_argument("--adapter", choices=ADAPTER_CHOICES, default="dry-run")
    parser.add_argument("--robot-id", default="fireclaw-gateway")
    parser.add_argument("--memory-path", default="memory/fireclaw-gateway.jsonl")
    parser.add_argument("--event-path", default="memory/fireclaw-gateway-events.jsonl")
    parser.add_argument("--skills-dir", default="skills")
    parser.add_argument("--no-workspace-skills", action="store_true")
    parser.add_argument("--session-id", default="default")
    parser.add_argument("--max-active-execution-tasks", type=int, default=1)
    parser.add_argument("--available-sensor", action="append", default=[])
    parser.add_argument("--real-run", action="store_true")
    args = parser.parse_args()

    gateway = FireClawGateway(
        GatewayConfig(
            host=args.host,
            port=args.port,
            adapter=args.adapter,
            robot_id=args.robot_id,
            memory_path=args.memory_path,
            event_path=args.event_path,
            workspace_skills_dir=None if args.no_workspace_skills else args.skills_dir,
            dry_run=not args.real_run,
            available_sensors=tuple(args.available_sensor),
            default_session_id=args.session_id,
            max_active_execution_tasks=max(1, args.max_active_execution_tasks),
        )
    )
    print(
        json.dumps(
            {
                "status": "starting",
                "base_url": f"http://{args.host}:{args.port}",
                "adapter": args.adapter,
                "robot_id": args.robot_id,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
