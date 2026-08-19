"""Background Mission Run lifecycle and operator controls.

This module is the Mission-side equivalent of OpenClaw's background run
handle: submission returns a stable run id, while execution continues in a
bounded worker.  The worker delegates planning and Robot dispatch to the
existing MissionAgent/Scheduler and only owns lifecycle coordination.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from threading import Condition, RLock, Thread
from typing import Any, Callable, Mapping
from uuid import uuid4

from fireclaw_core.mission.mission_report import generate_mission_final_report

logger = logging.getLogger(__name__)


RUN_ACTIVE_STATUSES = frozenset(
    {"queued", "planning", "running", "paused", "cancel_requested", "reporting"}
)
RUN_TERMINAL_STATUSES = frozenset(
    {"completed", "blocked", "escalated", "failed", "timed_out", "cancelled", "lost"}
)
MISSION_TERMINAL_EVENT_BY_STATUS = {
    "completed": "mission.completed",
    "blocked": "mission.blocked",
    "escalated": "mission.escalated",
    "failed": "mission.failed",
    "timed_out": "mission.timed_out",
    "cancelled": "mission.cancelled",
    "lost": "mission.lost",
}


class MissionRunControl:
    """Thread-safe pause/cancel/correction signal for one Mission Run."""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._paused = False
        self._cancel_requested = False
        self._corrections: list[dict[str, Any]] = []

    def request_pause(self) -> bool:
        with self._condition:
            if self._cancel_requested:
                return False
            changed = not self._paused
            self._paused = True
            self._condition.notify_all()
            return changed

    def request_resume(self) -> bool:
        with self._condition:
            changed = self._paused
            self._paused = False
            self._condition.notify_all()
            return changed

    def request_cancel(self) -> bool:
        with self._condition:
            changed = not self._cancel_requested
            self._cancel_requested = True
            self._paused = False
            self._condition.notify_all()
            return changed

    def add_correction(self, correction: dict[str, Any]) -> None:
        with self._condition:
            self._corrections.append(dict(correction))
            self._condition.notify_all()

    def corrections(self) -> list[dict[str, Any]]:
        with self._condition:
            return [dict(item) for item in self._corrections]

    def is_paused(self) -> bool:
        with self._condition:
            return self._paused

    def is_cancel_requested(self) -> bool:
        with self._condition:
            return self._cancel_requested

    def wait_until_resumed(self, timeout_seconds: float = 0.25) -> bool:
        """Wait while paused; return False when cancellation was requested."""
        with self._condition:
            while self._paused and not self._cancel_requested:
                self._condition.wait(timeout=max(0.01, timeout_seconds))
            return not self._cancel_requested


@dataclass
class MissionRun:
    run_id: str
    mission_id: str
    command: str
    operator: dict[str, Any] | None
    use_scheduler: bool
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    result: dict[str, Any] | None = None
    final_report: dict[str, Any] | None = None
    error: str | None = None
    control: MissionRunControl = field(default_factory=MissionRunControl, repr=False)
    thread: Thread | None = field(default=None, repr=False)

    @property
    def terminal(self) -> bool:
        return self.status in RUN_TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "command": self.command,
            "status": self.status,
            "run_status": self.status,
            "use_scheduler": self.use_scheduler,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal": self.terminal,
            "correction_count": len(self.control.corrections()),
        }
        if self.result is not None:
            result["result"] = dict(self.result)
        if self.final_report is not None:
            result["final_report"] = dict(self.final_report)
        if self.error is not None:
            result["error"] = self.error
        return result


RunEventSink = Callable[[str, str, Mapping[str, Any]], None]


class MissionRunManager:
    """Own background Mission workers and their operator control surface."""

    def __init__(
        self,
        mission_agent: Any,
        *,
        event_sink: RunEventSink | None = None,
        max_workers: int = 8,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.mission_agent = mission_agent
        self.event_sink = event_sink
        self.max_workers = max_workers
        self._lock = RLock()
        self._runs: dict[str, MissionRun] = {}

    def submit(
        self,
        command: str,
        *,
        mission_id: str | None = None,
        operator: dict[str, Any] | None = None,
        use_scheduler: bool = True,
    ) -> dict[str, Any]:
        resolved_mission_id = (
            mission_id.strip()
            if isinstance(mission_id, str) and mission_id.strip()
            else f"mission-{uuid4().hex}"
        )
        with self._lock:
            existing = self._runs.get(resolved_mission_id)
            if existing is not None and existing.status in RUN_ACTIVE_STATUSES:
                return {**existing.to_dict(), "status": "duplicate"}
            if sum(
                1
                for item in self._runs.values()
                if item.status in RUN_ACTIVE_STATUSES
            ) >= self.max_workers:
                return {
                    "status": "blocked",
                    "mission_id": resolved_mission_id,
                    "message": "Mission background worker capacity is exhausted.",
                }
            now = datetime.now(timezone.utc).isoformat()
            run = MissionRun(
                run_id=resolved_mission_id,
                mission_id=resolved_mission_id,
                command=command,
                operator=dict(operator) if isinstance(operator, dict) else None,
                use_scheduler=use_scheduler,
                created_at=now,
                updated_at=now,
            )
            self._runs[resolved_mission_id] = run
            self._create_mission_placeholder(run)
            thread = Thread(
                target=self._execute,
                args=(run,),
                name=f"fireclaw-mission-{resolved_mission_id}",
                daemon=True,
            )
            run.thread = thread
            thread.start()
        self._emit("mission.run_accepted", run, {"status": "queued"})
        return {
            "status": "accepted",
            "mission_id": run.mission_id,
            "run_id": run.run_id,
            "run_status": run.status,
            "created_at": run.created_at,
        }

    def get(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(mission_id)
            if run is None:
                return {"status": "not_found", "mission_id": mission_id}
            return run.to_dict()

    def pause(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(mission_id)
            if run is None:
                return {"status": "not_found", "mission_id": mission_id}
            if run.terminal:
                return {**run.to_dict(), "message": "Mission Run is already terminal."}
            run.control.request_pause()
            self._set_status(run, "paused")
            payload = run.to_dict()
        self._emit("mission.paused", run, payload)
        return payload

    def resume(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(mission_id)
            if run is None:
                return {"status": "not_found", "mission_id": mission_id}
            if run.terminal:
                return {**run.to_dict(), "message": "Mission Run is already terminal."}
            run.control.request_resume()
            if run.status == "paused":
                self._set_status(run, "running")
            payload = run.to_dict()
        self._emit("mission.resumed", run, payload)
        return payload

    def cancel(
        self,
        mission_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(mission_id)
            if run is None:
                return {"status": "not_found", "mission_id": mission_id}
            if run.terminal:
                return {**run.to_dict(), "message": "Mission Run is already terminal."}
            run.control.request_cancel()
            self._set_status(run, "cancel_requested")
            payload = run.to_dict()
        cancellation: dict[str, Any] | None = None
        try:
            cancellation = self.mission_agent.cancel_mission(
                mission_id,
                operator=operator,
            )
        except Exception as exc:  # pragma: no cover - adapter-specific
            logger.warning("Mission cancellation dispatch failed", exc_info=True)
            cancellation = {"status": "error", "message": str(exc)}
        payload["cancellation"] = cancellation
        self._emit("mission.cancel_requested", run, payload)
        return payload

    def correct(
        self,
        mission_id: str,
        *,
        correction: str,
        context: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(mission_id)
            if run is None:
                return {"status": "not_found", "mission_id": mission_id}
            if run.terminal:
                return {**run.to_dict(), "message": "Mission Run is already terminal."}
        result = self.mission_agent.record_correction(
            mission_id,
            correction=correction,
            context=context,
            robot_id=robot_id,
            subtask_id=subtask_id,
            operator=operator,
        )
        if result.get("status") == "recorded":
            correction_record = {
                "correction": correction,
                "context": context,
                "robot_id": robot_id,
                "subtask_id": subtask_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            run.control.add_correction(correction_record)
            self._emit("mission.correction_recorded", run, correction_record)
        return {**result, "run_id": mission_id}

    def report(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(mission_id)
            if run is None:
                return {"status": "not_found", "mission_id": mission_id}
            if run.final_report is None:
                return {
                    "status": "pending",
                    "mission_id": mission_id,
                    "run_status": run.status,
                }
            return dict(run.final_report)

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            threads = [run.thread for run in self._runs.values() if run.thread is not None]
        if wait:
            for thread in threads:
                if thread is not None:
                    thread.join(timeout=5)

    def _execute(self, run: MissionRun) -> None:
        try:
            if not run.control.wait_until_resumed():
                self._finish_cancelled(run, None)
                return
            self._set_status(run, "planning")
            result = self.mission_agent.plan_and_submit(
                run.command,
                session_id=run.mission_id,
                operator=run.operator,
                use_scheduler=run.use_scheduler,
                run_control=run.control,
            )
            run.result = dict(result)
            if result.get("plan") is not None or result.get("intent") is not None:
                self._emit(
                    "mission.planned",
                    run,
                    {
                        "intent": result.get("intent"),
                        "plan": result.get("plan"),
                    },
                )
            for subtask in result.get("subtask_results", []):
                if not isinstance(subtask, dict) or subtask.get("status") != "accepted":
                    continue
                self._emit(
                    "mission.subtask_dispatched",
                    run,
                    {
                        "robot_id": subtask.get("robot_id"),
                        "task_id": subtask.get("task_id"),
                        "status": subtask.get("status"),
                    },
                )
            if run.control.is_cancel_requested() and str(result.get("status")) in {
                "running",
                "planned",
                "accepted",
                "cancelled",
            }:
                self._finish_cancelled(run, result)
                return
            final_status = _run_status_from_result(result)
            if final_status == "running":
                self._set_status(run, "running")
                return
            self._set_status(run, "reporting")
            trace = self.mission_agent.mission_trace(run.mission_id)
            report = generate_mission_final_report(
                mission_agent=self.mission_agent,
                mission_id=run.mission_id,
                command=run.command,
                status=final_status,
                trace=trace,
                corrections=run.control.corrections(),
            )
            run.final_report = report
            recorder = getattr(self.mission_agent, "record_final_report", None)
            if callable(recorder):
                recorder(run.mission_id, report)
            self._set_status(run, final_status)
            self._emit("mission.report_ready", run, report)
            self._emit(
                MISSION_TERMINAL_EVENT_BY_STATUS[final_status],
                run,
                {"status": final_status, "final_report": report},
            )
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            logger.exception("Mission Run failed: %s", run.mission_id)
            run.error = f"{type(exc).__name__}: {exc}"
            run.result = {"status": "failed", "message": run.error}
            self._set_status(run, "reporting")
            trace = self.mission_agent.mission_trace(run.mission_id)
            run.final_report = generate_mission_final_report(
                mission_agent=self.mission_agent,
                mission_id=run.mission_id,
                command=run.command,
                status="failed",
                trace=trace,
                corrections=run.control.corrections(),
            )
            recorder = getattr(self.mission_agent, "record_final_report", None)
            if callable(recorder):
                recorder(run.mission_id, run.final_report)
            self._set_status(run, "failed")
            self._emit("mission.failed", run, {"error": run.error, "final_report": run.final_report})

    def _finish_cancelled(self, run: MissionRun, result: dict[str, Any] | None) -> None:
        if result is not None:
            run.result = dict(result)
        self._set_status(run, "reporting")
        trace = self.mission_agent.mission_trace(run.mission_id)
        run.final_report = generate_mission_final_report(
            mission_agent=self.mission_agent,
            mission_id=run.mission_id,
            command=run.command,
            status="cancelled",
            trace=trace,
            corrections=run.control.corrections(),
        )
        recorder = getattr(self.mission_agent, "record_final_report", None)
        if callable(recorder):
            recorder(run.mission_id, run.final_report)
        self._set_status(run, "cancelled")
        self._emit("mission.cancelled", run, {"final_report": run.final_report})

    def _create_mission_placeholder(self, run: MissionRun) -> None:
        registry = getattr(self.mission_agent, "mission_registry", None)
        if registry is None:
            return
        try:
            if registry.get_mission(run.mission_id) is None:
                registry.create_mission(
                    mission_id=run.mission_id,
                    session_id=run.mission_id,
                    command=run.command,
                    created_at=run.created_at,
                )
        except Exception:
            logger.warning("Failed to create Mission Run placeholder", exc_info=True)

    def _set_status(self, run: MissionRun, status: str) -> None:
        with self._lock:
            if run.terminal and status not in RUN_TERMINAL_STATUSES:
                return
            run.status = status
            run.updated_at = datetime.now(timezone.utc).isoformat()

    def _emit(self, event_type: str, run: MissionRun, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, run.mission_id, dict(payload))
        except Exception:
            logger.warning("Mission Run event sink failed", exc_info=True)


def _run_status_from_result(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "failed")
    if status in {"succeeded", "completed"}:
        return "completed"
    if status in {"blocked", "escalated", "failed", "timed_out", "cancelled", "lost"}:
        return status
    if status == "aborted":
        return "failed"
    if status in {"planned", "accepted", "running", "queued", "planning"}:
        return "running"
    if status in {"no_planner", "no_robots", "unauthorized", "denied", "error"}:
        return "blocked"
    return "failed"
