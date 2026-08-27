from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fireclaw_core.mission.graph_proposal import MissionGraphProposal
from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.mission.mission_planning_audit import MissionPlanningAuditRecord


# --- Data types ---

@dataclass(frozen=True)
class MissionSubtask:
    robot_id: str
    command: str
    floor: int | None
    capability_required: str
    execution_group: int = 0
    node_id: str | None = None
    task_type: str | None = None
    target: dict[str, Any] = field(default_factory=dict)
    completion_goal: str | None = None
    completion_contract: dict[str, Any] = field(default_factory=dict)
    belief_requirements: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "robot_id": self.robot_id,
            "command": self.command,
            "floor": self.floor,
            "capability_required": self.capability_required,
            "execution_group": self.execution_group,
        }
        optional = {
            "node_id": self.node_id,
            "task_type": self.task_type,
            "completion_goal": self.completion_goal,
        }
        result.update({
            key: value for key, value in optional.items() if value is not None
        })
        if self.target:
            result["target"] = dict(self.target)
        if self.completion_contract:
            result["completion_contract"] = dict(self.completion_contract)
        if self.belief_requirements:
            result["belief_requirements"] = [
                dict(requirement)
                for requirement in self.belief_requirements
            ]
        return result


@dataclass(frozen=True)
class MissionPlan:
    intent: str
    command: str
    subtasks: list[MissionSubtask] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)

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
            "knowledge_refs": list(self.knowledge_refs),
        }


@dataclass(frozen=True)
class MissionPlanningResult:
    status: str
    message: str
    intent: str | None = None
    plan: MissionPlan | None = None
    audit_record: MissionPlanningAuditRecord | None = None
    graph_proposal: MissionGraphProposal | None = None


@dataclass(frozen=True)
class MissionPlannerContext:
    available_robots: list[RobotRegistryEntry] = field(default_factory=list)
    state_snapshot: dict[str, Any] | None = None
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    operator_corrections: list[dict[str, Any]] = field(default_factory=list)
    external_knowledge: list[dict[str, Any]] = field(default_factory=list)
    tool_exposed_belief_ids: tuple[str, ...] | None = None
    active_observation_capabilities: tuple[str, ...] | None = None
    # Current-dialogue answers are authenticated operator input, not advisory
    # memory. The Gateway bounds and owns this list before it reaches a model.
    operator_clarifications: list[dict[str, Any]] = field(default_factory=list)


# --- Protocol ---

class MissionPlannerProtocol(Protocol):
    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult:
        ...


# --- Intent and capability mapping ---

DEFAULT_INTENT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # (pattern, intent, required_capability)
    (r"搜索|搜救|寻找受困|查找受困", "search", "victim_search"),
    (r"巡逻|巡查|巡检|规划运动|导航测试|简单移动", "patrol", "patrol"),
    (r"灭火|扑灭|压制火势", "firefight", "firefight"),
    (r"侦察|探查|侦查", "recon", "recon"),
    (r"运送|搬运|送物资", "transport", "transport"),
)


def _extract_points(command: str) -> list[dict[str, float]]:
    """Extract 2D target points from the current map."""
    points: list[dict[str, float]] = []
    for match in re.finditer(
        r"(?:坐标|目标点|点)\s*[（(]?\s*"
        r"(-?\d+(?:\.\d+)?)\s*[,，]\s*"
        r"(-?\d+(?:\.\d+)?)\s*[)）]?",
        command,
    ):
        points.append({
            "x": float(match.group(1)),
            "y": float(match.group(2)),
            "yaw": 0.0,
        })
    return points


def _detect_intent(
    command: str,
    patterns: Sequence[tuple[str, str, str]] = DEFAULT_INTENT_PATTERNS,
) -> tuple[str, str] | None:
    """Return (intent, capability_required) or None if no intent matched."""
    for pattern, intent, capability in patterns:
        if re.search(pattern, command):
            return intent, capability
    return None


# --- Planner ---

class MissionPlanner:
    """Deterministic fallback over declared robot capability names."""

    def __init__(
        self,
        *,
        intent_patterns: Sequence[tuple[str, str, str]] | None = None,
    ) -> None:
        self.intent_patterns = tuple(
            intent_patterns or DEFAULT_INTENT_PATTERNS
        )

    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult:
        intent_match = _detect_intent(command, self.intent_patterns)
        if intent_match is None:
            return MissionPlanningResult(
                status="clarify",
                message="无法识别任务意图。请指定任务类型，例如：搜索、巡逻、灭火、侦察、运送。",
            )
        intent, capability_required = intent_match

        points = _extract_points(command)
        if not points:
            return MissionPlanningResult(
                status="clarify",
                message=(
                    "当前仅支持二维 map；请指定当前地图中的目标点，"
                    "例如：去坐标 (2.0, 1.5) 搜索受困人员。"
                ),
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

        subtasks = _assign_point_targets(
            points,
            capable_robots,
            intent,
            capability_required,
        )
        plan = MissionPlan(intent=intent, command=command, subtasks=subtasks)

        return MissionPlanningResult(
            status="planned",
            message=f"已生成任务计划：{len(subtasks)} 个子任务，{plan.execution_groups} 个执行组。",
            intent=intent,
            plan=plan,
        )


def _assign_point_targets(
    points: list[dict[str, float]],
    capable_robots: list[RobotRegistryEntry],
    intent: str,
    capability_required: str,
) -> list[MissionSubtask]:
    """Assign 2D map points across capable robots."""
    subtasks: list[MissionSubtask] = []
    robot_count = len(capable_robots)
    for index, pose in enumerate(points):
        robot = capable_robots[index % robot_count]
        execution_group = index // robot_count
        x = pose["x"]
        y = pose["y"]
        subtasks.append(MissionSubtask(
            robot_id=robot.robot_id,
            command=f"在当前地图坐标 ({x}, {y}) 执行 {intent}",
            floor=None,
            capability_required=capability_required,
            execution_group=execution_group,
            target={
                "frame_id": "map",
                "pose": dict(pose),
            },
        ))
    return subtasks
