# src/fireclaw_core/mission_plan_validator.py
from __future__ import annotations

from fireclaw_core.mission_planner import MissionPlan
from fireclaw_core.robot_registry import RobotRegistry
from fireclaw_core.task_contract import structured_task_from_mission_subtask, validate_structured_robot_task


class MissionPlanValidator:
    def validate(self, plan: MissionPlan, registry: RobotRegistry) -> list[str]:
        errors: list[str] = []
        if not plan.subtasks:
            errors.append("Mission plan must contain at least one subtask.")
        for subtask in plan.subtasks:
            entry = registry.get(subtask.robot_id)
            if entry is None:
                errors.append(f"Robot {subtask.robot_id} is not registered.")
                continue
            if not entry.enabled:
                errors.append(f"Robot {subtask.robot_id} is disabled.")
            if subtask.capability_required not in entry.capabilities:
                errors.append(
                    f"Robot {subtask.robot_id} lacks required capability {subtask.capability_required}."
                )
            if subtask.floor <= 0:
                errors.append(f"Subtask floor must be positive for robot {subtask.robot_id}.")
            if subtask.execution_group < 0:
                errors.append(f"Execution group must be non-negative for robot {subtask.robot_id}.")
            structured = structured_task_from_mission_subtask(
                mission_id="validation",
                subtask=subtask,
            )
            errors.extend(validate_structured_robot_task(structured))
        return errors
