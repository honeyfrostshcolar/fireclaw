from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


RESCUE_SKILL_SEQUENCE = (
    "navigate_to_floor",
    "search_for_victims",
    "assess_victim",
    "report_status",
    "return_to_safe_zone",
)

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

        floor = self._extract_floor(command)
        if floor is None or "救人" not in command:
            return PlanningResult(
                status="clarify",
                message="请明确要前往的楼层和救援目标，例如：去二楼救人。",
            )

        policy_skill = self._extract_policy_skill(command)
        steps = self._build_rescue_steps(floor, command, policy_skill)
        return PlanningResult(
            status="planned",
            message="已生成救援 dry-run 计划。",
            intent="rescue_victim",
            target_floor=floor,
            plan=Plan(intent="rescue_victim", steps=steps),
        )

    def _extract_floor(self, command: str) -> int | None:
        match = re.search(r"([0-9]+|[一二三四五六七八九十])楼", command)
        if not match:
            return None
        token = match.group(1)
        if token.isdigit():
            return int(token)
        return CHINESE_DIGITS.get(token)

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

    def _extract_policy_skill(self, command: str) -> str | None:
        match = re.search(
            r"(?:导航策略用|策略用|使用|用)\s+([A-Za-z0-9_.-]+)\s*$",
            command,
        )
        if not match:
            return None
        return match.group(1)

    def _build_rescue_steps(
        self,
        floor: int,
        command: str,
        policy_skill: str | None,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = []
        if policy_skill:
            steps.append(PlanStep(policy_skill, {"floor": floor, "command": command}))
        steps.extend(
            [
                PlanStep("navigate_to_floor", {"floor": floor}),
                PlanStep("search_for_victims", {"floor": floor}),
                PlanStep("assess_victim", {"floor": floor}),
                PlanStep("report_status", {"floor": floor}),
                PlanStep("return_to_safe_zone", {}),
            ]
        )
        return steps
