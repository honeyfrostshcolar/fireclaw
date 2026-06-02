from __future__ import annotations

from typing import Any


class OperatorEventProjector:
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
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        floor = inputs.get("floor")
        floor_text = str(floor) if floor is not None else "目标"

        if skill_name == "navigate_to_floor":
            return f"正在前往{floor_text}楼。"
        if skill_name == "search_for_victims":
            return f"正在搜索{floor_text}楼被困人员。"
        if skill_name == "assess_victim":
            return "正在评估被困人员状态。"
        if skill_name == "report_status":
            return "正在向操作员报告现场状态。"
        if skill_name == "return_to_safe_zone":
            return "正在返回安全区域。"
        return f"正在执行技能 {skill_name}。"

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
