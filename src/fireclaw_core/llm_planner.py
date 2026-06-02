from __future__ import annotations

from typing import Any, Protocol

from fireclaw_core.planner import Plan, PlannerContext, PlanningResult, PlanStep, RuleBasedPlanner
from fireclaw_core.tool_schema import build_planner_request


class PlanningClient(Protocol):
    def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        ...


class LLMToolCallingPlanner:
    def __init__(
        self,
        *,
        client: PlanningClient,
        fallback: RuleBasedPlanner | None = None,
    ) -> None:
        self._client = client
        self._fallback = fallback or RuleBasedPlanner()

    def plan(self, command: str, context: PlannerContext | None = None) -> PlanningResult:
        request = self._build_request(command, context)
        try:
            response = self._client.plan(request)
        except Exception:
            return self._fallback.plan(command, context=context)
        try:
            return self._parse_response(response)
        except ValueError:
            return self._fallback.plan(command, context=context)

    def _build_request(self, command: str, context: PlannerContext | None) -> dict[str, Any]:
        return build_planner_request(command, context)

    def _parse_response(self, response: dict[str, Any]) -> PlanningResult:
        if not isinstance(response, dict):
            raise ValueError("Planner response must be an object.")

        status = response.get("status")
        message = response.get("message")
        if not isinstance(status, str) or not isinstance(message, str):
            raise ValueError("Planner response requires string status and message.")

        if status == "clarify":
            return PlanningResult(status="clarify", message=message)
        if status != "planned":
            raise ValueError(f"Unsupported planner status: {status}")

        intent = response.get("intent")
        if not isinstance(intent, str) or not intent:
            raise ValueError("Planned response requires intent.")

        steps_payload = response.get("steps")
        if not isinstance(steps_payload, list) or not steps_payload:
            raise ValueError("Planned response requires non-empty steps.")

        steps: list[PlanStep] = []
        for step_payload in steps_payload:
            if not isinstance(step_payload, dict):
                raise ValueError("Each planner step must be an object.")
            skill_name = step_payload.get("skill_name")
            inputs = step_payload.get("inputs", {})
            if not isinstance(skill_name, str) or not skill_name:
                raise ValueError("Each planner step requires skill_name.")
            if not isinstance(inputs, dict):
                raise ValueError("Each planner step inputs field must be an object.")
            steps.append(PlanStep(skill_name=skill_name, inputs=inputs))

        target_floor = response.get("target_floor")
        if target_floor is not None and not isinstance(target_floor, int):
            raise ValueError("target_floor must be an integer or null.")

        return PlanningResult(
            status="planned",
            message=message,
            intent=intent,
            target_floor=target_floor,
            plan=Plan(intent=intent, steps=steps),
        )
