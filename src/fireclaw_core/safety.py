from __future__ import annotations

from dataclasses import dataclass

from fireclaw_core.planner import PlanningResult
from fireclaw_core.robot import EnvironmentState, RobotState
from fireclaw_core.skills import SkillRegistry


@dataclass(frozen=True)
class SafetyDecision:
    status: str
    reasons: list[str]


class SafetyGate:
    def evaluate(
        self,
        planning_result: PlanningResult,
        registry: SkillRegistry,
        *,
        dry_run: bool,
        available_sensors: set[str] | None = None,
        operator_confirmed: bool = False,
        robot_state: RobotState | None = None,
        environment_state: EnvironmentState | None = None,
    ) -> SafetyDecision:
        if planning_result.status == "clarify":
            return SafetyDecision(status="clarify", reasons=[planning_result.message])

        if planning_result.plan is None:
            return SafetyDecision(status="block", reasons=["Planner did not produce an executable plan."])

        if planning_result.intent == "rescue_victim" and planning_result.target_floor is None:
            return SafetyDecision(status="block", reasons=["Target floor is missing."])

        state_blocks = self._evaluate_state_blocks(planning_result, robot_state, environment_state)
        if state_blocks:
            return SafetyDecision(status="block", reasons=state_blocks)

        missing = [
            f"Missing skill: {step.skill_name}"
            for step in planning_result.plan.steps
            if not registry.has(step.skill_name)
        ]
        if missing:
            return SafetyDecision(status="block", reasons=missing)

        # Infer sensors from robot_state when not explicitly provided
        if available_sensors is not None:
            sensors = available_sensors
        elif robot_state is not None:
            sensors = set(robot_state.available_sensors)
        else:
            sensors = set()
        missing_sensors: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is None:
                continue
            for sensor in skill.required_sensors:
                if sensor not in sensors:
                    missing_sensors.append(
                        f"Skill {step.skill_name} requires unavailable sensor: {sensor}"
                    )
        if missing_sensors:
            return SafetyDecision(status="block", reasons=missing_sensors)

        unsafe_retries: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is not None and skill.max_attempts > 1 and not skill.idempotent:
                unsafe_retries.append(
                    f"Skill {step.skill_name} has max_attempts > 1 but is not idempotent."
                )
        if unsafe_retries:
            return SafetyDecision(status="block", reasons=unsafe_retries)

        if dry_run:
            non_dry_run_skills = [
                f"Skill is not dry-run only: {step.skill_name}"
                for step in planning_result.plan.steps
                if registry.get(step.skill_name) is not None and not registry.get(step.skill_name).dry_run_only
            ]
            if non_dry_run_skills:
                return SafetyDecision(status="block", reasons=non_dry_run_skills)
        else:
            real_robot_blocks: list[str] = []
            for step in planning_result.plan.steps:
                skill = registry.get(step.skill_name)
                if skill is None:
                    continue
                if skill.dry_run_only or not skill.allow_real_robot:
                    real_robot_blocks.append(
                        f"Skill is not allowed for real robot execution: {step.skill_name}"
                    )
            if real_robot_blocks:
                return SafetyDecision(status="block", reasons=real_robot_blocks)

        confirmation_reasons: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is None:
                continue
            if not dry_run:
                confirmation_reasons.append(
                    f"Real robot execution requires operator confirmation: {step.skill_name}"
                )
            if skill.risk_level in {"high", "critical"}:
                confirmation_reasons.append(
                    f"Skill {step.skill_name} has high risk level: {skill.risk_level}"
                )
        if confirmation_reasons and not operator_confirmed:
            return SafetyDecision(status="require_confirmation", reasons=confirmation_reasons)

        return SafetyDecision(status="allow", reasons=[])

    def _evaluate_state_blocks(
        self,
        planning_result: PlanningResult,
        robot_state: RobotState | None,
        environment_state: EnvironmentState | None,
    ) -> list[str]:
        blocks: list[str] = []
        if robot_state is not None:
            if not robot_state.online:
                blocks.append(f"Robot {robot_state.robot_id} is offline.")
            if robot_state.battery_percent < 10.0:
                blocks.append(
                    f"Robot {robot_state.robot_id} battery is too low: {robot_state.battery_percent}%."
                )

        if (
            environment_state is not None
            and planning_result.target_floor is not None
            and planning_result.target_floor not in environment_state.reachable_floors
        ):
            blocks.append(f"Target floor is not reachable: {planning_result.target_floor}")
        return blocks
