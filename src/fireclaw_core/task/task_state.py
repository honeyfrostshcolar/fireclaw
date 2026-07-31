from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_TERMINAL_EVENT_TYPES,
    ROBOT_TASK_TERMINAL_STATUSES,
    robot_task_terminal_status_from_event,
)


TERMINAL_TASK_STATUSES = set(ROBOT_TASK_TERMINAL_STATUSES) | {
    "succeeded",
    "clarify",
}


@dataclass
class TaskState:
    task_id: str | None = None
    session_id: str | None = None
    status: str = "unknown"
    command: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    active_skill_name: str | None = None
    active_action_id: str | None = None
    skill_count: int = 0
    action_count: int = 0
    result: dict[str, Any] | None = None


@dataclass
class SkillState:
    skill_run_id: str
    skill_name: str | None = None
    status: str = "started"
    inputs: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    attempt_count: int = 0
    last_error: str | None = None
    action_ids: list[str] = field(default_factory=list)


@dataclass
class ActionState:
    action_id: str
    task_id: str | None = None
    skill_run_id: str | None = None
    skill_name: str | None = None
    action_type: str | None = None
    status: str = "requested"
    inputs: dict[str, Any] = field(default_factory=dict)
    requested_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    risk_level: str | None = None
    dry_run: bool | None = None
    timeout_seconds: float | None = None
    feedback_count: int = 0
    last_feedback: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None


def project_task_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    task = TaskState()
    skills: list[SkillState] = []
    actions: dict[str, ActionState] = {}
    current_skill: SkillState | None = None

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        timestamp = _string_or_none(event.get("timestamp"))
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        _fill_task_identity(task, event)

        if event_type == "task.received":
            _set_nonterminal_task_status(task, "received")
            task.command = _string_or_none(payload.get("command"))
            task.started_at = task.started_at or timestamp
        elif event_type == "task.planned":
            _set_nonterminal_task_status(task, "planned")
        elif event_type == "safety.decided":
            _apply_safety_status(task, payload)
        elif event_type == "skill.started":
            current_skill = _new_skill_state(len(skills) + 1, payload, timestamp)
            skills.append(current_skill)
            task.active_skill_name = current_skill.skill_name
            _set_nonterminal_task_status(task, "running")
        elif event_type == "skill.attempted":
            skill = current_skill or _latest_skill(skills)
            if skill is not None:
                skill.attempt_count = max(skill.attempt_count, _int_or_zero(payload.get("attempt_number")))
                if payload.get("status") == "failed":
                    skill.last_error = _string_or_none(payload.get("error"))
        elif event_type == "skill.succeeded":
            skill = current_skill or _latest_skill(skills)
            if skill is not None:
                skill.status = "succeeded"
                skill.ended_at = timestamp
                skill.attempt_count = max(skill.attempt_count, _int_or_zero(payload.get("attempt_count")))
            task.active_skill_name = None
            current_skill = None
        elif event_type == "skill.failed":
            skill = current_skill or _latest_skill(skills)
            if skill is not None:
                skill.status = "failed"
                skill.ended_at = timestamp
                skill.last_error = _string_or_none(payload.get("error"))
                skill.attempt_count = max(skill.attempt_count, _int_or_zero(payload.get("attempt_count")))
            task.active_skill_name = None
            current_skill = None
        elif event_type in {
            "action.requested",
            "action.started",
            "action.feedback",
            "action.cancel_requested",
            "action.cancelled",
            "action.succeeded",
            "action.failed",
        }:
            action = _action_for_event(actions, payload, event, current_skill, timestamp)
            _apply_action_event(action, event_type, payload, timestamp)
            if current_skill is not None and action.action_id not in current_skill.action_ids:
                current_skill.action_ids.append(action.action_id)
            if action.status not in {"succeeded", "failed", "cancelled", "timeout"}:
                task.active_action_id = action.action_id
            elif task.active_action_id == action.action_id:
                task.active_action_id = None
        elif event_type == "task.cancel_requested":
            _set_nonterminal_task_status(task, "cancel_requested")
        elif event_type in ROBOT_TASK_TERMINAL_EVENT_TYPES:
            _apply_terminal_task_event(task, event_type, payload, timestamp)
            if task.status == "cancelled":
                if current_skill is not None and current_skill.status not in {"succeeded", "failed"}:
                    current_skill.status = "cancelled"
                    current_skill.ended_at = current_skill.ended_at or timestamp
                for action in actions.values():
                    if action.status not in {"succeeded", "failed", "cancelled", "timeout"}:
                        action.status = "cancelled"
                        action.ended_at = action.ended_at or timestamp
                task.active_skill_name = None
                task.active_action_id = None

    task.skill_count = len(skills)
    task.action_count = len(actions)
    return {
        "task": asdict(task),
        "skills": [asdict(skill) for skill in skills],
        "actions": [asdict(action) for action in actions.values()],
    }


def _fill_task_identity(task: TaskState, event: dict[str, Any]) -> None:
    task.task_id = task.task_id or _string_or_none(event.get("task_id"))
    task.session_id = task.session_id or _string_or_none(event.get("session_id"))


def _set_nonterminal_task_status(task: TaskState, status: str) -> None:
    if task.status not in TERMINAL_TASK_STATUSES:
        task.status = status


def _apply_safety_status(task: TaskState, payload: dict[str, Any]) -> None:
    status = payload.get("status")
    if status == "require_confirmation":
        _set_nonterminal_task_status(task, "awaiting_confirmation")
    elif status == "block":
        _set_nonterminal_task_status(task, "blocked")
    elif status == "clarify":
        _set_nonterminal_task_status(task, "clarify")


def _new_skill_state(index: int, payload: dict[str, Any], timestamp: str | None) -> SkillState:
    inputs = payload.get("inputs")
    return SkillState(
        skill_run_id=f"skill-{index}",
        skill_name=_string_or_none(payload.get("skill_name")),
        status="running",
        inputs=dict(inputs) if isinstance(inputs, dict) else {},
        started_at=timestamp,
    )


def _latest_skill(skills: list[SkillState]) -> SkillState | None:
    if not skills:
        return None
    return skills[-1]


def _action_for_event(
    actions: dict[str, ActionState],
    payload: dict[str, Any],
    event: dict[str, Any],
    current_skill: SkillState | None,
    timestamp: str | None,
) -> ActionState:
    action_id = _string_or_none(payload.get("action_id")) or f"action-{len(actions) + 1}"
    action = actions.get(action_id)
    if action is None:
        inputs = payload.get("inputs")
        action = ActionState(
            action_id=action_id,
            task_id=_string_or_none(payload.get("task_id")) or _string_or_none(event.get("task_id")),
            skill_run_id=current_skill.skill_run_id if current_skill is not None else None,
            skill_name=_string_or_none(payload.get("skill_name")) or (current_skill.skill_name if current_skill else None),
            action_type=_string_or_none(payload.get("action_type")),
            inputs=dict(inputs) if isinstance(inputs, dict) else {},
            requested_at=timestamp,
            risk_level=_string_or_none(payload.get("risk_level")),
            dry_run=payload.get("dry_run") if isinstance(payload.get("dry_run"), bool) else None,
            timeout_seconds=_float_or_none(payload.get("timeout_seconds")),
        )
        actions[action_id] = action
    return action


def _apply_action_event(
    action: ActionState,
    event_type: str,
    payload: dict[str, Any],
    timestamp: str | None,
) -> None:
    if event_type == "action.requested":
        action.status = "requested"
        action.requested_at = action.requested_at or timestamp
    elif event_type == "action.started":
        action.status = "running"
        action.started_at = action.started_at or timestamp
    elif event_type == "action.feedback":
        action.status = "feedback"
        action.feedback_count += 1
        action.last_feedback = dict(payload)
    elif event_type == "action.cancel_requested":
        action.status = "cancel_requested"
    elif event_type == "action.cancelled":
        action.status = "cancelled"
        action.ended_at = timestamp
    elif event_type == "action.succeeded":
        action.status = "succeeded"
        action.ended_at = timestamp
        output = payload.get("output")
        action.output = dict(output) if isinstance(output, dict) else None
        action.error = _string_or_none(payload.get("error"))
    elif event_type == "action.failed":
        action.status = "failed"
        action.ended_at = timestamp
        output = payload.get("output")
        action.output = dict(output) if isinstance(output, dict) else None
        action.error = _string_or_none(payload.get("error"))


def _apply_terminal_task_event(
    task: TaskState,
    event_type: str,
    payload: dict[str, Any],
    timestamp: str | None,
) -> None:
    task.ended_at = timestamp
    result = payload.get("result")
    task.result = dict(result) if isinstance(result, dict) else None
    status = robot_task_terminal_status_from_event({
        "type": event_type,
        "payload": payload,
    })
    task.status = status or "failed"


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
