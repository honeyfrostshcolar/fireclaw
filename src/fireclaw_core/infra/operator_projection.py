from __future__ import annotations

from typing import Any

from fireclaw_core.execution.builtin_physical_skills import (
    get_builtin_physical_skill,
)


class OperatorEventProjector:
    def __init__(self, *, skill_catalog: Any | None = None) -> None:
        self._skill_catalog = skill_catalog

    def project(self, event: dict[str, Any]) -> str | None:
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type == "task.received":
            command = _text(payload.get("command"), "未知任务")
            return f"已接收任务：{command}。"
        if event_type == "task.planned":
            return "正在规划救援任务。"
        if event_type == "safety.decided":
            return self._project_safety(payload)
        if event_type == "confirmation.pending":
            return "等待人工确认后继续执行。"
        if event_type == "confirmation.confirmed":
            return "人工确认已收到，继续执行。"
        if event_type == "skill.started":
            return self._project_skill_started(payload)
        if event_type == "skill.attempted":
            return self._project_skill_attempted(payload)
        if event_type == "skill.failed":
            skill_name = _text(payload.get("skill_name"), "unknown")
            error = _text(payload.get("error"), "未知错误")
            return f"技能 {skill_name} 执行失败：{error}"
        if event_type == "task.completed":
            message = _text(payload.get("message"), "任务已完成")
            return f"任务完成：{message}"
        if event_type == "task.blocked":
            message = _text(payload.get("message"), "任务因前置条件不满足而阻塞")
            return f"任务阻塞：{message}"
        if event_type == "task.escalated":
            message = _text(payload.get("message"), "任务需要上级智能体或操作员介入")
            return f"任务已升级：{message}"
        if event_type == "task.failed":
            message = _text(payload.get("message"), "任务执行失败")
            return f"任务失败：{message}"
        if event_type == "task.timed_out":
            message = _text(payload.get("message"), "任务执行超时")
            return f"任务超时：{message}"
        if event_type == "task.lost":
            message = _text(payload.get("message"), "机器人任务状态丢失")
            return f"任务失联：{message}"
        if event_type == "task.cancel_requested":
            return "已请求取消任务，等待当前步骤结束。"
        if event_type == "task.cancelled":
            return "任务已取消。"
        return None

    def _project_safety(self, payload: dict[str, Any]) -> str | None:
        status = payload.get("status")
        reasons = _join_reasons(payload.get("reasons"))
        if status == "allow":
            return "安全检查通过。"
        if status == "clarify":
            return f"需要补充信息：{reasons or '任务信息不完整'}"
        if status == "block":
            return f"安全检查未通过：{reasons or '任务不允许执行'}"
        if status == "require_confirmation":
            return f"该任务需要人工确认：{reasons or '需要操作员确认'}"
        return None

    def _project_skill_started(self, payload: dict[str, Any]) -> str:
        skill_name = _text(payload.get("skill_name"), "unknown")
        operator_message = payload.get("operator_message")
        if isinstance(operator_message, str) and operator_message.strip():
            return operator_message.strip()
        plugin = self._physical_plugin(skill_name)
        inputs = (
            payload.get("inputs")
            if isinstance(payload.get("inputs"), dict)
            else {}
        )
        if plugin is not None and plugin.operator_started_message is not None:
            return plugin.operator_started_message(inputs)
        return f"正在执行技能 {skill_name}。"

    def _physical_plugin(self, skill_name: str):
        if self._skill_catalog is None:
            return get_builtin_physical_skill(skill_name)
        getter = getattr(self._skill_catalog, "get", None)
        if not callable(getter):
            return None
        value = getter(skill_name)
        return getattr(value, "physical_plugin", value)

    def _project_skill_attempted(self, payload: dict[str, Any]) -> str | None:
        if payload.get("status") != "failed":
            return None
        skill_name = _text(payload.get("skill_name"), "unknown")
        attempt_number = payload.get("attempt_number")
        attempt_text = str(attempt_number) if attempt_number is not None else "?"
        error = _text(payload.get("error"), "未知错误")
        return f"技能 {skill_name} 第 {attempt_text} 次尝试失败：{error}"


def _text(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _join_reasons(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        return value.strip()
    return ""
