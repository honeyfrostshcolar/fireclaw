from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fireclaw_core.robot import RobotActionResult, RobotAdapter
from fireclaw_core.runtime import SubprocessSkillRunner


SkillHandler = Callable[[dict[str, Any]], RobotActionResult]

GENERIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}

FLOOR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "floor": {"type": "integer"},
    },
    "required": ["floor"],
    "additionalProperties": False,
}

EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    handler: SkillHandler
    runtime: str = "in_process"
    dry_run_only: bool = True
    max_attempts: int = 1
    idempotent: bool = False
    required_sensors: list[str] = field(default_factory=list)
    failure_categories: list[str] = field(default_factory=list)
    allow_real_robot: bool = False
    timeout_seconds: float | None = None
    input_schema: dict[str, Any] = field(default_factory=lambda: dict(GENERIC_INPUT_SCHEMA))
    risk_level: str = "low"

    def run(self, inputs: dict[str, Any]) -> RobotActionResult:
        return self.handler(inputs)


@dataclass
class SkillRegistry:
    skills: dict[str, Skill]

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def has(self, name: str) -> bool:
        return name in self.skills

    def names(self) -> set[str]:
        return set(self.skills)

    def register(self, skill: Skill, *, replace: bool = False) -> None:
        if skill.name in self.skills and not replace:
            raise ValueError(f"Skill already registered: {skill.name}")
        self.skills[skill.name] = skill

    def extend(self, skills: list[Skill], *, replace: bool = False) -> None:
        for skill in skills:
            self.register(skill, replace=replace)

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "runtime": skill.runtime,
                "dry_run_only": skill.dry_run_only,
                "max_attempts": skill.max_attempts,
                "idempotent": skill.idempotent,
                "required_sensors": list(skill.required_sensors),
                "failure_categories": list(skill.failure_categories),
                "allow_real_robot": skill.allow_real_robot,
                "timeout_seconds": skill.timeout_seconds,
                "input_schema": dict(skill.input_schema),
                "risk_level": skill.risk_level,
            }
            for skill in sorted(self.skills.values(), key=lambda item: item.name)
        ]


def create_default_skill_registry(robot: RobotAdapter) -> SkillRegistry:
    return SkillRegistry(
        skills={
            "navigate_to_floor": Skill(
                name="navigate_to_floor",
                description="Dry-run navigation to a target floor.",
                handler=lambda inputs: robot.navigate_to_floor(int(inputs["floor"])),
                input_schema=dict(FLOOR_INPUT_SCHEMA),
            ),
            "search_for_victims": Skill(
                name="search_for_victims",
                description="Dry-run victim search on a floor.",
                handler=lambda inputs: robot.search_for_victims(int(inputs["floor"])),
                input_schema=dict(FLOOR_INPUT_SCHEMA),
            ),
            "assess_victim": Skill(
                name="assess_victim",
                description="Dry-run victim condition assessment.",
                handler=lambda inputs: robot.assess_victim(int(inputs["floor"])),
                input_schema=dict(FLOOR_INPUT_SCHEMA),
            ),
            "report_status": Skill(
                name="report_status",
                description="Dry-run status report to operator.",
                handler=lambda inputs: robot.report_status(int(inputs["floor"])),
                input_schema=dict(FLOOR_INPUT_SCHEMA),
            ),
            "return_to_safe_zone": Skill(
                name="return_to_safe_zone",
                description="Dry-run return to safe zone.",
                handler=lambda inputs: robot.return_to_safe_zone(),
                input_schema=dict(EMPTY_INPUT_SCHEMA),
            ),
        }
    )


def create_subprocess_skill(
    *,
    name: str,
    description: str,
    command: list[str],
    timeout_seconds: float = 30.0,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    dry_run_only: bool = True,
    max_attempts: int = 1,
    idempotent: bool = False,
    required_sensors: list[str] | None = None,
    failure_categories: list[str] | None = None,
    allow_real_robot: bool = False,
    input_schema: dict[str, Any] | None = None,
    risk_level: str = "low",
) -> Skill:
    runner = SubprocessSkillRunner(
        command=command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        env=env or {},
    )
    return Skill(
        name=name,
        description=description,
        handler=runner.run,
        runtime="subprocess",
        dry_run_only=dry_run_only,
        max_attempts=max_attempts,
        idempotent=idempotent,
        required_sensors=required_sensors or [],
        failure_categories=failure_categories or [],
        allow_real_robot=allow_real_robot,
        timeout_seconds=float(timeout_seconds),
        input_schema=input_schema or dict(GENERIC_INPUT_SCHEMA),
        risk_level=risk_level,
    )
