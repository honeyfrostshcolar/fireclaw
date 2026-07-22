from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.provider.provider import ProviderError
from fireclaw_core.provider.provider_runtime import FallbackSummaryError, ProviderRuntime
from fireclaw_core.task.task_contract import StructuredRobotTask, planning_result_from_structured_task

SAFE_SUPPLEMENTAL_SKILLS = ("report_status", "return_to_safe_zone")
# Used by RobotAgentPolicy (floor-mutation guard) and DeterministicRobotAgentPlanner.
FLOOR_SKILLS = {"navigate_to_floor", "search_for_victims", "assess_victim", "report_status"}


@dataclass(frozen=True)
class RobotAgentTaskEnvelope:
    task_id: str
    mission_id: str | None
    robot_id: str
    command: str | None
    task_type: str
    target: dict[str, Any]
    allowed_skills: list[str]
    required_skills: list[str]
    constraints: dict[str, Any]
    risk_level: str
    operator_id: str | None


@dataclass(frozen=True)
class RobotLocalPlanStep:
    skill_name: str
    inputs: dict[str, Any]
    reason: str | None = None


@dataclass(frozen=True)
class RobotLocalPlan:
    intent: str
    steps: list[RobotLocalPlanStep]
    rationale: str | None = None
    confidence: float | None = None


def envelope_from_structured_task(
    task: StructuredRobotTask,
    *,
    fallback_robot_id: str,
) -> RobotAgentTaskEnvelope:
    base_allowed = task.allowed_skills if task.allowed_skills else task.required_skills
    allowed_skills = list(dict.fromkeys([*base_allowed, *SAFE_SUPPLEMENTAL_SKILLS]))
    return RobotAgentTaskEnvelope(
        task_id=task.task_id,
        mission_id=task.mission_id,
        robot_id=task.robot_id or fallback_robot_id,
        command=task.command,
        task_type=task.task_type,
        target=dict(task.target),
        allowed_skills=allowed_skills,
        required_skills=list(task.required_skills),
        constraints=dict(task.constraints),
        risk_level=task.risk_level,
        operator_id=task.operator_id,
    )


def planning_result_from_local_plan(
    envelope: RobotAgentTaskEnvelope,
    local_plan: RobotLocalPlan,
) -> PlanningResult:
    floor = envelope.target.get("floor")
    target_floor = floor if isinstance(floor, int) else None
    steps = [
        PlanStep(skill_name=step.skill_name, inputs=dict(step.inputs))
        for step in local_plan.steps
    ]
    return PlanningResult(
        status="planned",
        message="Robot-local agent produced an executable plan.",
        intent=local_plan.intent or envelope.task_type,
        target_floor=target_floor,
        plan=Plan(intent=local_plan.intent or envelope.task_type, steps=steps),
    )


@dataclass(frozen=True)
class RobotAgentPolicyDecision:
    """Outcome of a robot-local policy validation.

    ``status`` is one of:
    - ``"allow"`` — plan is safe to execute without further approval.
    - ``"reject"`` — plan violates constraints; ``reasons`` explains why.
    - ``"approval_required"`` — plan is structurally valid but the risk
      level demands operator approval before execution.
    """

    status: Literal["allow", "reject", "approval_required"]
    reasons: list[str]


class RobotAgentPolicy:
    """Validates a robot-local plan against its task envelope.

    Checks (in priority order):
    1. Every planned skill must be in ``envelope.allowed_skills``.
    2. Floor-targeting skills must use the envelope's expected floor.
    3. All ``envelope.required_skills`` must appear in the plan.
    4. High/critical risk levels require operator approval.

    If any of checks 1–3 fail the decision is ``reject``.
    If check 4 is the only remaining concern the decision is
    ``approval_required``.  Otherwise the decision is ``allow``.
    """

    def validate(
        self,
        envelope: RobotAgentTaskEnvelope,
        plan: RobotLocalPlan,
    ) -> RobotAgentPolicyDecision:
        reasons: list[str] = []
        allowed = set(envelope.allowed_skills)
        planned = [step.skill_name for step in plan.steps]

        for skill_name in planned:
            if skill_name not in allowed:
                reasons.append(f"skill {skill_name!r} is outside allowed_skills")

        expected_floor = envelope.target.get("floor")
        if isinstance(expected_floor, int):
            for step in plan.steps:
                if step.skill_name in FLOOR_SKILLS:
                    actual_floor = step.inputs.get("floor")
                    if actual_floor != expected_floor:
                        reasons.append(
                            f"skill {step.skill_name!r} uses floor {actual_floor!r}, expected {expected_floor!r}"
                        )

        if envelope.task_type == "primitive_composition" and not plan.steps:
            reasons.append("primitive composition produced no executable steps")

        for skill_name in envelope.required_skills:
            if skill_name not in planned:
                reasons.append(f"required skill {skill_name!r} is missing")

        if reasons:
            return RobotAgentPolicyDecision(status="reject", reasons=reasons)

        if envelope.risk_level in {"high", "critical"}:
            return RobotAgentPolicyDecision(
                status="approval_required",
                reasons=[f"risk level {envelope.risk_level!r} requires approval before robot-local execution"],
            )

        return RobotAgentPolicyDecision(status="allow", reasons=[])


class RobotAgentPlannerError(Exception):
    pass


ROBOT_LOCAL_PLAN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_robot_local_plan",
        "description": "Create a bounded local execution plan for a firefighting robot.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "skill_name": {"type": "string"},
                            "inputs": {"type": "object"},
                            "reason": {"type": "string"},
                        },
                        "required": ["skill_name", "inputs"],
                    },
                },
                "rationale": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["intent", "steps"],
        },
    },
}


def build_robot_agent_messages(
    envelope: RobotAgentTaskEnvelope,
    *,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    memory_instruction = ""
    if context.get("memory_tools"):
        memory_instruction = (
            "需要现场历史实体信息时，可以先调用一轮只读实体记忆工具，再生成计划。"
            "实体记忆是可能过时的建议性证据，不能替代当前传感器或绕过 SafetyGate。"
        )
    system = (
        "你是消防机器人本地子 agent。"
        "你只能在 allowed_skills 内规划，不能改变 target，不能扩大任务权限。"
        f"{memory_instruction}"
        "请调用 create_robot_local_plan 工具返回结构化局部执行计划。"
    )
    planning_rules = [
        "优先使用可用 composite skill 完成明确的消防任务。",
        "没有合适 composite skill 时，可以组合 primitive skills。",
        "只能使用 allowed_skills 中的技能。",
        "不能扩大目标、楼层、区域、风险级别。",
        "运动类 primitive 必须保持在 target/constraints 允许范围内。",
        "不确定时返回空 steps 并说明需要澄清。",
    ]
    payload = {
        "task": {
            "task_id": envelope.task_id,
            "mission_id": envelope.mission_id,
            "robot_id": envelope.robot_id,
            "command": envelope.command,
            "task_type": envelope.task_type,
            "target": envelope.target,
            "allowed_skills": envelope.allowed_skills,
            "required_skills": envelope.required_skills,
            "constraints": envelope.constraints,
            "risk_level": envelope.risk_level,
        },
        "skill_inventory": context.get("skill_inventory", {}),
        "planning_rules": planning_rules,
        "context": context,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class LLMRobotAgentPlanner:
    def __init__(
        self,
        provider_runtime: ProviderRuntime,
        *,
        memory_tool_executor: Any | None = None,
    ) -> None:
        self._provider_runtime = provider_runtime
        self._memory_tool_executor = memory_tool_executor

    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotLocalPlan:
        if cancellation_requested is not None and cancellation_requested():
            raise RobotAgentPlannerError("planning cancelled before provider call")
        skill_tools = context.get("skill_tools") if isinstance(context, dict) else None
        action_tools = [ROBOT_LOCAL_PLAN_TOOL]
        if isinstance(skill_tools, list):
            action_tools.extend(tool for tool in skill_tools if isinstance(tool, dict))
        memory_tools = context.get("memory_tools") if isinstance(context, dict) else None
        exposed_memory_tools = [
            tool for tool in memory_tools or [] if isinstance(tool, dict)
        ]
        tools = [*action_tools, *exposed_memory_tools]
        messages = build_robot_agent_messages(envelope, context=context)
        try:
            response = self._provider_runtime.chat_completion(
                messages=messages,
                tools=tools,
                temperature=0.0,
                max_tokens=2048,
            )
        except (ProviderError, FallbackSummaryError) as exc:
            raise RobotAgentPlannerError(str(exc)) from exc
        if cancellation_requested is not None and cancellation_requested():
            raise RobotAgentPlannerError("planning cancelled after provider call")
        if not response.tool_calls:
            raise RobotAgentPlannerError("LLM did not return a robot-local plan tool call")
        memory_tool_names = {
            tool["function"]["name"]
            for tool in exposed_memory_tools
            if isinstance(tool.get("function"), dict)
            and isinstance(tool["function"].get("name"), str)
        }
        called_names = {call.name for call in response.tool_calls}
        if called_names & memory_tool_names:
            if not called_names.issubset(memory_tool_names):
                raise RobotAgentPlannerError(
                    "memory queries and action planning cannot be mixed in one tool-call round"
                )
            if self._memory_tool_executor is None or not envelope.mission_id:
                raise RobotAgentPlannerError("entity memory tools are unavailable for this task")
            messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in response.tool_calls
                ],
            })
            for call in response.tool_calls:
                try:
                    result = self._memory_tool_executor(
                        call.name,
                        call.arguments,
                        mission_id=envelope.mission_id,
                    )
                except (TypeError, ValueError) as exc:
                    result = {"status": "error", "message": str(exc), "advisory_only": True}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            messages.append({
                "role": "system",
                "content": (
                    "实体记忆结果仅供参考，可能过时或存在冲突。"
                    "现在必须调用 create_robot_local_plan 或一个允许的动作 skill；"
                    "任何动作仍须服从当前传感器状态、任务约束和 SafetyGate。"
                ),
            })
            if cancellation_requested is not None and cancellation_requested():
                raise RobotAgentPlannerError("planning cancelled after memory query")
            try:
                response = self._provider_runtime.chat_completion(
                    messages=messages,
                    tools=action_tools,
                    temperature=0.0,
                    max_tokens=2048,
                )
            except (ProviderError, FallbackSummaryError) as exc:
                raise RobotAgentPlannerError(str(exc)) from exc
            if cancellation_requested is not None and cancellation_requested():
                raise RobotAgentPlannerError("planning cancelled after final provider call")
            if not response.tool_calls:
                raise RobotAgentPlannerError("LLM did not return a plan after entity memory query")
        tool_call = response.tool_calls[0]
        if tool_call.name == "create_robot_local_plan":
            return _local_plan_from_arguments(tool_call.arguments)
        allowed_direct = {
            tool["function"]["name"]
            for tool in action_tools[1:]
            if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
        }
        direct_calls = [
            {"name": call.name, "arguments": call.arguments}
            for call in response.tool_calls
        ]
        if direct_calls and all(call["name"] in allowed_direct for call in direct_calls):
            from fireclaw_core.agent.robot_tools import local_plan_from_direct_tool_calls
            return local_plan_from_direct_tool_calls(direct_calls, intent=envelope.task_type)
        raise RobotAgentPlannerError(f"unexpected tool call {tool_call.name!r}")


def _local_plan_from_arguments(arguments: dict[str, Any]) -> RobotLocalPlan:
    raw_steps = arguments.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise RobotAgentPlannerError("robot-local plan must contain at least one step")
    steps = []
    for item in raw_steps:
        if not isinstance(item, dict):
            raise RobotAgentPlannerError("robot-local plan step must be an object")
        skill_name = str(item.get("skill_name") or "")
        if not skill_name:
            raise RobotAgentPlannerError("robot-local plan step missing skill_name")
        inputs = item.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise RobotAgentPlannerError("robot-local plan step inputs must be an object")
        reason = item.get("reason")
        steps.append(
            RobotLocalPlanStep(
                skill_name=skill_name,
                inputs=dict(inputs),
                reason=str(reason) if reason is not None else None,
            )
        )
    confidence = arguments.get("confidence")
    return RobotLocalPlan(
        intent=str(arguments.get("intent") or ""),
        steps=steps,
        rationale=str(arguments["rationale"]) if arguments.get("rationale") is not None else None,
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
    )


class DeterministicRobotAgentPlanner:
    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotLocalPlan:
        floor = envelope.target.get("floor")
        steps = []
        for skill_name in envelope.required_skills:
            inputs: dict[str, Any] = {}
            if skill_name in FLOOR_SKILLS and isinstance(floor, int):
                inputs["floor"] = floor
            steps.append(RobotLocalPlanStep(skill_name=skill_name, inputs=inputs))
        return RobotLocalPlan(
            intent=envelope.task_type,
            steps=steps,
            rationale="Deterministic robot-local fallback plan.",
            confidence=1.0,
        )


class RobotAgentRuntime:
    def __init__(
        self,
        *,
        planner: RobotAgentPlanner,
        policy: RobotAgentPolicy | None = None,
    ) -> None:
        self._planner = planner
        self._policy = policy or RobotAgentPolicy()

    def plan_structured_task(
        self,
        task: StructuredRobotTask,
        *,
        fallback_robot_id: str,
        context: dict[str, Any],
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> PlanningResult:
        envelope = envelope_from_structured_task(task, fallback_robot_id=fallback_robot_id)
        self._emit(event_sink, "robot_agent.plan_requested", {"task_id": envelope.task_id, "robot_id": envelope.robot_id})
        try:
            local_plan = self._planner.plan(
                envelope,
                context=context,
                cancellation_requested=cancellation_requested,
            )
        except RobotAgentPlannerError as exc:
            self._emit(event_sink, "robot_agent.plan_failed", {"task_id": envelope.task_id, "message": str(exc)})
            return self._fallback(task, event_sink=event_sink, reason="planner_failed")

        decision = self._policy.validate(envelope, local_plan)
        if decision.status == "allow":
            self._emit(
                event_sink,
                "robot_agent.plan_accepted",
                {
                    "task_id": envelope.task_id,
                    "step_count": len(local_plan.steps),
                    "confidence": local_plan.confidence,
                },
            )
            return planning_result_from_local_plan(envelope, local_plan)
        if decision.status == "approval_required":
            self._emit(
                event_sink,
                "robot_agent.policy_rejected",
                {"task_id": envelope.task_id, "status": decision.status, "reasons": decision.reasons},
            )
            return PlanningResult(
                status="clarify",
                message="Robot-local plan requires approval before execution.",
                intent=envelope.task_type,
            )
        self._emit(
            event_sink,
            "robot_agent.policy_rejected",
            {"task_id": envelope.task_id, "status": decision.status, "reasons": decision.reasons},
        )
        return self._fallback(task, event_sink=event_sink, reason="policy_rejected")

    def _fallback(
        self,
        task: StructuredRobotTask,
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
        reason: str,
    ) -> PlanningResult:
        self._emit(event_sink, "robot_agent.fallback_used", {"task_id": task.task_id, "reason": reason})
        return planning_result_from_structured_task(task)

    @staticmethod
    def _emit(event_sink, event_type: str, payload: dict[str, Any]) -> None:
        if event_sink is not None:
            event_sink(event_type, payload)


class RobotAgentPlanner(Protocol):
    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotLocalPlan:
        ...
