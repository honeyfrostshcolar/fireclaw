from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Protocol
from uuid import uuid4

from fireclaw_core.agent.robot import RobotActionResult, RobotAdapter


ActionEventSink = Callable[[str, Dict[str, Any]], None]
ActionFeedbackSink = Callable[[Dict[str, Any]], None]
CancellationCheck = Callable[[], bool]


class RobotActionBackend(Protocol):
    def execute(
        self,
        action_type: str,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
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
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        self._emit_robot_feedback(action_type, inputs, feedback_sink)
        if action_type == "navigate_to_floor":
            return self._call_robot_action("navigate_to_floor", {"floor": int(inputs["floor"])}, feedback_sink, cancellation_requested)
        if action_type == "search_for_victims":
            return self._call_robot_action("search_for_victims", {"floor": int(inputs["floor"])}, feedback_sink, cancellation_requested)
        if action_type == "assess_victim":
            return self._call_robot_action("assess_victim", {"floor": int(inputs["floor"])}, feedback_sink, cancellation_requested)
        if action_type == "report_status":
            return self._call_robot_action("report_status", {"floor": int(inputs["floor"])}, feedback_sink, cancellation_requested)
        if action_type == "return_to_safe_zone":
            return self._call_robot_action("return_to_safe_zone", {}, feedback_sink, cancellation_requested)
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

    def _call_robot_action(
        self,
        action_type: str,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None,
        cancellation_requested: CancellationCheck | None,
    ) -> RobotActionResult:
        action = getattr(self.robot, action_type)
        try:
            return action(**inputs, feedback_sink=feedback_sink, cancellation_requested=cancellation_requested)
        except TypeError:
            return action(**inputs)

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
        feedback_sink = lambda feedback: self._emit_feedback(payload, feedback)
        try:
            result = self.backend.execute(
                action_type,
                inputs,
                feedback_sink=feedback_sink,
                cancellation_requested=cancellation_requested,
            )
        except TypeError:
            result = self.backend.execute(action_type, inputs, feedback_sink=feedback_sink)
        result.data.setdefault("action_id", action_id)
        result.data.setdefault("task_id", self.task_id)
        if result.status == "cancelled":
            self._emit("action.cancel_requested", {**payload, "status": "cancel_requested"})
            self._emit(
                "action.cancelled",
                {
                    **payload,
                    "status": "cancelled",
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
