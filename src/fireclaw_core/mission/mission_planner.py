from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Protocol

from fireclaw_core.planner.planner import CHINESE_DIGITS
from fireclaw_core.agent.robot_registry import RobotRegistryEntry


# --- Data types ---

@dataclass(frozen=True)
class MissionSubtask:
    robot_id: str
    command: str
    floor: int
    capability_required: str
    execution_group: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "command": self.command,
            "floor": self.floor,
            "capability_required": self.capability_required,
            "execution_group": self.execution_group,
        }


@dataclass(frozen=True)
class MissionPlan:
    intent: str
    command: str
    subtasks: list[MissionSubtask] = field(default_factory=list)

    @property
    def execution_groups(self) -> int:
        if not self.subtasks:
            return 0
        return max(s.execution_group for s in self.subtasks) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "command": self.command,
            "execution_groups": self.execution_groups,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }


@dataclass(frozen=True)
class MissionPlanningResult:
    status: str
    message: str
    intent: str | None = None
    plan: MissionPlan | None = None


@dataclass(frozen=True)
class MissionPlannerContext:
    available_robots: list[RobotRegistryEntry] = field(default_factory=list)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    operator_corrections: list[dict[str, Any]] = field(default_factory=list)


# --- Protocol ---

class MissionPlannerProtocol(Protocol):
    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult:
        ...


# --- Intent and capability mapping ---

_INTENT_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, intent, required_capability)
    (r"搜索|搜救|寻找受困|查找受困", "search", "search_for_victims"),
    (r"巡逻|巡查|巡检", "patrol", "patrol"),
    (r"灭火|扑灭|压制火势", "firefight", "firefight"),
    (r"侦察|探查|侦查", "recon", "recon"),
    (r"运送|搬运|送物资", "transport", "transport"),
]


def _extract_floors(command: str) -> list[int]:
    """Extract all floor numbers from a Chinese command string."""
    floors: list[int] = []
    for match in re.finditer(r"([0-9]+|[一二三四五六七八九十])楼", command):
        token = match.group(1)
        if token.isdigit():
            floors.append(int(token))
        elif token in CHINESE_DIGITS:
            floors.append(CHINESE_DIGITS[token])
    return floors


def _detect_intent(command: str) -> tuple[str, str] | None:
    """Return (intent, capability_required) or None if no intent matched."""
    for pattern, intent, capability in _INTENT_PATTERNS:
        if re.search(pattern, command):
            return intent, capability
    return None


def _floor_command(floor: int, intent: str) -> str:
    """Generate a subtask command for a specific floor."""
    intent_verbs = {
        "search": "搜索受困人员",
        "patrol": "巡逻",
        "firefight": "灭火",
        "recon": "侦察",
        "transport": "运送物资",
    }
    verb = intent_verbs.get(intent, "执行任务")
    return f"去{floor}楼{verb}"


# --- Planner ---

class MissionPlanner:
    """Deterministic rule-based mission planner for multi-floor/multi-robot commands."""

    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult:
        intent_match = _detect_intent(command)
        if intent_match is None:
            return MissionPlanningResult(
                status="clarify",
                message="无法识别任务意图。请指定任务类型，例如：搜索、巡逻、灭火、侦察、运送。",
            )
        intent, capability_required = intent_match

        floors = _extract_floors(command)
        if not floors:
            return MissionPlanningResult(
                status="clarify",
                message="请指定目标楼层，例如：去二楼搜索受困人员。",
            )

        available_robots = context.available_robots if context is not None else []
        capable_robots = [
            r for r in available_robots
            if r.enabled and capability_required in r.capabilities
        ]

        if not capable_robots:
            return MissionPlanningResult(
                status="clarify",
                message=f"没有可用的机器人具备 {capability_required} 能力。",
            )

        subtasks = _assign_robots(floors, capable_robots, intent, capability_required)
        plan = MissionPlan(intent=intent, command=command, subtasks=subtasks)

        return MissionPlanningResult(
            status="planned",
            message=f"已生成任务计划：{len(subtasks)} 个子任务，{plan.execution_groups} 个执行组。",
            intent=intent,
            plan=plan,
        )


def _assign_robots(
    floors: list[int],
    capable_robots: list[RobotRegistryEntry],
    intent: str,
    capability_required: str,
) -> list[MissionSubtask]:
    """Assign robots to floor subtasks.

    When there are enough robots, each floor gets a different robot (parallel).
    When there are fewer robots than floors, robots are reused with sequential groups.
    """
    subtasks: list[MissionSubtask] = []
    robot_count = len(capable_robots)

    for index, floor in enumerate(floors):
        robot = capable_robots[index % robot_count]
        execution_group = index // robot_count
        subtasks.append(MissionSubtask(
            robot_id=robot.robot_id,
            command=_floor_command(floor, intent),
            floor=floor,
            capability_required=capability_required,
            execution_group=execution_group,
        ))

    return subtasks
