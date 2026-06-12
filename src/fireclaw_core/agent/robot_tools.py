from __future__ import annotations

from typing import Any

from fireclaw_core.agent.robot_agent import RobotLocalPlan, RobotLocalPlanStep
from fireclaw_core.devtools.tool_schema import skill_metadata_to_tool_schema
from fireclaw_core.execution.skills import SkillRegistry


def build_robot_skill_tools(
    registry: SkillRegistry,
    *,
    exposed_skill_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    exposed = set(exposed_skill_names)
    tools: list[dict[str, Any]] = []
    for metadata in registry.list_metadata():
        if metadata["name"] in exposed:
            tools.append(skill_metadata_to_tool_schema(metadata))
    return sorted(tools, key=lambda item: str(item["function"]["name"]))


def local_plan_from_direct_tool_calls(
    calls: list[dict[str, Any]],
    *,
    intent: str,
) -> RobotLocalPlan:
    steps = [
        RobotLocalPlanStep(
            skill_name=str(call["name"]),
            inputs=dict(call.get("arguments") or {}),
            reason="Direct robot skill tool call from LLM.",
        )
        for call in calls
    ]
    return RobotLocalPlan(
        intent=intent,
        steps=steps,
        rationale="LLM selected concrete robot skill tools.",
        confidence=None,
    )
