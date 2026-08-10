from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True)
class PlanStep:
    skill_name: str
    inputs: dict[str, Any]


@dataclass(frozen=True)
class Plan:
    intent: str
    steps: list[PlanStep]


@dataclass(frozen=True)
class PlanningResult:
    status: str
    message: str
    intent: str | None = None
    target_floor: int | None = None
    target_pose: dict[str, Any] | None = None
    plan: Plan | None = None


@dataclass(frozen=True)
class PlannerContext:
    session_id: str
    turn_index: int
    recent_records: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    context_envelope: dict[str, Any] | None = None
    context_manifest: dict[str, Any] | None = None


class RuleBasedPlanner:
    def plan(self, command: str, context: PlannerContext | None = None) -> PlanningResult:
        direct_skill_result = self._plan_direct_skill_invocation(command)
        if direct_skill_result is not None:
            return direct_skill_result

        point = self._extract_point(command)
        if point is not None:
            return PlanningResult(
                status="planned",
                message="已生成单楼层目标点导航计划。",
                intent="point_navigation",
                target_pose=point,
                plan=Plan(
                    intent="point_navigation",
                    steps=[PlanStep("navigate_to_point", dict(point))],
                ),
            )

        return PlanningResult(
            status="clarify",
            message=(
                "请明确当前单楼层 map 坐标系中的目标点，"
                "例如：去坐标 (2.0, 1.5)。"
            ),
        )

    def _extract_floor(self, command: str) -> int | None:
        match = re.search(r"([0-9]+|[一二三四五六七八九十])楼", command)
        if not match:
            return None
        token = match.group(1)
        if token.isdigit():
            return int(token)
        return CHINESE_DIGITS.get(token)

    def _extract_point(self, command: str) -> dict[str, Any] | None:
        match = re.search(
            r"(?:坐标|目标点|点)\s*[（(]?\s*"
            r"(-?\d+(?:\.\d+)?)\s*[,，]\s*"
            r"(-?\d+(?:\.\d+)?)\s*[)）]?",
            command,
        )
        if not match:
            return None
        return {
            "x": float(match.group(1)),
            "y": float(match.group(2)),
            "yaw": 0.0,
            "frame_id": "map",
        }

    def _plan_direct_skill_invocation(self, command: str) -> PlanningResult | None:
        match = re.match(
            r"^\s*(?:运行|调用|执行)\s+([A-Za-z0-9_.-]+)(?:\s+(.*?))?\s*$",
            command,
        )
        if not match:
            return None

        skill_name = match.group(1)
        remainder = (match.group(2) or "").strip()
        inputs: dict[str, Any] = {}
        if remainder:
            payload_match = re.match(r"^(?:处理|输入|参数)\s+(.+?)\s*$", remainder)
            if payload_match:
                inputs = {"text": payload_match.group(1).strip()}
            else:
                inputs = {"text": remainder}

        plan = Plan(
            intent="direct_skill_invocation",
            steps=[PlanStep(skill_name=skill_name, inputs=inputs)],
        )
        return PlanningResult(
            status="planned",
            message=f"已生成直接调用技能 {skill_name} 的 dry-run 计划。",
            intent="direct_skill_invocation",
            plan=plan,
        )
