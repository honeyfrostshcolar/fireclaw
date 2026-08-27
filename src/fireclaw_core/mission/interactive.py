from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fireclaw_core.gateway.auth import resolve_gateway_api_token
from fireclaw_core.gateway.transport import GatewayTlsClientConfig
from fireclaw_core.mission.console_input import ConsoleInput
from fireclaw_core.mission.mission_gateway_client import (
    MissionGatewayClient,
    MissionGatewayRequestError,
    MissionGatewayStreamUnsupported,
)
from fireclaw_core.mission.terminal_renderer import (
    TerminalAgentRenderer,
    sanitize_terminal_text,
)

TERMINAL_MISSION_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "completed",
    "escalated",
    "blocked",
    "timed_out",
    "lost",
}

_MISSION_TERMINAL_EVENTS = {
    "mission.completed": "succeeded",
    "mission.failed": "failed",
    "mission.cancelled": "cancelled",
    "mission.escalated": "escalated",
    "mission.blocked": "blocked",
    "mission.timed_out": "timed_out",
    "mission.lost": "lost",
}

_TERMINAL_STATUS_LABELS = {
    "succeeded": "已完成",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "escalated": "等待人工介入",
    "blocked": "已阻塞",
    "timed_out": "已超时",
    "lost": "状态失联",
}

_MISSION_OPERATION_LABELS = {
    "assemble_context": "🧩 组装可信规划上下文",
    "inspect_state": "🔍 探查机器人状态",
    "propose_plan": "🛡️ 制定并校验计划",
    "request_clarification": "❓ 发起操作员澄清",
    "request_observation": "👁️ 请求主动观测",
    "execute_agent_tool": "⚡ 执行 Agent Tool",
    "escalate": "⚠️ 升级人工介入",
    "policy_error": "🛑 模型决策失败",
    "invalid_decision": "🛑 拒绝无效决策",
}

_MISSION_OUTCOME_LABELS = {
    "accepted": "校验通过",
    "observed": "已获得观察",
    "clarification_requested": "需要操作员澄清",
    "requested": "请求已生成",
    "rejected": "未通过校验",
    "failed": "执行失败",
    "terminal": "规划阶段结束",
    "succeeded": "执行成功",
}

_PLANNING_STAGE_LABELS = {
    "readiness": "就绪检查",
    "mission_state_snapshot": "任务状态快照",
    "memory_write": "memory 写入",
    "planner_memory_context": "规划 memory/context",
    "planner_context": "规划上下文对象",
    "context_assembly": "模型上下文组装",
    "context_fit": "模型上下文预算适配",
    "provider_request": "LLM 请求",
    "state_observation": "状态观察",
    "plan_validation": "确定性计划校验",
    "relative_target_binding": "相对目标朝向绑定",
    "plan_artifact_seal": "计划封存",
    "mission_trace_persist": "规划轨迹持久化",
    "sse_finalize": "SSE 收尾",
}


# Execution events are intentionally grouped into operator-facing phases.  A
# phase is a presentation concept, not a new backend state: it lets the TUI
# answer "where is the time going?" while preserving the original event type
# and payload for audit/replay.
_EXECUTION_PHASE_LABELS = {
    "planning": "规划",
    "confirmation": "计划确认",
    "robot_planning": "Robot Agent 规划",
    "authorization": "执行授权",
    "queue": "排队/下发",
    "execution": "物理执行",
    "recovery": "恢复/重试",
    "terminal": "终态收敛",
}

_EXECUTION_PHASE_BY_EVENT = {
    "mission.planning": "planning",
    "mission.plan_confirmed": "confirmation",
    # Robot Gateway confirmation records belong to the execution
    # authorization handshake, not to the operator's sealed-plan preview.
    "confirmation.pending": "authorization",
    "confirmation.confirmed": "authorization",
    "authorization.requested": "authorization",
    "authorization.approved": "authorization",
    "authorization.denied": "authorization",
    "authorization.expired": "authorization",
    "authorization.cancelled": "authorization",
    "task.awaiting_confirmation": "authorization",
    "mission.run_accepted": "queue",
    "mission.sealed_plan_loaded": "queue",
    "mission.sealed_execution_started": "queue",
    "mission.sealed_plan_execution_started": "queue",
    "task.received": "queue",
    "task.planned": "queue",
    "subtask.submitted": "queue",
    "task.resume_scheduled": "recovery",
    "task.resume_started": "recovery",
    "task.authorized_plan_resumed": "recovery",
    "task.resume_rejected": "recovery",
    "task.resume_failed": "recovery",
    "robot_agent.pending_operation_reconciled": "recovery",
    "robot_agent.pending_operation_unresolved": "recovery",
    "mission.running": "execution",
    "task.running": "execution",
    "subtask.status_changed": "execution",
    "skill.started": "execution",
    "skill.succeeded": "execution",
    "skill.failed": "execution",
    "action.feedback": "execution",
    "ros.log": "execution",
    "robot_agent.deliberation_started": "robot_planning",
    "robot_agent.deliberation_finished": "robot_planning",
    "robot_agent.decision": "robot_planning",
    "robot_agent.observation": "execution",
}

# These events are useful for audit/replay, but are implementation details for
# the operator-facing console.  OpenClaw keeps thinking/tool internals out of
# the default chat surface; FireClaw follows the same default and exposes them
# with ``mission --verbose`` when diagnosing a run.
_INTERNAL_EXECUTION_EVENTS = {
    "robot_agent.deliberation_started",
    "robot_agent.deliberation_finished",
    "robot_agent.decision",
    "robot_agent.observation",
}

# Only stable identity/context may flow from one event to a later event for the
# same task. Event facts (source timestamp/type, durations, reason codes,
# authorization ids, messages) must never be cached: doing so previously made
# unrelated Mission events look like stale Robot confirmation events.
_TASK_CONTEXT_KEYS = (
    "mission_id",
    "robot_id",
    "task_id",
    "node_id",
    "plan_id",
    "plan_step_index",
    "plan_step_total",
    "plan_step_command",
)

# The complete authorization/recovery lifecycle remains in the event ledger
# and is shown with ``--verbose``. The default operator stream folds normal
# intermediate records into one waiting row and one approved/resumed row.
_DEFAULT_FOLDED_EXECUTION_EVENTS = frozenset({
    "authorization.approved",
    "confirmation.pending",
    "confirmation.confirmed",
    "task.awaiting_confirmation",
    "task.resume_scheduled",
    "task.resume_started",
    "task.received",
    "task.planned",
    "task.running",
    "subtask.submitted",
    "subtask.status_changed",
    # Legacy synchronous submission may still emit this after returning. The
    # current sealed-plan run no longer emits post-hoc dispatch records.
    "mission.subtask_dispatched",
})

_PHASE_START_EVENTS = {
    "authorization.requested": "authorization",
    "task.resume_scheduled": "recovery",
    "task.resume_started": "recovery",
    "skill.started": "execution",
}

_PHASE_END_EVENTS = {
    "authorization.approved": ("authorization",),
    "authorization.denied": ("authorization",),
    "authorization.expired": ("authorization",),
    "authorization.cancelled": ("authorization",),
    "task.authorized_plan_resumed": ("authorization", "recovery"),
    "task.resume_rejected": ("recovery",),
    "task.resume_failed": ("recovery",),
    "skill.succeeded": ("execution",),
    "skill.failed": ("execution",),
}


def _planning_stage_label(stage: str) -> str:
    return _PLANNING_STAGE_LABELS.get(stage, stage)


def _planning_stage_key_label(key: str) -> str:
    base = key
    record_suffix = ""
    if ":" in base:
        base, record = base.split(":", 1)
        record_suffix = f" · {record}"
    suffix = ""
    if "[" in base:
        base, iteration = base.split("[", 1)
        suffix = "[" + iteration
    return _planning_stage_label(base) + suffix + record_suffix


def _planning_timing_entries(
    timing: dict[str, Any] | None,
) -> list[tuple[str, float]]:
    """Return numeric planning stages in seconds, sorted by contribution."""

    if not isinstance(timing, dict):
        return []
    by_stage = timing.get("by_stage_ms")
    if not isinstance(by_stage, dict):
        return []
    entries = [
        (
            _planning_stage_key_label(str(key)),
            max(0.0, float(value)) / 1000.0,
        )
        for key, value in by_stage.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return sorted(entries, key=lambda item: item[1], reverse=True)


def _planning_timing_summary(timing: dict[str, Any] | None) -> str | None:
    """Build a short operator-facing timing summary.

    The full per-stage ledger remains available in verbose mode.  The default
    console only needs the dominant contributors so it can answer where time
    went without turning every backend event into a durable terminal block.
    """

    entries = _planning_timing_entries(timing)
    if not entries:
        return None
    top = entries[:3]
    return "主要耗时：" + "；".join(
        f"{label} {seconds:.1f} 秒" for label, seconds in top
    )

_EXECUTION_TERMINAL_EVENTS = set(_MISSION_TERMINAL_EVENTS)


def _phase_for_event(event_type: str, payload: dict[str, Any]) -> str | None:
    """Return the stable TUI phase for an event (without changing the event)."""

    semantic_event_type = _semantic_execution_event_type(event_type, payload)
    phase = _EXECUTION_PHASE_BY_EVENT.get(semantic_event_type)
    if phase is not None:
        return phase
    if event_type in _EXECUTION_TERMINAL_EVENTS:
        return "terminal"
    return _EXECUTION_PHASE_BY_EVENT.get(event_type)


def _semantic_execution_event_type(
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """Resolve the original Robot event wrapped by a Mission relay event."""

    source_event_type = payload.get("source_event_type")
    if isinstance(source_event_type, str) and source_event_type.strip():
        return source_event_type.strip()
    return event_type


def _task_context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract cache-safe task identity without retaining event facts."""

    return {
        key: payload[key]
        for key in _TASK_CONTEXT_KEYS
        if key in payload and payload[key] is not None
    }


def _event_timestamp_seconds(
    event: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[float | None, str | None]:
    """Read the producer timestamp, retaining whether it is server-side.

    Robot trace events are relayed after a polling interval.  The relay adds
    ``source_timestamp`` so the TUI does not mistake that polling delay for
    physical execution time.  The envelope timestamp remains the fallback for
    events emitted directly by the Mission Gateway.
    """

    candidates = (
        (payload.get("source_timestamp"), "机器人事件时间戳"),
        (event.get("timestamp"), "网关事件时间戳"),
        (payload.get("timestamp"), "事件载荷时间戳"),
        (payload.get("occurred_at"), "事件载荷时间戳"),
    )
    for value, source in candidates:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value > 0:
                return float(value), source
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            return datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            ).timestamp(), source
        except (TypeError, ValueError, OverflowError):
            continue
    return None, None


def _format_seconds(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return f"{float(value):.1f} 秒"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _is_interactive_output() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _character_display_width(character: str) -> int:
    if character in {"\n", "\r"}:
        return 0
    if unicodedata.combining(character):
        return 0
    if unicodedata.category(character) in {"Cc", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def _display_width(text: str) -> int:
    return sum(_character_display_width(character) for character in text)


def _truncate_to_display_width(text: str, maximum_width: int) -> str:
    """Truncate plain text by terminal cells, preserving combining marks."""

    if maximum_width <= 0:
        return ""
    text = text.replace("\r", " ").replace("\n", " ")
    if _display_width(text) <= maximum_width:
        return text
    ellipsis = "…"
    ellipsis_width = _display_width(ellipsis)
    if maximum_width <= ellipsis_width:
        return ellipsis
    content_width = maximum_width - ellipsis_width
    result: list[str] = []
    used = 0
    for character in text:
        character_width = _character_display_width(character)
        if character_width == 0:
            if result:
                result.append(character)
            continue
        if used + character_width > content_width:
            break
        result.append(character)
        used += character_width
    return "".join(result) + ellipsis


def _render_activity_line(
    frame: str,
    message: str,
    elapsed: float,
    *,
    style: _Style,
    columns: int | None = None,
) -> str:
    """Render one honest, single-line activity update within terminal width."""

    if columns is None:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    # Leave one cell unused so terminals that wrap in the final column do not
    # leave spinner fragments on the following line.
    available_width = max(1, columns - 1)
    prefix = f"{frame} "
    suffix = f" ({elapsed:.1f}s)"
    prefix_width = _display_width(prefix)
    suffix_width = _display_width(suffix)
    if available_width <= prefix_width:
        visible_frame = _truncate_to_display_width(frame, available_width)
        return f"\r\033[K{style.cyan(visible_frame)}"
    message_width = available_width - prefix_width - suffix_width
    if message_width < 4:
        suffix = ""
        message_width = available_width - prefix_width
    visible_message = _truncate_to_display_width(message, message_width)
    return (
        f"\r\033[K{style.cyan(frame)} "
        f"{style.bold(visible_message)}{style.gray(suffix)}"
    )


class _Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._c("1", text)

    def dim(self, text: str) -> str:
        return self._c("2", text)

    def cyan(self, text: str) -> str:
        return self._c("36", text)

    def yellow(self, text: str) -> str:
        return self._c("33", text)

    def green(self, text: str) -> str:
        return self._c("32", text)

    def red(self, text: str) -> str:
        return self._c("31", text)

    def magenta(self, text: str) -> str:
        return self._c("35", text)

    def blue(self, text: str) -> str:
        return self._c("34", text)

    def gray(self, text: str) -> str:
        return self._c("90", text)


_GLOBAL_STYLE = _Style(_supports_color())


def _sanitize_terminal_text(value: Any) -> str:
    """Remove terminal control sequences from remote Agent/ROS text."""

    return sanitize_terminal_text(value)


def _wrap_to_display_width(text: str, maximum_width: int) -> list[str]:
    """Wrap plain text by terminal cells while preferring word boundaries."""

    if maximum_width <= 0:
        return [text]
    remaining = text
    wrapped: list[str] = []
    while _display_width(remaining) > maximum_width:
        used = 0
        cut = 0
        for index, character in enumerate(remaining):
            width = _character_display_width(character)
            if used + width > maximum_width:
                break
            used += width
            cut = index + 1
        if cut <= 0:
            cut = 1
        candidate = remaining[:cut]
        word_break = max(candidate.rfind(" "), candidate.rfind("\t"))
        if word_break >= max(8, cut // 2):
            cut = word_break
        part = remaining[:cut].rstrip()
        wrapped.append(part or remaining[:cut])
        remaining = remaining[cut:].lstrip()
    wrapped.append(remaining)
    return wrapped


def _compact_identifier(value: str, maximum_characters: int = 24) -> str:
    if len(value) <= maximum_characters:
        return value
    return value[: maximum_characters - 1] + "…"


class MissionTerminalRenderer(TerminalAgentRenderer):
    """Compatibility facade over the semantic Rich activity renderer.

    Existing Mission code may still call ``block`` or ``activity`` while new
    code uses ``render_thought``, ``render_tool_call`` and
    ``render_final_response`` directly.  All paths share Rich's single console
    ownership and the same complete-block render lock.
    """

    def __init__(
        self,
        *,
        style: _Style | None = None,
        stream: Any | None = None,
    ) -> None:
        self.style = style or _GLOBAL_STYLE
        super().__init__(
            stream=stream or sys.stdout,
            force_terminal=self.style.enabled,
        )

    def block(
        self,
        header: str,
        lines: list[str] | tuple[str, ...],
        *,
        tone: str = "cyan",
        muted: bool = False,
    ) -> None:
        if muted:
            self.render_thought(None, label=header, details=lines)
            return
        self.render_primary_status(
            header,
            lines,
            tone=self._rich_tone(tone),
        )

    def activity(
        self,
        header: str,
        lines: list[str] | tuple[str, ...] = (),
        *,
        tone: str = "cyan",
        muted: bool = False,
    ) -> None:
        """Append one Codex/OpenClaw-style durable activity entry."""

        self.block(header, lines, tone=tone, muted=muted)

    @staticmethod
    def _rich_tone(tone: str) -> str:
        if tone == "green":
            return "success"
        if tone == "yellow":
            return "warning"
        if tone == "red":
            return "error"
        return "primary"


class LiveActivityIndicator:
    def __init__(
        self,
        message: str,
        style: _Style | None = None,
        renderer: MissionTerminalRenderer | None = None,
    ):
        self.message = message
        self.style = style or _GLOBAL_STYLE
        self.renderer = renderer or MissionTerminalRenderer(style=self.style)
        self._started_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: Any | None = None

    def __enter__(self) -> LiveActivityIndicator:
        self._started_at = time.monotonic()
        self._stop.clear()
        self.renderer.render_tool_call(
            "Mission Gateway",
            phase="called",
            summary=f"{self.message}（已用时 0.0 秒）",
        )
        if self.renderer.is_interactive:
            self._status = self.renderer.status(
                f"{self.message}（已用时 0.0 秒）",
                label="Working",
            )
            self._status.start()
            self._thread = threading.Thread(
                target=self._report_elapsed,
                name="fireclaw-mission-activity",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None
        if self._status is not None:
            self._status.stop()
            self._status = None
        elapsed = max(0.0, time.monotonic() - self._started_at)
        self.renderer.render_tool_call(
            "Mission Gateway",
            phase="ran",
            summary=(
                f"{self.message}（总用时 {elapsed:.1f} 秒；"
                + ("阶段完成）" if exc_type is None else "阶段异常）")
            ),
            failed=exc_type is not None,
        )
        return None

    def _report_elapsed(self) -> None:
        while not self._stop.wait(0.5):
            elapsed = max(0.0, time.monotonic() - self._started_at)
            status = self._status
            if status is not None:
                self.renderer.update_status(
                    status,
                    f"{self.message}（已用时 {elapsed:.1f} 秒）",
                    label="Working",
                )


class MissionPlanningStreamError(RuntimeError):
    """Terminal error delivered after a planning SSE response has started."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.error_code = str(
            payload.get("error_code") or "planning_stream_error"
        )
        super().__init__(
            str(payload.get("message") or "Mission planning stream failed.")
        )


class MissionPlanningMonitor:
    """Render one planning run as live structured activity plus a final row."""

    def __init__(
        self,
        message: str,
        *,
        style: _Style | None = None,
        renderer: MissionTerminalRenderer | None = None,
        heartbeat_render_seconds: float = 5.0,
        show_details: bool = False,
    ) -> None:
        self.message = message
        self.style = style or _GLOBAL_STYLE
        self.renderer = renderer or MissionTerminalRenderer(style=self.style)
        self.show_details = bool(show_details)
        self.heartbeat_render_seconds = max(
            1.0,
            float(heartbeat_render_seconds),
        )
        self.current_iteration: int | None = None
        self.max_iterations: int | None = None
        self.current_operation: str | None = None
        self.last_heartbeat_elapsed = 0.0
        self._status: Any | None = None

    def start(self) -> None:
        self.renderer.render_thought(
            f"{self.message}（已用时 0.0 秒）",
            label="Thought · Mission Agent · 规划已启动",
        )

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or event.get("event") or "")
        raw_payload = event.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        elapsed = self._elapsed(event)

        iteration = payload.get("iteration")
        if isinstance(iteration, int) and iteration > 0:
            self.current_iteration = iteration
        maximum = payload.get("max_iterations")
        if isinstance(maximum, int) and maximum > 0:
            self.max_iterations = maximum

        if event_type == "planning.started":
            return
        if event_type == "mission_agent.turn.started":
            self.current_operation = None
            self.last_heartbeat_elapsed = elapsed
            self.renderer.render_thought(
                f"正在推理下一步动作（总用时 {elapsed:.1f} 秒）",
                label="Thought · " + self._turn_header().removeprefix("🤖 "),
            )
            self._update_status(
                f"模型正在生成结构化决策（总用时 {elapsed:.1f} 秒）"
            )
            return
        if event_type == "planning.heartbeat":
            if (
                elapsed - self.last_heartbeat_elapsed
                < self.heartbeat_render_seconds
            ):
                return
            self.last_heartbeat_elapsed = elapsed
            if self.current_operation:
                activity = (
                    "正在执行："
                    + self._operation_label(self.current_operation)
                )
            else:
                activity = "模型仍在生成本回合的结构化决策"
            self._update_status(
                f"{activity}（总用时 {elapsed:.1f} 秒）"
            )
            if not self.show_details:
                return
            self.renderer.render_thought(
                f"{activity}（总用时 {elapsed:.1f} 秒）",
                label="Thought · " + self._turn_header().removeprefix("🤖 "),
            )
            return
        if event_type == "mission_agent.operation.started":
            self._stop_status()
            operation = str(payload.get("operation") or "")
            self.current_operation = operation or None
            summary = f"选择动作：{self._operation_label(operation)}"
            details: list[str] = []
            tool_name = payload.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                details.append(f"Tool：{tool_name}")
            decision_ms = payload.get("decision_duration_ms")
            if isinstance(decision_ms, (int, float)):
                details.append(
                    f"模型决策用时 {max(0.0, float(decision_ms)) / 1000:.1f} 秒"
                )
            self.renderer.render_tool_call(
                str(tool_name or operation or "mission_agent.operation"),
                phase="called",
                summary=summary,
                details=(self._turn_header(), *details),
            )
            return
        if event_type == "mission_agent.attempt.completed":
            operation = str(payload.get("operation") or "")
            outcome = str(payload.get("outcome") or "unknown")
            duration_ms = payload.get("duration_ms")
            duration = (
                max(0.0, float(duration_ms)) / 1000
                if isinstance(duration_ms, (int, float))
                else None
            )
            outcome_label = _MISSION_OUTCOME_LABELS.get(outcome, outcome)
            summary = (
                f"{self._operation_label(operation)} → {outcome_label}"
                + (f"（本回合 {duration:.1f} 秒）" if duration is not None else "")
            )
            lines = [summary]
            observation = payload.get("observation")
            if isinstance(observation, dict):
                kind = str(observation.get("kind") or "")
                data = observation.get("data")
                lines.append(
                    "观察："
                    + _summarize_observation(
                        kind,
                        data if isinstance(data, dict) else {},
                    )
                )
            reason_code = payload.get("reason_code")
            if isinstance(reason_code, str) and reason_code:
                lines.append(f"判定：{reason_code}")
            errors = payload.get("validation_errors")
            if isinstance(errors, list):
                for error in errors[:2]:
                    if isinstance(error, str) and error.strip():
                        lines.append(f"校验反馈：{error.strip()}")
            tool_name = payload.get("tool_name")
            self.renderer.render_tool_call(
                str(tool_name or operation or "mission_agent.operation"),
                phase=("explored" if outcome == "observed" else "ran"),
                summary=lines[0],
                details=(self._turn_header(), *lines[1:]),
                failed=outcome in {"rejected", "failed"},
            )
            self.current_operation = None
            return
        if event_type == "mission_agent.stage.completed":
            if not self.show_details:
                return
            stage = str(payload.get("stage") or "unknown")
            duration_ms = payload.get("duration_ms")
            duration = (
                max(0.0, float(duration_ms)) / 1000
                if isinstance(duration_ms, (int, float))
                and not isinstance(duration_ms, bool)
                else None
            )
            label = _planning_stage_label(stage)
            iteration_label = ""
            if isinstance(payload.get("iteration"), int):
                iteration_label = f" · 回合 {payload['iteration']}"
            lines = [
                f"{label}{iteration_label}："
                + (f"{duration:.3f} 秒" if duration is not None else "未知"),
                f"累计总用时 {elapsed:.1f} 秒",
            ]
            for key, text in (
                ("model", "模型"),
                ("input_tokens", "输入 token"),
                ("record_kind", "记录类型"),
                ("bound_target_count", "绑定目标数"),
                ("outcome", "结果"),
            ):
                value = payload.get(key)
                if value is not None:
                    lines.append(f"{text}：{value}")
            self.renderer.render_thought(
                lines[0],
                label="Timing · Mission Agent · 阶段计时",
                details=lines[1:],
            )
            return
        if event_type == "planning.result":
            self._stop_status()
            status = str(payload.get("status") or "unknown")
            status_label = {
                "preview_ready": "封存计划预览已生成",
                "clarification_required": "需要操作员补充信息",
                "escalated": "需要人工介入",
                "blocked": "规划已阻塞",
            }.get(status, status)
            lines = [
                f"结果：{status_label}",
                f"总用时 {elapsed:.1f} 秒",
            ]
            timing = payload.get("planning_timing")
            timing_entries = _planning_timing_entries(
                timing if isinstance(timing, dict) else None
            )
            if not self.show_details:
                compact_timing = _planning_timing_summary(
                    timing if isinstance(timing, dict) else None
                )
                if compact_timing:
                    lines.append(compact_timing)
            dropped = payload.get("dropped_progress_events")
            if isinstance(dropped, int) and dropped > 0:
                lines.append(f"提示：有 {dropped} 条中间显示事件因队列满而丢弃")
            self.renderer.render_final_response(
                "\n".join(lines),
                title="Mission Agent · 规划阶段完成",
                tone=(
                    "success"
                    if status == "preview_ready"
                    else "warning"
                    if status in {"clarification_required", "escalated"}
                    else "error"
                ),
            )
            if self.show_details and timing_entries:
                self.renderer.render_thought(
                    "阶段耗时明细",
                    label="Timing · Mission Agent · 阶段汇总",
                    details=[
                        f"{label}：{seconds:.3f} 秒"
                        for label, seconds in timing_entries
                    ],
                )
            return
        if event_type == "planning.error":
            self._stop_status()
            lines = [
                str(payload.get("message") or "Mission planning failed."),
                f"错误码：{payload.get('error_code') or 'planning_stream_error'}",
                f"总用时 {elapsed:.1f} 秒",
            ]
            self.renderer.render_final_response(
                "\n".join(lines),
                title="Mission Agent · 规划失败",
                tone="error",
            )

    def stop(self) -> None:
        self._stop_status()

    def _update_status(self, message: str) -> None:
        if not self.renderer.is_interactive:
            return
        if self._status is None:
            self._status = self.renderer.status(
                message,
                label="Thinking",
            )
            self._status.start()
            return
        self.renderer.update_status(
            self._status,
            message,
            label="Thinking",
        )

    def _stop_status(self) -> None:
        if self._status is None:
            return
        self._status.stop()
        self._status = None

    def _turn_header(self) -> str:
        if self.current_iteration is None:
            return "🤖 Mission Agent · 规划中"
        if self.max_iterations is None:
            return f"🤖 Mission Agent · 回合 {self.current_iteration}"
        return (
            f"🤖 Mission Agent · 回合 {self.current_iteration}"
            f"（上限 {self.max_iterations}）"
        )

    @staticmethod
    def _operation_label(operation: str) -> str:
        return _MISSION_OPERATION_LABELS.get(
            operation,
            f"⚙️ {operation or 'unknown'}",
        )

    @staticmethod
    def _elapsed(event: dict[str, Any]) -> float:
        value = event.get("elapsed_seconds")
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        return 0.0


def _consume_planning_stream(
    events: Any,
    *,
    monitor: MissionPlanningMonitor,
) -> dict[str, Any]:
    monitor.start()
    final_result: dict[str, Any] | None = None
    try:
        for raw_event in events:
            if not isinstance(raw_event, dict):
                continue
            monitor.handle_event(raw_event)
            event_type = str(
                raw_event.get("event_type") or raw_event.get("event") or ""
            )
            payload = raw_event.get("payload")
            normalized_payload = (
                dict(payload) if isinstance(payload, dict) else {}
            )
            if event_type == "planning.error":
                raise MissionPlanningStreamError(normalized_payload)
            if event_type == "planning.result":
                final_result = normalized_payload
        if final_result is None:
            raise MissionPlanningStreamError({
                "error_code": "planning_stream_incomplete",
                "message": (
                    "Mission planning event stream ended before a final result "
                    "was received."
                ),
            })
        return final_result
    finally:
        monitor.stop()


def _run_planning_phase(
    *,
    client: Any,
    stream_method: str,
    stream_arguments: tuple[Any, ...],
    fallback: Callable[[], dict[str, Any]],
    message: str,
    style: _Style,
    show_details: bool = False,
) -> tuple[dict[str, Any], bool]:
    stream = getattr(client, stream_method, None)
    if callable(stream):
        monitor = MissionPlanningMonitor(
            message,
            style=style,
            show_details=show_details,
        )
        try:
            return (
                _consume_planning_stream(
                    stream(*stream_arguments),
                    monitor=monitor,
                ),
                True,
            )
        except (MissionGatewayRequestError, MissionGatewayStreamUnsupported) as exc:
            if (
                isinstance(exc, MissionGatewayRequestError)
                and exc.status_code not in {404, 405}
            ):
                raise
            MissionTerminalRenderer(style=style).activity(
                "⚠️ Mission Agent · 实时流不可用",
                ["Gateway 不支持规划事件流，本轮回退为兼容模式。"],
                tone="yellow",
            )
    with LiveActivityIndicator(message, style=style):
        return fallback(), False


class LiveExecutionMonitor:
    def __init__(
        self,
        default_message: str = "机器人调度器正在初始化任务...",
        style: _Style | None = None,
        renderer: MissionTerminalRenderer | None = None,
        show_details: bool = False,
    ):
        self.message = default_message
        self.style = style or _GLOBAL_STYLE
        self.renderer = renderer or MissionTerminalRenderer(style=self.style)
        self.show_details = bool(show_details)
        self._lock = threading.RLock()
        self._started = False
        self._task_contexts: dict[str, dict[str, Any]] = {}
        self._last_secondary_signature: tuple[Any, ...] | None = None
        self._phase_starts: dict[tuple[str, str, str], float] = {}
        self._phase_durations: dict[str, float] = {}
        self._duration_event_ids: set[str] = set()
        self._first_event_at: float | None = None
        self._last_event_at: float | None = None
        self._timing_sources: set[str] = set()
        self._authorization_waiting_shown: set[str] = set()
        self._tool_calls_shown: set[tuple[str, str]] = set()

    @staticmethod
    def _phase_timing_key(
        phase: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, str]:
        operation = payload.get("tool_name") or payload.get("skill_name")
        return (
            phase,
            task_id or "mission",
            str(operation or "task"),
        )

    def _observe_phase(
        self,
        event_type: str,
        payload: dict[str, Any],
        task_id: str,
        observed_at: float,
        timing_source: str | None,
        event_id: str,
    ) -> float | None:
        """Record only explicit or correlated phase durations.

        Relayed Robot events may arrive in batches and phase intervals can
        overlap (authorization and resume, for example). Inferring mutually
        exclusive phases from adjacent events therefore over-counts wall time.
        This method uses producer timestamps plus known lifecycle start/end
        pairs, or an explicit backend duration when one exists.
        """

        if timing_source is not None:
            self._timing_sources.add(timing_source)
        if self._first_event_at is None or observed_at < self._first_event_at:
            self._first_event_at = observed_at
        if self._last_event_at is None or observed_at > self._last_event_at:
            self._last_event_at = observed_at

        semantic_event_type = _semantic_execution_event_type(
            event_type,
            payload,
        )
        phase = _phase_for_event(event_type, payload)
        start_phase = _PHASE_START_EVENTS.get(semantic_event_type)
        if start_phase is not None:
            start_key = self._phase_timing_key(
                start_phase,
                task_id,
                payload,
            )
            self._phase_starts.setdefault(start_key, observed_at)

        # Robot planning has an explicit context + decision duration rather
        # than a reliable begin/end pair in the relayed stream.
        if semantic_event_type == "robot_agent.decision":
            duration_key = event_id or (
                f"robot_agent.decision:{task_id}:{observed_at:.6f}"
            )
            if duration_key not in self._duration_event_ids:
                duration_ms = 0.0
                for key in ("context_duration_ms", "decision_duration_ms"):
                    value = payload.get(key)
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and value >= 0
                    ):
                        duration_ms += float(value)
                if duration_ms > 0:
                    self._phase_durations["robot_planning"] = (
                        self._phase_durations.get("robot_planning", 0.0)
                        + duration_ms / 1000.0
                    )
                self._duration_event_ids.add(duration_key)

        end_phases = _PHASE_END_EVENTS.get(semantic_event_type, ())
        ended_duration: float | None = None
        for end_phase in end_phases:
            end_key = self._phase_timing_key(
                end_phase,
                task_id,
                payload,
            )
            started_at = self._phase_starts.pop(end_key, None)
            explicit_duration = payload.get("elapsed_seconds")
            phase_duration: float | None = None
            if (
                end_phase == "execution"
                and isinstance(explicit_duration, (int, float))
                and not isinstance(explicit_duration, bool)
                and explicit_duration >= 0
            ):
                phase_duration = float(explicit_duration)
            elif started_at is not None and observed_at >= started_at:
                phase_duration = observed_at - started_at
            if phase_duration is not None:
                self._phase_durations[end_phase] = (
                    self._phase_durations.get(end_phase, 0.0)
                    + phase_duration
                )
                if phase == end_phase:
                    ended_duration = phase_duration

        if phase is None:
            return None
        if ended_duration is not None:
            return ended_duration
        active_key = self._phase_timing_key(phase, task_id, payload)
        started_at = self._phase_starts.get(active_key)
        if started_at is None or observed_at < started_at:
            return None
        return observed_at - started_at

    def phase_summary_lines(self) -> list[str]:
        """Return a compact, honest timing summary for the terminal block."""

        with self._lock:
            durations = dict(self._phase_durations)
            chain_duration = (
                self._last_event_at - self._first_event_at
                if self._last_event_at is not None
                and self._first_event_at is not None
                and self._last_event_at >= self._first_event_at
                else None
            )
            if not durations and chain_duration is None:
                return []
            ordered = [
                "planning",
                "confirmation",
                "robot_planning",
                "authorization",
                "queue",
                "recovery",
                "execution",
                "terminal",
            ]
            items = [
                f"{_EXECUTION_PHASE_LABELS[phase]} {durations[phase]:.1f} 秒"
                for phase in ordered
                if phase in durations
            ]
            source = "、".join(sorted(self._timing_sources)) or (
                "本地接收时间（事件未携带 timestamp）"
            )
            lines = [f"计时来源：{source}"]
            if items:
                lines.append(
                    "阶段耗时（显式起止，各项可能重叠）："
                    + "；".join(items)
                )
            if chain_duration is not None:
                lines.append(f"事件链路总时长：{chain_duration:.1f} 秒")
            return lines

    def render_phase_summary(self) -> None:
        lines = self.phase_summary_lines()
        if lines:
            self.renderer.render_thought(
                lines[0],
                label="Timing · Mission 阶段耗时",
                details=lines[1:],
            )

    def set_message(self, message: str) -> None:
        with self._lock:
            self.message = message

    def print_line(self, line: str, *, stream: Any | None = None) -> None:
        if stream is not None and stream is not self.renderer.stream:
            fallback = MissionTerminalRenderer(style=self.style, stream=stream)
            fallback.render_primary_status(
                "FireClaw 连接",
                [line],
                tone="warning",
            )
            return
        self.renderer.render_primary_status(
            "FireClaw 连接",
            [line],
            tone="warning",
        )

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self.renderer.render_thought(
            self.message,
            label="Thought · Mission 调度",
        )

    def stop(self) -> None:
        with self._lock:
            self._started = False

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type, payload = _normalized_event(event)
        task_id = str(event.get("task_id") or payload.get("task_id") or "")
        if task_id:
            cached_context = self._task_contexts.get(task_id, {})
            current_context = _task_context_from_payload(payload)
            context = {**cached_context, **current_context}
            if context:
                self._task_contexts[task_id] = context
            # Cached identity fills missing context only. The current event's
            # facts always win and are never written back into the cache.
            payload = {**context, **payload}
            event = {**event, "payload": payload}

        observed_at, timing_source = _event_timestamp_seconds(event, payload)
        if observed_at is None:
            # A monotonic fallback keeps local/fake clients observable without
            # pretending that the value is a backend timestamp.  Real gateway
            # events always carry an ISO timestamp.
            observed_at = time.monotonic()
            timing_source = "本地接收时间（事件未携带 timestamp）"
        previous_event_at = self._last_event_at
        phase = _phase_for_event(event_type, payload)
        phase_elapsed = self._observe_phase(
            event_type,
            payload,
            task_id,
            observed_at,
            timing_source,
            str(
                payload.get("source_event_id")
                or event.get("event_id")
                or ""
            ),
        )
        if self.show_details:
            payload["_tui_show_details"] = True
            if phase is not None:
                payload["_tui_phase"] = phase
            if phase_elapsed is not None:
                payload["_tui_phase_elapsed_seconds"] = phase_elapsed
            if (
                previous_event_at is not None
                and observed_at >= previous_event_at
                and observed_at - previous_event_at >= 0.5
            ):
                payload["_tui_event_gap_seconds"] = (
                    observed_at - previous_event_at
                )
            elif previous_event_at is not None and observed_at < previous_event_at:
                payload["_tui_event_out_of_order"] = True
        event = {**event, "payload": payload}
        semantic_event_type = _semantic_execution_event_type(
            event_type,
            payload,
        )

        if not self.show_details and event_type in _INTERNAL_EXECUTION_EVENTS:
            return

        if not self.show_details and event_type in _DEFAULT_FOLDED_EXECUTION_EVENTS:
            return

        if not self.show_details and event_type == "authorization.requested":
            authorization_key = task_id or str(
                payload.get("request_id") or "mission"
            )
            if authorization_key not in self._authorization_waiting_shown:
                self._authorization_waiting_shown.add(authorization_key)
                step_index = payload.get("plan_step_index")
                step_total = payload.get("plan_step_total")
                step = ""
                if isinstance(step_index, int) and not isinstance(
                    step_index,
                    bool,
                ):
                    step = f"计划步骤 {step_index}"
                    if isinstance(step_total, int) and not isinstance(
                        step_total,
                        bool,
                    ):
                        step += f"/{step_total}"
                self.renderer.render_thought(
                    "等待执行授权：正在校验物理动作…",
                    label=(
                        f"Safety · {step}"
                        if step
                        else "Safety · 执行授权"
                    ),
                )
            return

        if (
            not self.show_details
            and event_type == "task.authorized_plan_resumed"
        ):
            details: list[str] = []
            step_index = payload.get("plan_step_index")
            step_total = payload.get("plan_step_total")
            step_command = _sanitize_terminal_text(
                payload.get("plan_step_command")
            )
            if isinstance(step_index, int) and not isinstance(step_index, bool):
                step = f"计划步骤 {step_index}"
                if isinstance(step_total, int) and not isinstance(
                    step_total,
                    bool,
                ):
                    step += f"/{step_total}"
                if step_command:
                    step += f" · {step_command}"
                details.append(step)
            resume_flags: list[str] = []
            if payload.get("snapshot_reused") is True:
                resume_flags.append("复用原快照")
            if payload.get("robot_agent_reinvoked") is False:
                resume_flags.append("Robot Agent 未重跑")
            if payload.get("llm_reinvoked") is False:
                resume_flags.append("LLM 未重跑")
            if resume_flags:
                details.append("；".join(resume_flags))
            self.renderer.render_primary_status(
                "🛡️ 执行授权通过",
                details,
                tone="success",
            )
            return

        if not self.show_details and semantic_event_type in {
            "robot_agent.decision",
            "skill.started",
        }:
            tool_name = _sanitize_terminal_text(
                payload.get("tool_name") or payload.get("skill_name")
            )
            tool_key = (task_id, tool_name)
            if (
                semantic_event_type == "skill.started"
                and tool_name
                and tool_key in self._tool_calls_shown
            ):
                return
            if tool_name:
                self._tool_calls_shown.add(tool_key)

        if not self.show_details and event_type in {"action.feedback", "ros.log"}:
            signature = (
                event_type,
                semantic_event_type,
                task_id,
                payload.get("plan_step_index"),
                payload.get("robot_agent_iteration"),
                payload.get("tool_name"),
                payload.get("skill_name"),
                payload.get("severity"),
                payload.get("node"),
                _sanitize_terminal_text(payload.get("message")),
            )
            if signature == self._last_secondary_signature:
                return
            self._last_secondary_signature = signature
        else:
            self._last_secondary_signature = None

        header, lines, tone = _format_event_block(event)
        if _is_secondary_execution_event(event_type):
            tool_name = _sanitize_terminal_text(
                payload.get("tool_name") or payload.get("skill_name")
            )
            self.renderer.render_tool_call(
                event_type if event_type == "ros.log" else tool_name or event_type,
                phase=_tool_phase_for_execution_event(event_type, payload),
                summary=lines[-1] if lines else header,
                details=(header, *lines[:-1]),
                failed=(
                    tone == "red"
                    or semantic_event_type.endswith(
                        ("failed", "lost", "timed_out")
                    )
                ),
            )
        else:
            self.renderer.render_primary_status(
                header,
                lines,
                tone=MissionTerminalRenderer._rich_tone(tone),
            )

        message = payload.get("message")
        robot_id = str(event.get("robot_id") or payload.get("robot_id") or "")
        if event_type == "action.feedback" and isinstance(message, str) and message.strip():
            self.set_message(f"[{robot_id or 'Robot'}] {message.strip()}")
        elif event_type in {"task.received", "task.planned"}:
            self.set_message(f"[{robot_id or 'Robot'}] Robot Agent 正在规划并执行物理动作")
        elif event_type in {
            "mission.running",
            "mission.sealed_execution_started",
            "mission.sealed_plan_execution_started",
        }:
            self.set_message("正在执行已确认的封存计划")


def _is_secondary_execution_event(event_type: str) -> bool:
    """Keep implementation progress visible without competing with outcomes."""

    return event_type in {
        "action.feedback",
        "ros.log",
        "task.received",
        "task.planned",
        "task.running",
        "subtask.submitted",
        "subtask.status_changed",
        "skill.started",
        "skill.succeeded",
        "skill.failed",
    } or event_type.startswith("robot_agent.")


def _tool_phase_for_execution_event(
    event_type: str,
    payload: dict[str, Any],
) -> str:
    semantic_event_type = _semantic_execution_event_type(event_type, payload)
    if semantic_event_type in {
        "task.received",
        "task.planned",
        "skill.started",
        "robot_agent.deliberation_started",
        "robot_agent.decision",
    }:
        return "called"
    if semantic_event_type in {
        "action.feedback",
        "ros.log",
        "robot_agent.observation",
    }:
        return "explored"
    return "ran"


_EVENT_LABELS = {
    "mission.plan_confirmed": "封存计划已由操作员确认",
    "mission.run_accepted": "任务已进入队列",
    "mission.sealed_execution_started": "开始执行已确认的封存计划",
    "mission.sealed_plan_execution_started": "开始执行已确认的封存计划",
    "mission.sealed_plan_loaded": "已载入封存计划",
    "mission.report_generation_started": "物理任务已完成；最终报告后台生成中",
    "mission.report_ready": "最终报告已生成",
    "mission.final_report_ready": "最终报告已生成",
    "mission.planning": "正在规划任务",
    "mission.running": "任务开始执行",
    "mission.cancel_requested": "已请求取消，等待机器人确认终态",
    "confirmation.pending": "等待安全确认",
    "confirmation.confirmed": "安全确认已完成",
    "authorization.requested": "等待执行授权",
    "authorization.approved": "执行授权已批准",
    "authorization.denied": "执行授权被拒绝",
    "authorization.expired": "执行授权已过期",
    "authorization.cancelled": "执行授权已取消",
    "task.received": "机器人已接收子任务",
    "task.planned": "Robot Agent 已完成子任务规划",
    "task.running": "Robot Agent 已开始执行子任务",
    "task.awaiting_confirmation": "子任务等待执行授权",
    "task.cancel_requested": "机器人取消请求已发送，尚未证明停止",
    "task.resume_scheduled": "已排入授权恢复队列",
    "task.resume_started": "已开始恢复授权任务",
    "task.authorized_plan_resumed": "已恢复封存计划（复用原快照）",
    "task.resume_rejected": "授权恢复被拒绝",
    "task.resume_failed": "授权恢复失败",
    "task.completed": "机器人子任务已完成",
    "task.blocked": "机器人子任务被安全策略阻塞",
    "task.escalated": "机器人子任务需要人工介入",
    "task.failed": "机器人子任务失败",
    "task.timed_out": "机器人子任务超时",
    "task.cancelled": "机器人已确认子任务取消",
    "task.lost": "机器人子任务失联；不能据此推断已经停止",
    "skill.started": "物理工具开始执行",
    "skill.succeeded": "物理工具执行成功",
    "skill.failed": "物理工具执行失败",
    "subtask.submitted": "子任务已提交到 Robot Agent",
    "subtask.status_changed": "Robot Agent 子任务状态已变化",
    "robot_agent.deliberation_started": "Robot Agent 开始推理",
    "robot_agent.deliberation_finished": "Robot Agent 推理完成",
    "robot_agent.decision": "Robot Agent 已决定下一步动作",
    "robot_agent.observation": "Robot Agent 已获得执行观察",
    "robot_agent.pending_operation_reconciled": "已对账待恢复操作",
    "robot_agent.pending_operation_unresolved": "待恢复操作无法对账",
    "mission.completed": "任务已完成",
    "mission.failed": "任务失败",
    "mission.timed_out": "任务超时",
    "mission.cancelled": "任务已取消",
    "mission.blocked": "任务被阻塞",
    "mission.escalated": "任务等待人工介入",
    "mission.lost": "任务状态失联",
}


def _normalized_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    event_type = str(event.get("event_type") or event.get("type") or "unknown")
    raw_payload = event.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    details = event.get("details")
    if isinstance(details, dict):
        for key, value in details.items():
            payload.setdefault(key, value)
    return event_type, payload


def _event_context_lines(
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    include_agent_round: bool,
) -> list[str]:
    lines: list[str] = []
    show_details = payload.get("_tui_show_details") is True
    step_index = payload.get("plan_step_index")
    step_total = payload.get("plan_step_total")
    step_command = _sanitize_terminal_text(payload.get("plan_step_command"))
    node_id = _sanitize_terminal_text(payload.get("node_id"))
    if isinstance(step_index, int) and not isinstance(step_index, bool):
        step = f"计划步骤 {step_index}"
        if isinstance(step_total, int) and not isinstance(step_total, bool):
            step += f"/{step_total}"
        if step_command:
            step += f" · {step_command}"
        lines.append(step)
    elif node_id or step_command:
        step = f"计划节点 {node_id}" if node_id else "计划步骤"
        if step_command:
            step += f" · {step_command}"
        lines.append(step)

    robot_id = _sanitize_terminal_text(
        event.get("robot_id") or payload.get("robot_id")
    )
    task_id = _sanitize_terminal_text(event.get("task_id") or payload.get("task_id"))
    if show_details and robot_id:
        lines.append(f"Robot {robot_id}")
    execution_identity: list[str] = []
    if task_id:
        execution_identity.append(f"Task {_compact_identifier(task_id)}")
    if node_id:
        execution_identity.append(f"Node {node_id}")
    if show_details and execution_identity:
        lines.append(" · ".join(execution_identity))

    if include_agent_round and show_details:
        iteration = payload.get("robot_agent_iteration", payload.get("iteration"))
        tool_name = _sanitize_terminal_text(payload.get("tool_name"))
        round_parts: list[str] = []
        if isinstance(iteration, int) and not isinstance(iteration, bool):
            round_parts.append(f"Agent 回合 {iteration}")
        if tool_name:
            round_parts.append(f"Tool {tool_name}")
        if round_parts:
            lines.append(" · ".join(round_parts))
    return lines


def _pose_summary(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    coordinates: list[str] = []
    for key in ("x", "y", "z", "yaw"):
        coordinate = value.get(key)
        if (
            isinstance(coordinate, (int, float))
            and not isinstance(coordinate, bool)
        ):
            coordinates.append(f"{key}={float(coordinate):.3f}")
    if not coordinates:
        return None
    frame_id = _sanitize_terminal_text(value.get("frame_id") or "map")
    return f"{frame_id} ({', '.join(coordinates)})"


def _skill_success_lines(payload: dict[str, Any]) -> list[str]:
    """Build a deterministic Chinese Tool-success summary."""

    lines: list[str] = []
    target_pose = payload.get("target_pose")
    final_pose = payload.get("final_pose")
    target_summary = _pose_summary(target_pose)
    final_summary = _pose_summary(final_pose)
    if target_summary is not None:
        lines.append(f"目标位姿：{target_summary}")
    if final_summary is not None:
        lines.append(f"最终位姿：{final_summary}")

    planar_error: float | None = None
    if isinstance(target_pose, dict) and isinstance(final_pose, dict):
        target_x = target_pose.get("x")
        target_y = target_pose.get("y")
        final_x = final_pose.get("x")
        final_y = final_pose.get("y")
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (target_x, target_y, final_x, final_y)
        ):
            planar_error = (
                (float(final_x) - float(target_x)) ** 2
                + (float(final_y) - float(target_y)) ** 2
            ) ** 0.5
            lines.append(f"平面误差：{planar_error:.3f} m")

    elapsed = payload.get("elapsed_seconds")
    elapsed_text = (
        f"{float(elapsed):.1f} 秒"
        if isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and elapsed >= 0
        else None
    )
    if elapsed_text is not None:
        lines.append(f"物理动作耗时：{elapsed_text}")

    skill_name = _sanitize_terminal_text(
        payload.get("skill_name") or payload.get("tool_name")
    )
    goal_reached = payload.get("goal_reached") is True
    if skill_name in {"navigate", "navigate_to_point", "navigate_to_pose"}:
        summary = "导航完成，已到达目标"
    else:
        summary = f"{skill_name or '物理工具'} 执行成功"
    evidence: list[str] = []
    if planar_error is not None:
        evidence.append(f"平面误差 {planar_error:.3f} m")
    if elapsed_text is not None:
        evidence.append(f"耗时 {elapsed_text}")
    if goal_reached and planar_error is None:
        evidence.append("运行时确认目标已到达")
    if evidence:
        summary += "（" + "；".join(evidence) + "）"
    lines.append(summary)
    return lines


def _event_observability_lines(
    event: dict[str, Any],
    payload: dict[str, Any],
) -> list[str]:
    """Render structured timing/authorization evidence without raw JSON dumps."""

    lines: list[str] = []
    show_details = payload.get("_tui_show_details") is True
    event_type = str(event.get("event_type") or event.get("type") or "")
    source_event_type = payload.get("source_event_type")
    phase = payload.get("_tui_phase")
    if (
        show_details
        and isinstance(phase, str)
        and phase in _EXECUTION_PHASE_LABELS
    ):
        lines.append(f"阶段：{_EXECUTION_PHASE_LABELS[phase]}")

    phase_elapsed = _format_seconds(payload.get("_tui_phase_elapsed_seconds"))
    if show_details and phase_elapsed is not None:
        lines.append(f"阶段已用时：{phase_elapsed}")

    if show_details and isinstance(source_event_type, str) and source_event_type and (
        source_event_type != event_type
    ):
        lines.append(f"事件来源：{source_event_type}")

    report_status = payload.get("report_status")
    if isinstance(report_status, str) and report_status:
        report_label = {
            "pending": "后台生成中",
            "ready": "已生成",
            "failed": "生成失败，使用确定性兜底",
            "completed": "已生成",
            "succeeded": "已生成",
            "cancelled": "已生成",
            "blocked": "已生成",
            "escalated": "已生成",
            "timed_out": "已生成",
            "lost": "已生成",
        }.get(report_status, report_status)
        lines.append(f"最终报告：{report_label}")
    if payload.get("report_pending") is True:
        lines.append("物理终态已确定；报告生成不阻塞任务完成")

    report_duration = payload.get("report_duration_ms")
    if isinstance(report_duration, (int, float)) and not isinstance(
        report_duration,
        bool,
    ) and report_duration >= 0:
        lines.append(f"最终报告生成耗时：{float(report_duration) / 1000:.1f} 秒")
    report_error = payload.get("report_error")
    if isinstance(report_error, str) and report_error.strip():
        lines.append(f"报告生成诊断：{_sanitize_terminal_text(report_error)}")

    # Keep model/adapter timing separate from the event-chain timing above.
    decision_duration = payload.get("decision_duration_ms")
    if decision_duration is None:
        decision_duration = payload.get("llm_duration_ms")
    if decision_duration is None and source_event_type == "robot_agent.decision":
        decision_duration = payload.get("duration_ms")
    if show_details and isinstance(decision_duration, (int, float)) and not isinstance(
        decision_duration,
        bool,
    ) and decision_duration >= 0:
        lines.append(f"Agent 决策耗时：{float(decision_duration) / 1000:.1f} 秒")

    context_duration = payload.get("context_duration_ms")
    if show_details and isinstance(context_duration, (int, float)) and not isinstance(
        context_duration,
        bool,
    ) and context_duration >= 0:
        lines.append(f"上下文组装耗时：{float(context_duration) / 1000:.1f} 秒")

    event_gap = _format_seconds(payload.get("_tui_event_gap_seconds"))
    if show_details and event_gap is not None:
        lines.append(f"相邻事件间隔：{event_gap}")
    if show_details and payload.get("_tui_event_out_of_order") is True:
        lines.append("事件时间早于已显示事件；未按相邻间隔累计")

    backend_duration = payload.get("backend_duration_ms")
    if show_details and isinstance(backend_duration, (int, float)) and not isinstance(
        backend_duration,
        bool,
    ) and backend_duration >= 0:
        lines.append(f"后端处理耗时：{float(backend_duration) / 1000:.1f} 秒")

    authorization = payload.get("authorization")
    authorization = authorization if isinstance(authorization, dict) else {}
    execution_authorization = payload.get("execution_authorization")
    execution_authorization = (
        execution_authorization
        if isinstance(execution_authorization, dict)
        else {}
    )
    request_id = payload.get("request_id") or authorization.get("request_id")
    authorization_id = (
        payload.get("authorization_id")
        or execution_authorization.get("authorization_id")
        or authorization.get("authorization_id")
    )
    if show_details and isinstance(request_id, str) and request_id:
        lines.append(f"授权请求：{_compact_identifier(request_id)}")
    if show_details and isinstance(authorization_id, str) and authorization_id:
        lines.append(f"执行授权：{_compact_identifier(authorization_id)}")

    pending_operation_id = payload.get("pending_operation_id")
    if show_details and isinstance(pending_operation_id, str) and pending_operation_id:
        lines.append(f"待恢复操作：{_compact_identifier(pending_operation_id)}")

    resume = payload.get("authorization_resume")
    resume = resume if isinstance(resume, dict) else payload
    resume_flags: list[str] = []
    if isinstance(resume.get("snapshot_reused"), bool):
        resume_flags.append(
            "复用快照" if resume["snapshot_reused"] else "未复用快照"
        )
    if isinstance(resume.get("robot_agent_reinvoked"), bool):
        resume_flags.append(
            "Robot Agent 已重跑"
            if resume["robot_agent_reinvoked"]
            else "Robot Agent 未重跑"
        )
    if isinstance(resume.get("llm_reinvoked"), bool):
        resume_flags.append(
            "LLM 已重跑" if resume["llm_reinvoked"] else "LLM 未重跑"
        )
    if show_details and resume_flags and (
        event_type.startswith("task.resume")
        or event_type == "task.authorized_plan_resumed"
        or "authorization_resume" in payload
    ):
        lines.append(f"恢复证据：{'；'.join(resume_flags)}")
    return lines


def _format_event_block(
    event: dict[str, Any],
) -> tuple[str, list[str], str]:
    event_type, payload = _normalized_event(event)
    robot_id = _sanitize_terminal_text(
        event.get("robot_id") or payload.get("robot_id")
    )
    mission_id = _sanitize_terminal_text(
        event.get("mission_id") or payload.get("mission_id")
    )

    if event_type == "ros.log":
        severity = _sanitize_terminal_text(payload.get("severity") or "WARN").upper()
        node = _sanitize_terminal_text(payload.get("node") or "unknown")
        icon = "🛑" if severity in {"ERROR", "FATAL"} else "⚠️"
        tone = "red" if severity in {"ERROR", "FATAL"} else "yellow"
        lines = _event_context_lines(
            event,
            payload,
            include_agent_round=False,
        )
        lines.extend(_event_observability_lines(event, payload))
        message = _sanitize_terminal_text(payload.get("message")) or "ROS emitted an empty log record."
        lines.extend(message.splitlines())
        dropped_before = payload.get("dropped_before")
        if isinstance(dropped_before, int) and dropped_before > 0:
            lines.append(f"采集队列此前丢弃 {dropped_before} 条日志；请检查 Robot Gateway 负载。")
        return f"{icon} ROS {severity} · {node}", lines, tone

    if event_type == "action.feedback":
        header = "🤖 Robot Agent"
        if robot_id:
            header += f" · {robot_id}"
        lines = _event_context_lines(
            event,
            payload,
            include_agent_round=True,
        )
        lines.extend(_event_observability_lines(event, payload))
        message = _sanitize_terminal_text(payload.get("message"))
        progress = payload.get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            lines.append(f"进度：{progress:g}")
        lines.append(message or "机器人正在执行")
        return header, lines, "blue"

    if event_type == "skill.succeeded":
        header = "🤖 Robot Agent"
        if robot_id:
            header += f" · {robot_id}"
        lines = _event_context_lines(
            event,
            payload,
            include_agent_round=False,
        )
        lines.extend(_event_observability_lines(event, payload))
        lines.extend(_skill_success_lines(payload))
        return header, lines, "green"

    is_mission_event = event_type.startswith("mission.")
    header = "🧭 Mission Agent" if is_mission_event else "🤖 Robot Agent"
    if is_mission_event and mission_id:
        header += f" · {_compact_identifier(mission_id)}"
    elif robot_id:
        header += f" · {robot_id}"
    lines = _event_context_lines(
        event,
        payload,
        include_agent_round=False,
    )
    lines.extend(_event_observability_lines(event, payload))
    label = _EVENT_LABELS.get(event_type)
    status = payload.get("status") or event.get("status")
    previous_status = event.get("previous_status") or payload.get("previous_status")
    if label is None:
        label = event_type
    if event_type in {"subtask.submitted", "subtask.status_changed"} and status:
        if previous_status:
            label += f"：{previous_status} → {status}"
        else:
            label += f"：{status}"
    reason = payload.get("reason_code") or payload.get("message")
    if event_type == "task.completed" and reason in {
        "任务已完成。",
        "Task completed.",
    }:
        reason = None
    if isinstance(reason, str) and reason.strip() and reason.strip() != label:
        label += f"：{reason.strip()}"
    elif event_type not in _EVENT_LABELS and status:
        label += f"：{status}"
    lines.append(label)
    if event_type in {"task.completed", "mission.completed"}:
        tone = "green"
    elif not is_mission_event and not event_type.startswith(("task.", "subtask.")):
        tone = "gray"
    elif event_type.endswith(("failed", "blocked", "lost", "timed_out")):
        tone = "red"
    elif event_type.endswith(("escalated", "cancel_requested")):
        tone = "yellow"
    else:
        tone = "cyan" if is_mission_event else "blue"
    return header, lines, tone


def parse_builtin_command(input_text: str) -> tuple[str, str | None]:
    text = input_text.strip()
    if not text:
        return "help", None
    if text in {"help", "quit", "exit", "status"}:
        return text, None
    if text.startswith("cancel ") or text == "cancel":
        mission_id = text[len("cancel "):].strip() if text.startswith("cancel ") else None
        return "cancel", mission_id
    if (
        text.startswith("follow ")
        or text.startswith("attach ")
        or text.startswith("watch ")
        or text in {"follow", "attach", "watch"}
    ):
        mission_id = text.split(maxsplit=1)[1].strip() if " " in text else None
        return "follow", mission_id
    return "submit", text


def _summarize_observation(kind: str, data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return str(data)
    if kind == "robot_state":
        robot = data.get("robot") or {}
        robot_id = robot.get("robot_id", "robot")
        pose = robot.get("pose")
        if isinstance(pose, dict) and "x" in pose and "y" in pose:
            yaw = pose.get("yaw", 0.0)
            return f"获取到 {robot_id} 实测位姿 (x={pose['x']}, y={pose['y']}, yaw={yaw})"
        return f"获取到 {robot_id} 状态: online={robot.get('online')}, sensors={robot.get('available_sensors')}"
    if kind == "fleet_state":
        robots = data.get("robots") or []
        return f"获取到 Fleet 状态 (共 {len(robots)} 台机器人在线)"
    return f"{kind}: {str(data)[:80]}"


def _display_thinking_trail(data: dict[str, Any], style: _Style | None = None) -> None:
    style = style or _GLOBAL_STYLE
    attempts = data.get("deliberation_attempts") or []
    observations = data.get("deliberation_observations") or []
    if not isinstance(attempts, list) or not attempts:
        return

    obs_by_iter: dict[int, list[dict[str, Any]]] = {}
    if isinstance(observations, list):
        for obs in observations:
            if isinstance(obs, dict):
                it = obs.get("iteration", 0)
                obs_by_iter.setdefault(it, []).append(obs)

    op_labels = {
        "inspect_state": "🔍 探查状态",
        "propose_plan": "🛡️ 制定并校验计划",
        "request_clarification": "❓ 发起操作员澄清",
        "request_observation": "👁️ 请求主动观测",
        "execute_agent_tool": "⚡ 执行工具",
        "escalate": "⚠️ 升级人工介入",
    }

    trail_lines: list[str] = []
    total_attempts = len(attempts)
    for i, att in enumerate(attempts, start=1):
        if not isinstance(att, dict):
            continue
        it_num = att.get("iteration", i)
        op = att.get("operation", "think")
        op_name = op_labels.get(op, f"⚙️ {op}")
        reason = att.get("reason_code")
        dur_ms = att.get("duration_ms")
        dur_text = f" ({dur_ms:.0f}ms)" if isinstance(dur_ms, (int, float)) and dur_ms > 0 else ""

        step_desc = f"[{i}/{total_attempts}] {op_name}{dur_text}"
        if reason and reason not in {"None", "null"}:
            step_desc += f" -> {reason}"
        trail_lines.append(step_desc)

        if it_num in obs_by_iter and obs_by_iter[it_num]:
            obs_list = obs_by_iter[it_num]
            for obs in obs_list:
                kind = str(obs.get("kind") or "")
                obs_data = obs.get("data") or {}
                obs_summary = _summarize_observation(kind, obs_data if isinstance(obs_data, dict) else {})
                trail_lines.append(f"  👁️ 观察：{obs_summary}")
    MissionTerminalRenderer(style=style).render_thought(
        f"共 {total_attempts} 个结构化决策回合",
        label="Thought · Mission Agent 思考轨迹",
        details=trail_lines,
    )


def display_event(
    event: dict[str, Any],
    style: _Style | None = None,
    *,
    renderer: MissionTerminalRenderer | None = None,
) -> None:
    active_renderer = renderer or MissionTerminalRenderer(
        style=style or _GLOBAL_STYLE
    )
    event_type, payload = _normalized_event(event)
    header, lines, tone = _format_event_block(event)
    if _is_secondary_execution_event(event_type):
        active_renderer.render_tool_call(
            (
                event_type
                if event_type == "ros.log"
                else _sanitize_terminal_text(
                    payload.get("tool_name") or payload.get("skill_name")
                )
                or event_type
            ),
            phase=_tool_phase_for_execution_event(event_type, payload),
            summary=lines[-1] if lines else header,
            details=(header, *lines[:-1]),
            failed=tone == "red",
        )
        return
    active_renderer.render_primary_status(
        header,
        lines,
        tone=MissionTerminalRenderer._rich_tone(tone),
    )


_console_input: ConsoleInput | None = None


def _get_console_input() -> ConsoleInput:
    global _console_input
    if _console_input is None:
        _console_input = ConsoleInput(
            history_path=Path("data/mission/.cli-history"),
        )
    return _console_input


def _prompt(text: str) -> str:
    return _get_console_input().prompt(text)


def _operator_prompt(prompt_text: str, *, label: str = "🧑 操作员") -> str:
    print(_GLOBAL_STYLE.bold(_GLOBAL_STYLE.blue(label)))
    return _prompt(_GLOBAL_STYLE.bold(_GLOBAL_STYLE.blue(prompt_text)))


def _save_console_history() -> None:
    if _console_input is not None:
        _console_input.save()


def run_interactive(
    server_url: str = "http://127.0.0.1:8766",
    timeout: float = 75.0,
    api_token: str | None = None,
    tls: GatewayTlsClientConfig | None = None,
    show_details: bool = False,
) -> None:
    style = _GLOBAL_STYLE
    client = MissionGatewayClient(
        server_url,
        timeout=timeout,
        api_token=resolve_gateway_api_token(api_token),
        tls=tls,
    )
    print()
    print(style.bold(style.cyan("═" * 55)))
    print(style.bold(style.cyan("  🔥 FireClaw 具身智能任务控制台")))
    print(style.gray(f"  Mission Gateway：{server_url}"))
    print(style.gray("  输入 help 查看命令；输入 quit 退出。"))
    print(style.bold(style.cyan("═" * 55)))
    print()
    last_mission_id: str | None = None
    while True:
        try:
            raw = _operator_prompt("  › ")
            print()
        except (EOFError, KeyboardInterrupt):
            print()
            _save_console_history()
            return
        action, arg = parse_builtin_command(raw)
        if action in {"quit", "exit"}:
            _save_console_history()
            return
        if action == "help":
            print(style.gray("命令：help、status、follow [mission_id]、cancel [mission_id]、quit，或直接输入自然语言任务。"))
            continue
        if action == "status":
            try:
                _display_readiness(client.get_readiness(), style=style)
                if last_mission_id:
                    print(style.cyan(f"最近活跃任务：{last_mission_id}（输入 follow 重新接入监控）"))
            except MissionGatewayRequestError as exc:
                _display_gateway_request_error("状态查询失败", exc)
            continue
        if action == "follow":
            target_id = arg or last_mission_id
            if not target_id:
                print("当前没有已知任务。用法：follow <mission_id>")
                continue
            print(style.cyan(f"正在重新接入任务监控：{target_id}...\n"))
            try:
                _follow_mission_stream(
                    client,
                    target_id,
                    style=style,
                    show_details=show_details,
                )
            except KeyboardInterrupt:
                print(style.yellow("\n已停止本地观察；远端任务仍在该机器人上自主安全执行。"))
                print(style.gray(f"💡 提示：输入 follow 继续追踪该任务进度，或输入 cancel 取消该任务 ({target_id})。"))
            continue
        if action == "cancel":
            target_id = arg or last_mission_id
            if not target_id:
                print("请输入要取消的任务 ID。用法：cancel <mission_id>")
                continue
            try:
                result = client.cancel_mission(target_id)
            except MissionGatewayRequestError as exc:
                _display_gateway_request_error("取消请求失败", exc)
                continue
            status = str(result.get("status") or "unknown")
            if status == "cancel_requested":
                print("取消请求已发送；在机器人确认终态前，不代表已经停止。")
            elif status in {"cancelled", "already_terminal"}:
                print(f"取消结果：{status}")
            else:
                print(f"取消未完成：{result.get('message') or status}")
            continue
        if action == "submit" and arg:
            try:
                mid = _submit_and_follow(
                    client,
                    arg,
                    style=style,
                    show_details=show_details,
                )
                if mid:
                    last_mission_id = mid
            except KeyboardInterrupt:
                print(style.yellow("\n已停止本地观察；远端任务仍在该机器人上自主安全执行。"))
                if last_mission_id:
                    print(style.gray(f"💡 提示：输入 follow 继续追踪该任务进度，或输入 cancel 取消该任务 ({last_mission_id})。"))


def _follow_mission_stream(
    client: MissionGatewayClient,
    mission_id: str,
    *,
    style: _Style | None = None,
    sleep_fn=time.sleep,
    reconnect_initial_seconds: float = 0.25,
    reconnect_max_seconds: float = 4.0,
    max_reconnect_attempts: int | None = None,
    show_details: bool = False,
) -> None:
    style = style or _GLOBAL_STYLE
    last_sequence = 0
    seen_event_ids: set[str] = set()
    reconnect_attempts = 0
    reconnect_delay = max(0.01, reconnect_initial_seconds)
    disconnected = False

    monitor = LiveExecutionMonitor(
        default_message="机器人调度器正在初始化任务...",
        style=style,
        show_details=show_details,
    )
    monitor.start()
    try:
        while True:
            try:
                for event in client.stream_mission_events(
                    mission_id,
                    after_sequence=last_sequence,
                ):
                    if not isinstance(event, dict):
                        continue
                    event_id = event.get("event_id")
                    if isinstance(event_id, str) and event_id in seen_event_ids:
                        continue
                    sequence = event.get("sequence")
                    if isinstance(sequence, int) and sequence > last_sequence:
                        last_sequence = sequence
                    if isinstance(event_id, str):
                        seen_event_ids.add(event_id)
                    if disconnected:
                        monitor.print_line(f"[连接] 事件流已恢复，从 cursor={last_sequence} 继续。")
                        disconnected = False
                    reconnect_attempts = 0
                    reconnect_delay = max(0.01, reconnect_initial_seconds)

                    monitor.handle_event(event)

                    event_type = str(event.get("event_type") or event.get("type") or "")
                    terminal = _MISSION_TERMINAL_EVENTS.get(event_type)
                    if terminal is not None:
                        monitor.render_phase_summary()
                        monitor.stop()
                        label = _TERMINAL_STATUS_LABELS.get(terminal, terminal)
                        monitor.renderer.render_final_response(
                            f"[完成] {label}（{terminal}）",
                            tone=(
                                "success"
                                if terminal in {"succeeded", "completed"}
                                else "error"
                            ),
                        )
                        if terminal not in {"succeeded", "completed"}:
                            _print_terminal_reason(client, mission_id)
                        return

                snapshot = _mission_status_snapshot(client, mission_id)
                terminal = _terminal_trace_status(snapshot)
                if terminal is not None:
                    monitor.render_phase_summary()
                    monitor.stop()
                    label = _TERMINAL_STATUS_LABELS.get(terminal, terminal)
                    monitor.renderer.render_final_response(
                        f"[完成] {label}（{terminal}）",
                        tone=(
                            "success"
                            if terminal in {"succeeded", "completed"}
                            else "error"
                        ),
                    )
                    if terminal not in {"succeeded", "completed"}:
                        _print_terminal_reason(client, mission_id)
                    return
            except KeyboardInterrupt:
                raise
            except Exception:
                pass

            reconnect_attempts += 1
            if max_reconnect_attempts is not None and reconnect_attempts > max_reconnect_attempts:
                monitor.stop()
                print("[连接] 已达到本地重连上限；远端任务状态未被修改。", file=sys.stderr)
                return
            if not disconnected:
                monitor.print_line(
                    f"[连接] 事件流中断；{reconnect_delay:g} 秒后从 "
                    f"cursor={last_sequence} 自动重连。",
                    stream=sys.stderr,
                )
                disconnected = True
            sleep_fn(reconnect_delay)
            reconnect_delay = min(
                reconnect_max_seconds,
                reconnect_delay * 2,
            )
    finally:
        monitor.stop()


def _submit_and_follow(
    client: MissionGatewayClient,
    command: str,
    *,
    confirm_fn: Callable[[dict[str, Any]], bool] | None = None,
    clarify_fn: Callable[[dict[str, Any]], str | None] | None = None,
    sleep_fn=time.sleep,
    reconnect_initial_seconds: float = 0.25,
    reconnect_max_seconds: float = 4.0,
    max_reconnect_attempts: int | None = None,
    style: _Style | None = None,
    show_details: bool = False,
) -> str | None:
    style = style or _GLOBAL_STYLE
    phase_streamed = False
    try:
        preview, phase_streamed = _run_planning_phase(
            client=client,
            stream_method="stream_preview_mission",
            stream_arguments=(command,),
            fallback=lambda: client.preview_mission(command),
            message="正在解析任务意图与规划动作...",
            style=style,
            show_details=show_details,
        )
    except MissionGatewayRequestError as exc:
        _display_gateway_request_error("无法生成任务预览", exc)
        print("封存计划未生成，任务没有下发；请补充可验证的目标或约束后重试。")
        return None
    except MissionPlanningStreamError as exc:
        print(
            "无法生成任务预览："
            f"{exc}（{exc.error_code}）"
        )
        print("封存计划未生成，任务没有下发；请补充可验证的目标或约束后重试。")
        return None
    except TimeoutError:
        print("请求网关超时（超过指定超时时间）；网关多轮大模型推理尚未返回，请重试或加大 --timeout。")
        return None
    except Exception as exc:
        print(f"请求网关异常：{exc}")
        return None
    preview_status = str(preview.get("status") or "unknown")
    while preview_status == "clarification_required":
        if not phase_streamed and show_details:
            _display_thinking_trail(preview, style=style)
        question = str(
            preview.get("question")
            or preview.get("message")
            or "请补充完成安全规划所需的信息。"
        )
        current_round = preview.get("clarification_round")
        max_rounds = preview.get("max_clarification_rounds")
        round_label = (
            f" {current_round}/{max_rounds}"
            if isinstance(current_round, int)
            and isinstance(max_rounds, int)
            else ""
        )
        MissionTerminalRenderer(style=style).block(
            f"🤖 Mission Agent · 追问{round_label}",
            question.splitlines(),
            tone="magenta",
        )
        try:
            answer = (
                clarify_fn(preview)
                if clarify_fn is not None
                else _operator_prompt(
                    "  › （输入 cancel 放弃本次规划） ",
                    label="🧑 操作员 · 回答",
                )
            )
        except (EOFError, KeyboardInterrupt):
            print("\n已放弃本次规划；任务没有下发。")
            return None
        if answer is None:
            print("已放弃本次规划；任务没有下发。")
            return None
        normalized_answer = str(answer).strip()
        if normalized_answer.lower() in {"cancel", "quit", "exit"} or (
            normalized_answer in {"取消", "放弃"}
        ):
            print("已放弃本次规划；任务没有下发。")
            return None
        if not normalized_answer:
            print("回答为空；本次规划已放弃，任务没有下发。")
            return None
        planning_session_id = preview.get("planning_session_id")
        if (
            not isinstance(planning_session_id, str)
            or not planning_session_id
        ):
            print("追问会话缺少可信 session id；任务没有下发。")
            return None
        try:
            preview, phase_streamed = _run_planning_phase(
                client=client,
                stream_method="stream_planning_clarification",
                stream_arguments=(
                    planning_session_id,
                    normalized_answer,
                ),
                fallback=lambda: client.answer_planning_clarification(
                    planning_session_id,
                    normalized_answer,
                ),
                message=(
                    "正在结合澄清信息进行因果推理与计划生成..."
                ),
                style=style,
                show_details=show_details,
            )
        except MissionGatewayRequestError as exc:
            _display_gateway_request_error("提交澄清回答失败", exc)
            print("封存计划未生成，任务没有下发。")
            return None
        except MissionPlanningStreamError as exc:
            print(
                "提交澄清回答失败："
                f"{exc}（{exc.error_code}）"
            )
            print("封存计划未生成，任务没有下发。")
            return None
        except TimeoutError:
            print("提交澄清回答超时；网关多轮大模型推理尚未返回，请重试。")
            return None
        except Exception as exc:
            print(f"提交澄清回答异常：{exc}")
            return None
        preview_status = str(preview.get("status") or "unknown")
    if preview_status != "preview_ready":
        print(f"无法生成任务预览：{preview.get('message') or preview_status}")
        return None

    if not phase_streamed and show_details:
        _display_thinking_trail(preview, style=style)
    _display_plan_preview(preview, style=style)
    if not phase_streamed:
        _display_planning_timing(
            preview,
            style=style,
            show_details=show_details,
        )
    confirmed = (
        confirm_fn(preview)
        if confirm_fn is not None
        else _operator_prompt(
            "  › 确认执行？输入 yes：[y/N] ",
            label="🧑 操作员 · 安全确认",
        )
        .strip()
        .lower()
        in {"y", "yes"}
    )
    if not confirmed:
        print("未确认；任务没有下发。")
        return None
    try:
        with LiveActivityIndicator("正在向网关提交封存计划并启动调度...", style=style):
            result = client.confirm_plan(preview, operator_confirmed=True)
    except MissionGatewayRequestError as exc:
        _display_gateway_request_error("计划确认请求失败", exc)
        print("请先查询任务状态；不能根据该错误推断机器人未执行或已经停止。")
        return None
    except TimeoutError:
        print("计划确认请求超时；请先查询任务状态（status）。")
        return None
    except Exception as exc:
        print(f"计划确认请求异常：{exc}")
        return None
    mission_id = result.get("mission_id")
    status = str(result.get("status") or "unknown")
    if not isinstance(mission_id, str) or not mission_id:
        print(f"任务未提交：{result.get('message') or status}")
        return None
    MissionTerminalRenderer(style=style).render_final_response(
        f"任务已提交：{mission_id}（{status}）",
        tone="success",
    )

    _follow_mission_stream(
        client,
        mission_id,
        style=style,
        show_details=show_details,
        sleep_fn=sleep_fn,
        reconnect_initial_seconds=reconnect_initial_seconds,
        reconnect_max_seconds=reconnect_max_seconds,
        max_reconnect_attempts=max_reconnect_attempts,
    )
    return mission_id


def _display_gateway_request_error(
    label: str,
    exc: MissionGatewayRequestError,
) -> None:
    message = f"{label}（HTTP {exc.status_code}）：{exc.gateway_message}"
    if exc.gateway_code:
        message += f"\n错误码：{exc.gateway_code}"
    MissionTerminalRenderer().render_final_response(
        message,
        tone="error",
    )


def _display_plan_preview(preview: dict[str, Any], style: _Style | None = None) -> None:
    """Render only fields sealed by the Gateway preview response."""
    style = style or _GLOBAL_STYLE
    lines = [
        f"Plan digest: {preview.get('plan_digest') or 'UNKNOWN'}",
        f"Robots: {', '.join(preview.get('robot_ids') or []) or 'UNKNOWN'}",
        f"Risk: {preview.get('risk_level') or 'UNKNOWN'}",
    ]
    for step in preview.get("steps") or []:
        if not isinstance(step, dict):
            continue
        index = step.get("index", "?")
        robot_id = step.get("robot_id") or "UNKNOWN"
        step_command = step.get("command") or "UNKNOWN"
        lines.append(f"{index}. [{robot_id}] {step_command}")
        target_text = _format_plan_target(step.get("target"))
        if target_text is not None:
            lines.append(f"   目标：{target_text}")
    MissionTerminalRenderer(style=style).render_primary_status(
        "📋 任务预览（尚未执行）",
        lines,
        tone="primary",
    )


def _display_planning_timing(
    preview: dict[str, Any],
    style: _Style | None = None,
    *,
    show_details: bool = False,
) -> None:
    """Render timing returned by a synchronous/legacy preview response."""

    timing = preview.get("planning_timing")
    if not isinstance(timing, dict):
        return
    style = style or _GLOBAL_STYLE
    if not show_details:
        summary = _planning_timing_summary(timing)
        if summary:
            MissionTerminalRenderer(style=style).render_thought(
                summary,
                label="Timing · Mission Agent · 规划耗时摘要",
            )
        return
    entries = _planning_timing_entries(timing)
    if entries:
        MissionTerminalRenderer(style=style).block(
            "⏱️ Mission Agent · 阶段耗时",
            [f"{label}：{seconds:.3f} 秒" for label, seconds in entries],
            tone="cyan",
            muted=True,
        )


def _format_plan_target(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    pose = value.get("pose")
    if not isinstance(pose, dict):
        return None
    x = pose.get("x")
    y = pose.get("y")
    yaw = pose.get("yaw")
    if any(
        isinstance(number, bool) or not isinstance(number, (int, float))
        for number in (x, y)
    ):
        return None
    frame_id = str(value.get("frame_id") or "map")
    yaw_text = (
        "未指定"
        if yaw is None
        else f"{float(yaw):g}"
        if isinstance(yaw, (int, float)) and not isinstance(yaw, bool)
        else "无效"
    )
    return (
        f"{frame_id} (x={float(x):g}, y={float(y):g}, "
        f"yaw={yaw_text})"
    )


def _submit_and_poll(client: MissionGatewayClient, command: str) -> None:
    """Compatibility alias for the former polling implementation."""

    _submit_and_follow(client, command)


def _terminal_trace_status(trace: dict[str, Any]) -> str | None:
    run = trace.get("run")
    candidates = [
        trace.get("run_status"),
        run.get("run_status") if isinstance(run, dict) else None,
        run.get("status") if isinstance(run, dict) else None,
        trace.get("status"),
        trace.get("mission_status"),
    ]
    aliases = {"completed": "succeeded"}
    for value in candidates:
        if not isinstance(value, str):
            continue
        normalized = aliases.get(value, value)
        if normalized in TERMINAL_MISSION_STATUSES:
            return normalized
    return None


def _mission_status_snapshot(
    client: MissionGatewayClient,
    mission_id: str,
) -> dict[str, Any]:
    """Prefer the bounded Run projection and retain old-Gateway compatibility."""

    getter = getattr(client, "get_mission_run_status", None)
    if callable(getter):
        try:
            snapshot = getter(mission_id)
        except Exception:
            snapshot = None
        if isinstance(snapshot, dict):
            return snapshot
    return client.get_mission_trace(mission_id)


def _print_terminal_reason(
    client: MissionGatewayClient,
    mission_id: str,
) -> None:
    """Surface the authoritative failure cause after a non-success terminal state."""

    try:
        snapshot = _mission_status_snapshot(client, mission_id)
    except Exception:
        return
    result = snapshot.get("result")
    payload = result if isinstance(result, dict) else snapshot
    reasons = payload.get("failure_reasons")
    if isinstance(reasons, list) and reasons:
        shown = "；".join(str(reason) for reason in reasons[:3])
        print(f"[原因] {shown}")
        return
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        print(f"[原因] {message.strip()}")
        return
    report = snapshot.get("final_report")
    if isinstance(report, dict):
        attention = report.get("needs_attention")
        if isinstance(attention, list) and attention:
            print(f"[需关注] {'；'.join(str(item) for item in attention[:3])}")


def _display_readiness(snapshot: dict[str, Any], style: _Style | None = None) -> None:
    """Render authoritative active probes instead of stale registry heartbeats."""
    style = style or _GLOBAL_STYLE
    runtime_mode = str(snapshot.get("runtime_mode") or "unknown")
    phase = str(snapshot.get("phase") or "unknown")
    safe_state = str(snapshot.get("safe_state") or "unknown")
    readiness_text = (
        f"{phase}/{safe_state}"
        if safe_state not in {"", "unknown"}
        else phase
    )
    print(f"Runtime：{runtime_mode}; {readiness_text}")

    entries = snapshot.get("robot_readiness")
    if not isinstance(entries, list) or not entries:
        fallback = snapshot.get("fleet_state")
        _display_fleet_state(fallback if isinstance(fallback, dict) else snapshot)
        return

    print(f"Fleet：{len(entries)} 台机器人")
    for item in entries:
        if not isinstance(item, dict):
            continue
        robot_id = str(item.get("robot_id") or "unknown")
        envelope = item.get("readiness")
        envelope = envelope if isinstance(envelope, dict) else {}
        value = envelope.get("value")
        value = value if isinstance(value, dict) else {}
        status = str(value.get("status") or "unknown")
        freshness = str(envelope.get("freshness") or "unknown")
        if freshness == "stale" and "/stale" not in status:
            status += "/stale"
        capabilities = value.get("declared_capabilities")
        capability_text = (
            ", ".join(str(capability) for capability in capabilities)
            if isinstance(capabilities, list) and capabilities
            else "无已声明能力"
        )
        print(f"  - {robot_id}: {status}; {capability_text}")


def _display_fleet_state(state: dict[str, Any]) -> None:
    entries = state.get("entries")
    if not isinstance(entries, list) or not entries:
        print("Fleet：没有已登记的机器人。")
        return
    print(f"Fleet：{len(entries)} 台机器人")
    for item in entries:
        if not isinstance(item, dict):
            continue
        robot = item.get("robot")
        robot = robot if isinstance(robot, dict) else item
        robot_id = str(robot.get("robot_id") or "unknown")
        online = item.get("is_online")
        stale = item.get("is_stale")
        status = "online" if online is True else "offline"
        if stale is True:
            status += "/stale"
        capabilities = robot.get("capabilities")
        capability_text = (
            ", ".join(str(value) for value in capabilities)
            if isinstance(capabilities, list) and capabilities
            else "无已声明能力"
        )
        print(f"  - {robot_id}: {status}; {capability_text}")
