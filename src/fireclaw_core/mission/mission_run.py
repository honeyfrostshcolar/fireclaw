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
import time
from threading import Condition, Event, RLock, Thread
from typing import Any, Callable, Mapping
from uuid import uuid4

from fireclaw_core.mission.mission_planner import MissionPlan
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
        self._event_sink: Callable[[str, dict[str, Any]], None] | None = None

    def set_event_sink(self, sink: Callable[[str, dict[str, Any]], None] | None) -> None:
        with self._condition:
            self._event_sink = sink

    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._condition:
            sink = self._event_sink
        if sink is not None:
            sink(event_type, payload)

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
    sealed_plan: MissionPlan | None = field(default=None, repr=False)
    plan_artifact_id: str | None = None
    plan_digest: str | None = None
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    result: dict[str, Any] | None = None
    final_report: dict[str, Any] | None = None
    error: str | None = None
    report_error: str | None = None
    report_started_at: str | None = None
    report_finished_at: str | None = None
    report_duration_ms: float | None = None
    control: MissionRunControl = field(default_factory=MissionRunControl, repr=False)
    thread: Thread | None = field(default=None, repr=False)
    report_thread: Thread | None = field(default=None, repr=False)

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
        if self.final_report is not None:
            report_status = str(
                self.final_report.get("status") or "ready"
            )
        elif self.terminal:
            report_status = "pending"
        else:
            report_status = "not_started"
        result["report_status"] = report_status
        result["report_pending"] = (
            self.terminal and self.final_report is None
        )
        if self.report_started_at is not None:
            result["report_started_at"] = self.report_started_at
        if self.report_finished_at is not None:
            result["report_finished_at"] = self.report_finished_at
        if self.report_duration_ms is not None:
            result["report_duration_ms"] = self.report_duration_ms
        if self.report_error is not None:
            result["report_error"] = self.report_error
        if self.error is not None:
            result["error"] = self.error
        if self.plan_artifact_id is not None:
            result["plan_artifact_id"] = self.plan_artifact_id
        if self.plan_digest is not None:
            result["plan_digest"] = self.plan_digest
            result["plan_source"] = "sealed_plan_artifact"
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

    def submit_preplanned(
        self,
        plan: MissionPlan,
        *,
        mission_id: str,
        operator: dict[str, Any] | None,
        artifact_id: str,
        plan_digest: str,
    ) -> dict[str, Any]:
        """Queue one immutable plan; the worker never calls the planner."""

        resolved_mission_id = mission_id.strip()
        if not resolved_mission_id:
            raise ValueError("mission_id must be non-empty")
        with self._lock:
            existing = self._runs.get(resolved_mission_id)
            if existing is not None:
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
                command=plan.command,
                operator=dict(operator) if isinstance(operator, dict) else None,
                use_scheduler=True,
                sealed_plan=plan,
                plan_artifact_id=artifact_id,
                plan_digest=plan_digest,
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
        self._emit(
            "mission.run_accepted",
            run,
            {
                "status": "queued",
                "plan_artifact_id": artifact_id,
                "plan_digest": plan_digest,
                "plan_source": "sealed_plan_artifact",
            },
        )
        return {
            "status": "accepted",
            "mission_id": run.mission_id,
            "run_id": run.run_id,
            "run_status": run.status,
            "created_at": run.created_at,
            "plan_artifact_id": artifact_id,
            "plan_digest": plan_digest,
            "plan_source": "sealed_plan_artifact",
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
                    "report_status": "pending",
                    "report_pending": run.terminal,
                }
            return dict(run.final_report)

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            threads = [
                thread
                for run in self._runs.values()
                for thread in (run.thread, run.report_thread)
                if thread is not None
            ]
        if wait:
            for thread in threads:
                if thread is not None:
                    thread.join(timeout=5)

    def _execute(self, run: MissionRun) -> None:
        run.control.set_event_sink(lambda et, p: self._emit(et, run, p))
        try:
            if not run.control.wait_until_resumed():
                self._finish_cancelled(run, None)
                return
            if run.sealed_plan is not None:
                self._set_status(run, "running")
                self._emit(
                    "mission.sealed_plan_loaded",
                    run,
                    {
                        "plan": run.sealed_plan.to_dict(),
                        "plan_artifact_id": run.plan_artifact_id,
                        "plan_digest": run.plan_digest,
                        "plan_source": "sealed_plan_artifact",
                    },
                )
                self._emit(
                    "mission.sealed_plan_execution_started",
                    run,
                    {
                        "plan_artifact_id": run.plan_artifact_id,
                        "plan_digest": run.plan_digest,
                        "plan_source": "sealed_plan_artifact",
                    },
                )
                result = self.mission_agent.execute_sealed_plan(
                    run.sealed_plan,
                    session_id=run.mission_id,
                    operator=run.operator,
                    artifact_id=str(run.plan_artifact_id),
                    plan_digest=str(run.plan_digest),
                    run_control=run.control,
                )
            else:
                self._set_status(run, "planning")
                result = self.mission_agent.plan_and_submit(
                    run.command,
                    session_id=run.mission_id,
                    operator=run.operator,
                    use_scheduler=run.use_scheduler,
                    run_control=run.control,
                )
            run.result = dict(result)
            if run.sealed_plan is None and (
                result.get("plan") is not None
                or result.get("intent") is not None
            ):
                self._emit(
                    "mission.planned",
                    run,
                    {
                        "intent": result.get("intent"),
                        "plan": result.get("plan"),
                        "plan_source": "planner",
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
            trace = self._mission_trace_snapshot(run)
            self._complete_and_schedule_report(
                run,
                status=final_status,
                trace=trace,
            )
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            logger.exception("Mission Run failed: %s", run.mission_id)
            run.error = f"{type(exc).__name__}: {exc}"
            run.result = {"status": "failed", "message": run.error}
            trace = self._mission_trace_snapshot(run)
            self._complete_and_schedule_report(
                run,
                status="failed",
                trace=trace,
                error=run.error,
            )
        finally:
            run.control.set_event_sink(None)

    def _finish_cancelled(self, run: MissionRun, result: dict[str, Any] | None) -> None:
        if result is not None:
            run.result = dict(result)
        trace = self._mission_trace_snapshot(run)
        self._complete_and_schedule_report(
            run,
            status="cancelled",
            trace=trace,
        )

    def _mission_trace_snapshot(self, run: MissionRun) -> dict[str, Any]:
        """Capture trace evidence without turning report lookup into a failure."""

        try:
            value = self.mission_agent.mission_trace(run.mission_id)
        except Exception as exc:  # pragma: no cover - adapter-specific
            logger.warning(
                "Mission trace snapshot failed for %s: %s",
                run.mission_id,
                exc,
                exc_info=True,
            )
            return {
                "mission_id": run.mission_id,
                "status": "unknown",
                "subtasks": [],
                "trace_error": f"{type(exc).__name__}: {exc}",
            }
        return dict(value) if isinstance(value, dict) else {
            "mission_id": run.mission_id,
            "status": "unknown",
            "subtasks": [],
        }

    def _complete_and_schedule_report(
        self,
        run: MissionRun,
        *,
        status: str,
        trace: dict[str, Any],
        error: str | None = None,
    ) -> None:
        """Publish physical completion first, then generate the report off-path."""

        report_start_gate = Event()
        report_thread = Thread(
            target=self._generate_report,
            args=(run, status, dict(trace), report_start_gate),
            name=f"fireclaw-report-{run.mission_id}",
            daemon=True,
        )
        with self._lock:
            run.report_thread = report_thread
            run.report_started_at = datetime.now(timezone.utc).isoformat()
            if error is not None:
                run.error = error
        self._set_status(run, status)
        pending_payload: dict[str, Any] = {
            "status": status,
            "report_status": "pending",
            "report_pending": True,
            "report_started_at": run.report_started_at,
        }
        if error is not None:
            pending_payload["error"] = error
        # Start a gated daemon before emitting lifecycle events so shutdown()
        # can always join a live thread; the gate preserves event ordering.
        report_thread.start()
        self._emit(
            "mission.report_generation_started",
            run,
            pending_payload,
        )
        self._emit(
            MISSION_TERMINAL_EVENT_BY_STATUS[status],
            run,
            pending_payload,
        )
        report_start_gate.set()

    def _generate_report(
        self,
        run: MissionRun,
        status: str,
        trace: dict[str, Any],
        start_gate: Event,
    ) -> None:
        """Generate and persist the advisory report without delaying terminal state."""

        start_gate.wait()
        report_started_monotonic = time.monotonic()
        report_error: str | None = None
        try:
            report = generate_mission_final_report(
                mission_agent=self.mission_agent,
                mission_id=run.mission_id,
                command=run.command,
                status=status,
                trace=trace,
                corrections=run.control.corrections(),
            )
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            report_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Mission final report generation failed for %s: %s",
                run.mission_id,
                exc,
                exc_info=True,
            )
            report = _deterministic_report_after_error(
                mission_id=run.mission_id,
                command=run.command,
                status=status,
                trace=trace,
                corrections=run.control.corrections(),
                error=report_error,
            )

        recorder = getattr(self.mission_agent, "record_final_report", None)
        if callable(recorder):
            try:
                recorder(run.mission_id, report)
            except Exception as exc:  # pragma: no cover - persistence-specific
                report["persistence_error"] = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Failed to persist final Mission report for %s",
                    run.mission_id,
                    exc_info=True,
                )
        with self._lock:
            run.final_report = dict(report)
            run.report_error = report_error
            run.report_finished_at = datetime.now(timezone.utc).isoformat()
            run.report_duration_ms = round(
                max(0.0, time.monotonic() - report_started_monotonic) * 1000,
                3,
            )
            run.updated_at = datetime.now(timezone.utc).isoformat()
            report_duration_ms = run.report_duration_ms
            report_finished_at = run.report_finished_at
        self._emit(
            "mission.report_ready",
            run,
            {
                **report,
                "report_status": "ready",
                "report_duration_ms": report_duration_ms,
                "report_finished_at": report_finished_at,
            },
        )

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
        event_payload = dict(payload)
        if run.plan_artifact_id is not None:
            event_payload.setdefault("plan_artifact_id", run.plan_artifact_id)
        if run.plan_digest is not None:
            event_payload.setdefault("plan_digest", run.plan_digest)
            event_payload.setdefault("plan_source", "sealed_plan_artifact")
        try:
            self.event_sink(event_type, run.mission_id, event_payload)
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


def _deterministic_report_after_error(
    *,
    mission_id: str,
    command: str,
    status: str,
    trace: dict[str, Any],
    corrections: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    error: str,
) -> dict[str, Any]:
    """Keep report retrieval useful even when the asynchronous generator fails."""

    robot_results = [
        dict(item)
        for item in trace.get("subtasks", [])
        if isinstance(item, dict)
    ]
    return {
        "mission_id": mission_id,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "deterministic_fallback",
        "summary": (
            f"Mission {mission_id} 已完成物理执行，最终报告生成失败；"
            "请查看任务轨迹。"
        ),
        "command": command,
        "robot_results": robot_results,
        "corrections": [dict(item) for item in corrections],
        "report_error": error,
    }
