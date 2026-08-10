from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from inspect import Parameter, signature
from math import isfinite
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable, Dict, Literal, Protocol
from uuid import uuid4

from fireclaw_core.agent.robot import RobotActionResult, RobotAdapter


ActionEventSink = Callable[[str, Dict[str, Any]], None]
ActionFeedbackSink = Callable[[Dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
RobotActionHandler = Callable[..., RobotActionResult]
ActionCancellationReason = Literal[
    "operator_cancelled",
    "deadline_exceeded",
    "control_check_failed",
]

_ACTION_TERMINAL_EVENT_BY_STATUS = {
    "cancelled": "action.cancelled",
    "timed_out": "action.timed_out",
    "lost": "action.lost",
    "escalated": "action.escalated",
}


class ActionCancellationSignal:
    """Thread-safe union of operator cancellation and a monotonic deadline.

    Physical backends receive this object through the existing callable
    ``cancellation_requested`` boundary.  Calling it remains backward
    compatible, while the host can retain the first cancellation reason and
    the bounded acknowledgement deadline for audit and fail-safe handling.
    """

    def __init__(
        self,
        *,
        external_check: CancellationCheck | None,
        timeout_seconds: float | None,
        cancellation_ack_timeout_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._external_check = external_check
        self._clock = clock
        self._lock = Lock()
        self._reason: ActionCancellationReason | None = None
        self._requested_at_monotonic: float | None = None
        self._ack_deadline_monotonic: float | None = None
        self._control_error: str | None = None
        self.deadline_monotonic = (
            clock() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        self.cancellation_ack_timeout_seconds = (
            cancellation_ack_timeout_seconds
        )

    @property
    def reason(self) -> ActionCancellationReason | None:
        with self._lock:
            return self._reason

    @property
    def requested_at_monotonic(self) -> float | None:
        with self._lock:
            return self._requested_at_monotonic

    @property
    def cancellation_ack_deadline_monotonic(self) -> float | None:
        with self._lock:
            return self._ack_deadline_monotonic

    @property
    def control_error(self) -> str | None:
        with self._lock:
            return self._control_error

    def request(self, reason: ActionCancellationReason) -> bool:
        requested_at = self._clock()
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            self._requested_at_monotonic = requested_at
            self._ack_deadline_monotonic = (
                requested_at + self.cancellation_ack_timeout_seconds
            )
            return True

    def remaining_deadline_seconds(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        return max(0.0, self.deadline_monotonic - self._clock())

    def remaining_ack_seconds(self) -> float:
        deadline = self.cancellation_ack_deadline_monotonic
        if deadline is None:
            return self.cancellation_ack_timeout_seconds
        return max(0.0, deadline - self._clock())

    def __call__(self) -> bool:
        if self.reason is not None:
            return True
        if self._external_check is not None:
            try:
                if self._external_check():
                    self.request("operator_cancelled")
            except Exception as exc:
                with self._lock:
                    self._control_error = type(exc).__name__
                self.request("control_check_failed")
        if (
            self.reason is None
            and self.deadline_monotonic is not None
            and self._clock() >= self.deadline_monotonic
        ):
            self.request("deadline_exceeded")
        return self.reason is not None


def accepts_keyword_argument(handler: Callable[..., Any], name: str) -> bool:
    try:
        parameters = signature(handler).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        parameter.kind == Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def invoke_robot_action_handler(
    handler: RobotActionHandler,
    inputs: dict[str, Any],
    *,
    feedback_sink: ActionFeedbackSink | None = None,
    cancellation_requested: CancellationCheck | None = None,
) -> RobotActionResult:
    kwargs = dict(inputs)
    if accepts_keyword_argument(handler, "feedback_sink"):
        kwargs["feedback_sink"] = feedback_sink
    if accepts_keyword_argument(handler, "cancellation_requested"):
        kwargs["cancellation_requested"] = cancellation_requested
    return handler(**kwargs)


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
class RegisteredActionBackend:
    """Dispatch only handlers explicitly contributed by activated Plugins."""

    robot: RobotAdapter
    handlers: dict[str, RobotActionHandler] = field(default_factory=dict)

    def register_action(
        self,
        action_name: str,
        handler: RobotActionHandler,
        *,
        replace: bool = False,
    ) -> None:
        if not action_name.strip():
            raise ValueError("robot action name must not be empty")
        if action_name in self.handlers and not replace:
            raise ValueError(f"Robot action already registered: {action_name}")
        if not callable(handler):
            raise TypeError("robot action handler must be callable")
        self.handlers[action_name] = handler

    def execute(
        self,
        action_type: str,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        handler = self.handlers.get(action_type)
        if handler is not None:
            return self._call_robot_action(
                handler,
                inputs,
                feedback_sink,
                cancellation_requested,
            )
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
        action: RobotActionHandler,
        inputs: dict[str, Any],
        feedback_sink: ActionFeedbackSink | None,
        cancellation_requested: CancellationCheck | None,
    ) -> RobotActionResult:
        return invoke_robot_action_handler(
            action,
            inputs,
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
        )

@dataclass
class RobotActionRuntime:
    backend: RobotActionBackend
    event_sink: ActionEventSink | None = None
    task_id: str | None = None
    clock: Callable[[], float] = monotonic
    monitor_interval_seconds: float = 0.01
    default_cancellation_ack_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        _require_positive_finite(
            self.monitor_interval_seconds,
            "monitor_interval_seconds",
        )
        _require_positive_finite(
            self.default_cancellation_ack_timeout_seconds,
            "default_cancellation_ack_timeout_seconds",
        )

    def run(
        self,
        *,
        skill_name: str,
        action_type: str,
        inputs: dict[str, Any],
        dry_run: bool,
        risk_level: str,
        timeout_seconds: float | None,
        cancellation_ack_timeout_seconds: float | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        if timeout_seconds is not None:
            _require_positive_finite(timeout_seconds, "timeout_seconds")
        ack_timeout = (
            self.default_cancellation_ack_timeout_seconds
            if cancellation_ack_timeout_seconds is None
            else cancellation_ack_timeout_seconds
        )
        _require_positive_finite(
            ack_timeout,
            "cancellation_ack_timeout_seconds",
        )
        started_at = self.clock()
        cancellation = ActionCancellationSignal(
            external_check=cancellation_requested,
            timeout_seconds=timeout_seconds,
            cancellation_ack_timeout_seconds=float(ack_timeout),
            clock=self.clock,
        )
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
            "cancellation_ack_timeout_seconds": float(ack_timeout),
        }
        self._emit("action.requested", {**payload, "status": "requested"})
        if cancellation():
            self._emit_cancel_requested(payload, cancellation)
            result = self._pre_start_terminal_result(
                action_id=action_id,
                action_type=action_type,
                dry_run=dry_run,
                cancellation=cancellation,
            )
            self._emit_terminal(payload, result)
            return result
        self._emit("action.started", {**payload, "status": "started"})
        feedback_lock = Lock()
        terminal_closed = False

        def feedback_sink(feedback: dict[str, Any]) -> None:
            nonlocal terminal_closed
            with feedback_lock:
                if terminal_closed:
                    return
                self._emit_feedback(payload, feedback)

        execute_kwargs: dict[str, Any] = {}
        if accepts_keyword_argument(self.backend.execute, "feedback_sink"):
            execute_kwargs["feedback_sink"] = feedback_sink
        if accepts_keyword_argument(
            self.backend.execute,
            "cancellation_requested",
        ):
            execute_kwargs["cancellation_requested"] = cancellation

        completed = Event()
        execution: dict[str, Any] = {}

        def execute_backend() -> None:
            try:
                execution["result"] = self.backend.execute(
                    action_type,
                    inputs,
                    **execute_kwargs,
                )
            except BaseException as exc:  # Re-raised on the supervising thread.
                execution["error"] = exc
            finally:
                execution["completed_at_monotonic"] = self.clock()
                completed.set()

        worker = Thread(
            target=execute_backend,
            name=f"fireclaw-action-{action_id}",
            daemon=True,
        )
        worker.start()
        cancel_event_emitted = False

        while not completed.is_set():
            cancellation()
            if cancellation.reason is not None:
                self._emit_cancel_requested(payload, cancellation)
                cancel_event_emitted = True
                if not completed.wait(cancellation.remaining_ack_seconds()):
                    with feedback_lock:
                        terminal_closed = True
                    result = self._lost_result(
                        action_id=action_id,
                        action_type=action_type,
                        dry_run=dry_run,
                        cancellation=cancellation,
                        error=(
                            "Physical runtime did not acknowledge that it "
                            "stopped before the cancellation deadline."
                        ),
                    )
                    result.data["elapsed_seconds"] = max(
                        0.0,
                        self.clock() - started_at,
                    )
                    self._emit_terminal(payload, result)
                    return result
                break
            wait_seconds = self.monitor_interval_seconds
            remaining = cancellation.remaining_deadline_seconds()
            if remaining is not None:
                wait_seconds = min(wait_seconds, max(0.0, remaining))
            if wait_seconds <= 0:
                continue
            completed.wait(wait_seconds)

        completed_at = execution.get("completed_at_monotonic")
        requested_at = cancellation.requested_at_monotonic
        cancellation_won = (
            requested_at is not None
            and isinstance(completed_at, (int, float))
            and requested_at <= float(completed_at)
        )
        if cancellation_won and not cancel_event_emitted:
            self._emit_cancel_requested(payload, cancellation)

        error = execution.get("error")
        if error is not None:
            if cancellation_won:
                result = self._lost_result(
                    action_id=action_id,
                    action_type=action_type,
                    dry_run=dry_run,
                    cancellation=cancellation,
                    error=(
                        "Physical runtime raised while handling cancellation; "
                        "a safe stop was not acknowledged."
                    ),
                )
            else:
                raise error
        else:
            result = execution.get("result")
            if not isinstance(result, RobotActionResult):
                raise TypeError(
                    "Robot action backend must return RobotActionResult."
                )
            if cancellation_won:
                result = self._terminal_after_cancellation(
                    result,
                    action_id=action_id,
                    action_type=action_type,
                    dry_run=dry_run,
                    cancellation=cancellation,
                )
            else:
                result = self._normalize_backend_terminal(
                    result,
                    action_id=action_id,
                    action_type=action_type,
                    dry_run=dry_run,
                )

        result.data.setdefault("action_id", action_id)
        result.data.setdefault("task_id", self.task_id)
        result.data.setdefault(
            "elapsed_seconds",
            max(0.0, self.clock() - started_at),
        )
        if (
            result.status in {"cancelled", "timed_out"}
            and not cancel_event_emitted
            and cancellation.reason is None
        ):
            self._emit(
                "action.cancel_requested",
                {
                    **payload,
                    "status": "cancel_requested",
                    "cancellation_reason": "backend_cancelled",
                },
            )
        with feedback_lock:
            terminal_closed = True
        self._emit_terminal(payload, result)
        return result

    def _pre_start_terminal_result(
        self,
        *,
        action_id: str,
        action_type: str,
        dry_run: bool,
        cancellation: ActionCancellationSignal,
    ) -> RobotActionResult:
        reason = cancellation.reason or "operator_cancelled"
        status = (
            "cancelled"
            if reason == "operator_cancelled"
            else "timed_out"
            if reason == "deadline_exceeded"
            else "lost"
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        return RobotActionResult(
            ok=False,
            status=status,
            robot_id=self._backend_robot_id(),
            mode=self._backend_mode(),
            action=action_type,
            dry_run=dry_run,
            data={
                "action_id": action_id,
                "task_id": self.task_id,
                "backend_started": False,
                "cancellation_reason": reason,
                "cancellation_acknowledged": True,
                "runtime_stopped": True,
                "resource_release_safe": True,
            },
            timestamp=timestamp,
            error=(
                "Robot action cancelled before backend execution."
                if status == "cancelled"
                else "Robot action did not start because execution control failed."
            ),
        )

    def _terminal_after_cancellation(
        self,
        result: RobotActionResult,
        *,
        action_id: str,
        action_type: str,
        dry_run: bool,
        cancellation: ActionCancellationSignal,
    ) -> RobotActionResult:
        if not _stop_acknowledged(result):
            return self._lost_result(
                action_id=action_id,
                action_type=action_type,
                dry_run=dry_run,
                cancellation=cancellation,
                source=result,
                error=(
                    "Physical runtime returned after cancellation without "
                    "explicitly acknowledging that it stopped."
                ),
            )
        reason = cancellation.reason or "operator_cancelled"
        if reason == "control_check_failed":
            return self._lost_result(
                action_id=action_id,
                action_type=action_type,
                dry_run=dry_run,
                cancellation=cancellation,
                source=result,
                error="Physical action control check failed during execution.",
            )
        status = "timed_out" if reason == "deadline_exceeded" else "cancelled"
        data = {
            **dict(result.data),
            "action_id": action_id,
            "task_id": self.task_id,
            "backend_status": result.status,
            "cancellation_reason": reason,
            "cancellation_acknowledged": True,
            "runtime_stopped": True,
            "resource_release_safe": True,
        }
        return replace(
            result,
            ok=False,
            status=status,
            data=data,
            error=(
                result.error
                or (
                    "Physical action exceeded its deadline; the runtime "
                    "confirmed that it stopped."
                    if status == "timed_out"
                    else "Physical action was cancelled and the runtime "
                    "confirmed that it stopped."
                )
            ),
        )

    def _normalize_backend_terminal(
        self,
        result: RobotActionResult,
        *,
        action_id: str,
        action_type: str,
        dry_run: bool,
    ) -> RobotActionResult:
        if (
            result.data.get("runtime_stopped") is False
            or result.data.get("resource_release_safe") is False
        ):
            return self._lost_result(
                action_id=action_id,
                action_type=action_type,
                dry_run=dry_run,
                source=result,
                error=(
                    result.error
                    or "Physical runtime returned without confirming a safe stop."
                ),
            )
        if result.status in {"cancelled", "timed_out"}:
            if not _stop_acknowledged(result):
                return self._lost_result(
                    action_id=action_id,
                    action_type=action_type,
                    dry_run=dry_run,
                    source=result,
                    error=(
                        "Physical runtime reported cancellation without an "
                        "explicit safe-stop acknowledgement."
                    ),
                )
            result.data.setdefault("resource_release_safe", True)
        elif result.status == "lost":
            result.ok = False
            result.data.setdefault("cancellation_acknowledged", False)
            result.data.setdefault("runtime_stopped", False)
            result.data["resource_release_safe"] = False
        return result

    def _lost_result(
        self,
        *,
        action_id: str,
        action_type: str,
        dry_run: bool,
        error: str,
        cancellation: ActionCancellationSignal | None = None,
        source: RobotActionResult | None = None,
    ) -> RobotActionResult:
        data = dict(source.data) if source is not None else {}
        if source is not None:
            data.setdefault("backend_status", source.status)
        data.update(
            {
                "action_id": action_id,
                "task_id": self.task_id,
                "cancellation_reason": (
                    cancellation.reason
                    if cancellation is not None
                    else data.get("cancellation_reason") or "backend_cancelled"
                ),
                "cancellation_acknowledged": False,
                "runtime_stopped": False,
                "resource_release_safe": False,
            }
        )
        if cancellation is not None and cancellation.control_error is not None:
            data["control_error"] = cancellation.control_error
        return RobotActionResult(
            ok=False,
            status="lost",
            robot_id=(source.robot_id if source is not None else self._backend_robot_id()),
            mode=(source.mode if source is not None else self._backend_mode()),
            action=(source.action if source is not None else action_type),
            dry_run=(source.dry_run if source is not None else dry_run),
            data=data,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error=error,
        )

    def _emit_cancel_requested(
        self,
        payload: dict[str, Any],
        cancellation: ActionCancellationSignal,
    ) -> None:
        self._emit(
            "action.cancel_requested",
            {
                **payload,
                "status": "cancel_requested",
                "cancellation_reason": cancellation.reason,
                "requested_at_monotonic": cancellation.requested_at_monotonic,
            },
        )

    def _emit_terminal(
        self,
        payload: dict[str, Any],
        result: RobotActionResult,
    ) -> None:
        terminal_type = _ACTION_TERMINAL_EVENT_BY_STATUS.get(
            result.status,
            "action.succeeded" if result.ok else "action.failed",
        )
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

    def _backend_robot_id(self) -> str:
        robot = getattr(self.backend, "robot", None)
        return str(getattr(robot, "robot_id", "unknown"))

    def _backend_mode(self) -> str:
        robot = getattr(self.backend, "robot", None)
        return str(getattr(robot, "mode", "action_runtime"))

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


def _stop_acknowledged(result: RobotActionResult) -> bool:
    return (
        result.data.get("cancellation_acknowledged") is True
        and result.data.get("runtime_stopped") is True
    )


def _require_positive_finite(value: float, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")
