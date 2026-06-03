from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from fireclaw_core.robot import RobotActionResult, RobotAdapter


ActionEventSink = Callable[[str, dict[str, Any]], None]
ActionFeedbackSink = Callable[[dict[str, Any]], None]
CancellationCheck = Callable[[], bool]


class RobotActionBackend(Protocol):
    def execute(
        self,
        action_type: str,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None = None,
    ) -> RobotActionResult:
        ...


@dataclass
class RobotAdapterActionBackend:
    robot: RobotAdapter

    def execute(
        self,
        action_type: str,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None = None,
    ) -> RobotActionResult:
        self._emit_robot_feedback(action_type, inputs, feedback_sink)
        if action_type == "navigate_to_floor":
            return self.robot.navigate_to_floor(int(inputs["floor"]))
        if action_type == "search_for_victims":
            return self.robot.search_for_victims(int(inputs["floor"]))
        if action_type == "assess_victim":
            return self.robot.assess_victim(int(inputs["floor"]))
        if action_type == "report_status":
            return self.robot.report_status(int(inputs["floor"]))
        if action_type == "return_to_safe_zone":
            return self.robot.return_to_safe_zone()
        timestamp = datetime.now(timezone.utc).isoformat()
        return RobotActionResult(
            ok=False,
            status="failed",
            robot_id=getattr(self.robot, "robot_id", "unknown"),
            mode=getattr(self.robot, "mode", "unknown"),
            action=action_type,
            dry_run=getattr(self.robot, "dry_run", True),
            data={},
            timestamp=timestamp,
            error=f"Unsupported robot action type: {action_type}",
        )

    def _emit_robot_feedback(
        self,
        action_type: str,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None,
    ) -> None:
        if feedback_sink is None:
            return
        feedback_provider = getattr(self.robot, "action_feedback", None)
        if not callable(feedback_provider):
            return
        for feedback in feedback_provider(action_type, inputs):
            if isinstance(feedback, dict):
                feedback_sink(feedback)


@dataclass
class RobotActionRuntime:
    backend: RobotActionBackend
    event_sink: ActionEventSink | None = None
    task_id: str | None = None

    def run(
        self,
        *,
        skill_name: str,
        action_type: str,
        inputs: dict[str, Any],
        dry_run: bool,
        risk_level: str,
        timeout_seconds: float | None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        action_id = f"action-{uuid4().hex}"
        payload = {
            "action_id": action_id,
            "task_id": self.task_id,
            "skill_name": skill_name,
            "action_type": action_type,
            "inputs": dict(inputs),
            "dry_run": dry_run,
            "risk_level": risk_level,
            "timeout_seconds": timeout_seconds,
        }
        self._emit("action.requested", {**payload, "status": "requested"})
        if cancellation_requested is not None and cancellation_requested():
            return self._cancelled_result(action_id, action_type, payload)
        self._emit("action.started", {**payload, "status": "started"})
        result = self.backend.execute(
            action_type,
            inputs,
            feedback_sink=lambda feedback: self._emit_feedback(payload, feedback),
        )
        result.data.setdefault("action_id", action_id)
        result.data.setdefault("task_id", self.task_id)
        terminal_type = "action.succeeded" if result.ok else "action.failed"
        self._emit(
            terminal_type,
            {
                **payload,
                "status": result.status,
                "output": {
                    "robot_id": result.robot_id,
                    "mode": result.mode,
                    "action": result.action,
                    "dry_run": result.dry_run,
                    **result.data,
                },
                "error": result.error,
            },
        )
        return result

    def _cancelled_result(
        self,
        action_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> RobotActionResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        self._emit("action.cancel_requested", {**payload, "status": "cancel_requested"})
        self._emit("action.cancelled", {**payload, "status": "cancelled"})
        return RobotActionResult(
            ok=False,
            status="cancelled",
            robot_id="unknown",
            mode="action_runtime",
            action=action_type,
            dry_run=bool(payload["dry_run"]),
            data={"action_id": action_id, "task_id": payload["task_id"]},
            timestamp=timestamp,
            error="Robot action cancelled before backend execution.",
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, payload)
        except Exception:
            return

    def _emit_feedback(self, action_payload: dict[str, Any], feedback: dict[str, Any]) -> None:
        self._emit(
            "action.feedback",
            {
                **action_payload,
                "status": "feedback",
                **dict(feedback),
            },
        )
