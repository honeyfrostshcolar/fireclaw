from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from fireclaw_core.context.manager import (
    ContextBudgetExceeded,
    ContextManagementPolicy,
    ManagedContextResult,
    ModelAwareContextManager,
    TokenCounter,
)
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.provider.provider import ProviderError
from fireclaw_core.provider.provider_runtime import FallbackSummaryError, ProviderRuntime
from fireclaw_core.task.task_contract import StructuredRobotTask, planning_result_from_structured_task

SAFE_SUPPLEMENTAL_SKILLS = ("report_status", "return_to_safe_zone")
# Used by RobotAgentPolicy (floor-mutation guard) and DeterministicRobotAgentPlanner.
FLOOR_SKILLS = {"navigate_to_floor", "search_for_victims", "assess_victim", "report_status"}
POINT_SKILLS = {"navigate_to_point"}


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
    operation_id: str | None = None


@dataclass(frozen=True)
class RobotLocalPlan:
    intent: str
    steps: list[RobotLocalPlanStep]
    rationale: str | None = None
    confidence: float | None = None
    context_manifest: dict[str, Any] | None = None


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
        target_pose=(
            {
                **dict(envelope.target["pose"]),
                "frame_id": str(envelope.target.get("frame_id") or "map"),
            }
            if isinstance(envelope.target.get("pose"), dict)
            else None
        ),
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
        reasons = self._step_errors(envelope, plan.steps)
        planned = [step.skill_name for step in plan.steps]

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

    def validate_step(
        self,
        envelope: RobotAgentTaskEnvelope,
        step: RobotLocalPlanStep,
    ) -> RobotAgentPolicyDecision:
        """Validate one proposed action in a multi-round Robot Agent loop."""

        reasons = self._step_errors(envelope, [step])
        if reasons:
            return RobotAgentPolicyDecision(status="reject", reasons=reasons)
        if envelope.risk_level in {"high", "critical"}:
            return RobotAgentPolicyDecision(
                status="approval_required",
                reasons=[
                    f"risk level {envelope.risk_level!r} requires approval "
                    "before robot-local execution"
                ],
            )
        return RobotAgentPolicyDecision(status="allow", reasons=[])

    @staticmethod
    def _step_errors(
        envelope: RobotAgentTaskEnvelope,
        steps: list[RobotLocalPlanStep],
    ) -> list[str]:
        reasons: list[str] = []
        allowed = set(envelope.allowed_skills)
        expected_floor = envelope.target.get("floor")
        expected_pose = envelope.target.get("pose")
        expected_frame = envelope.target.get("frame_id", "map")
        for step in steps:
            if step.skill_name not in allowed:
                reasons.append(
                    f"skill {step.skill_name!r} is outside allowed_skills"
                )
            if (
                isinstance(expected_floor, int)
                and step.skill_name in FLOOR_SKILLS
                and step.inputs.get("floor") != expected_floor
            ):
                reasons.append(
                    f"skill {step.skill_name!r} uses floor "
                    f"{step.inputs.get('floor')!r}, expected {expected_floor!r}"
                )
            if step.skill_name in POINT_SKILLS:
                if not isinstance(expected_pose, dict):
                    reasons.append(
                        f"skill {step.skill_name!r} requires target.pose"
                    )
                    continue
                for axis in ("x", "y"):
                    if step.inputs.get(axis) != expected_pose.get(axis):
                        reasons.append(
                            f"skill {step.skill_name!r} uses {axis} "
                            f"{step.inputs.get(axis)!r}, expected "
                            f"{expected_pose.get(axis)!r}"
                        )
                expected_yaw = expected_pose.get("yaw", 0.0)
                if step.inputs.get("yaw", 0.0) != expected_yaw:
                    reasons.append(
                        f"skill {step.skill_name!r} uses yaw "
                        f"{step.inputs.get('yaw', 0.0)!r}, expected "
                        f"{expected_yaw!r}"
                    )
                if step.inputs.get("frame_id", "map") != expected_frame:
                    reasons.append(
                        f"skill {step.skill_name!r} uses frame_id "
                        f"{step.inputs.get('frame_id', 'map')!r}, expected "
                        f"{expected_frame!r}"
                    )
        return reasons


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
    planning_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    memory_instruction = ""
    if context.get("memory_tools"):
        memory_instruction = (
            "需要现场历史实体信息时，可以先调用一轮只读实体记忆工具，再生成计划。"
            "实体记忆是可能过时的建议性证据，不能替代当前传感器或绕过 SafetyGate。"
        )
    system = (
        "你是消防机器人本地 Robot Agent。"
        "你只能在 allowed_skills 内规划，不能改变 target，不能扩大任务权限。"
        f"{memory_instruction}"
        "planning_context.authoritative 是本轮权威任务和本机状态；"
        "planning_context.advisory 是可能过时的历史或记忆，不能覆盖当前状态。"
        "请调用 create_robot_local_plan 工具返回结构化局部执行计划。"
    )
    planning_rules = [
        "优先使用可用 composite skill 完成明确的消防任务。",
        "没有合适 composite skill 时，可以组合 primitive skills。",
        "只能使用 allowed_skills 中的技能。",
        "不能扩大目标、坐标范围、区域、风险级别。",
        "运动类 primitive 必须保持在 target/constraints 允许范围内。",
        "不确定时返回空 steps 并说明需要澄清。",
    ]
    if planning_context is not None:
        payload = {
            "planning_context": planning_context,
            "planning_rules": planning_rules,
        }
    else:
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
        context_manager: ModelAwareContextManager | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._provider_runtime = provider_runtime
        self._memory_tool_executor = memory_tool_executor
        self._context_manager = (
            context_manager
            or ModelAwareContextManager(
                runtime=provider_runtime,
                task="robot_local_planning",
                policy=ContextManagementPolicy(
                    output_reserve_tokens=2048,
                ),
                token_counter=token_counter,
            )
        )

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
        try:
            managed = self._fit_context(
                envelope,
                context=context,
                tools=tools,
            )
        except ContextBudgetExceeded as exc:
            raise RobotAgentPlannerError(str(exc)) from exc
        messages = managed.messages
        try:
            response = self._provider_runtime.chat_completion(
                messages=messages,
                tools=managed.tools,
                temperature=0.0,
                max_tokens=managed.manifest.output_reserve_tokens,
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
            memory_results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                try:
                    result = self._memory_tool_executor(
                        call.name,
                        call.arguments,
                        mission_id=envelope.mission_id,
                    )
                except (TypeError, ValueError) as exc:
                    result = {"status": "error", "message": str(exc), "advisory_only": True}
                memory_results.append({
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "result": result,
                    "advisory_only": True,
                })
            if cancellation_requested is not None and cancellation_requested():
                raise RobotAgentPlannerError("planning cancelled after memory query")
            try:
                managed = self._fit_context(
                    envelope,
                    context=context,
                    tools=action_tools,
                    memory_results=memory_results,
                )
                response = self._provider_runtime.chat_completion(
                    messages=managed.messages,
                    tools=managed.tools,
                    temperature=0.0,
                    max_tokens=(
                        managed.manifest.output_reserve_tokens
                    ),
                )
            except (
                ContextBudgetExceeded,
                ProviderError,
                FallbackSummaryError,
            ) as exc:
                raise RobotAgentPlannerError(str(exc)) from exc
            if cancellation_requested is not None and cancellation_requested():
                raise RobotAgentPlannerError("planning cancelled after final provider call")
            if not response.tool_calls:
                raise RobotAgentPlannerError("LLM did not return a plan after entity memory query")
        tool_call = response.tool_calls[0]
        if tool_call.name == "create_robot_local_plan":
            return replace(
                _local_plan_from_arguments(tool_call.arguments),
                context_manifest=managed.manifest.to_dict(),
            )
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
            return replace(
                local_plan_from_direct_tool_calls(
                    direct_calls,
                    intent=envelope.task_type,
                ),
                context_manifest=managed.manifest.to_dict(),
            )
        raise RobotAgentPlannerError(f"unexpected tool call {tool_call.name!r}")

    def _fit_context(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        memory_results: list[dict[str, Any]] | None = None,
    ) -> ManagedContextResult:
        authoritative = {
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
            "robot_state": context.get("robot_state"),
            "environment_state": context.get("environment_state"),
            "available_sensors": context.get(
                "available_sensors",
                [],
            ),
            "skill_inventory": context.get("skill_inventory", {}),
            "skill_metadata": context.get("skill_metadata", []),
        }
        advisory = {
            "session_history": [
                dict(item)
                for item in context.get("session_history", [])
                if isinstance(item, dict)
            ],
            "entity_memory_results": list(memory_results or []),
        }
        context_id = (
            f"{envelope.mission_id or 'local'}:"
            f"{envelope.task_id}:robot-context"
        )

        def build_request(
            candidate_authoritative: dict[str, Any],
            continuity: dict[str, Any],
            candidate_advisory: dict[str, list[dict[str, Any]]],
            context_policy: dict[str, Any],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            planning_context = {
                "context_id": context_id,
                "authoritative": candidate_authoritative,
                "continuity": continuity,
                "advisory": candidate_advisory,
                "context_policy": context_policy,
            }
            return (
                build_robot_agent_messages(
                    envelope,
                    context=context,
                    planning_context=planning_context,
                ),
                tools,
            )

        return self._context_manager.fit(
            scope="robot_local_planner",
            context_id=context_id,
            authoritative=authoritative,
            continuity={
                "mission_id": envelope.mission_id,
                "task_id": envelope.task_id,
            },
            advisory=advisory,
            build_request=build_request,
            compact_sections=(
                "session_history",
                "entity_memory_results",
            ),
        )


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
        pose = envelope.target.get("pose")
        steps = []
        for skill_name in envelope.required_skills:
            inputs: dict[str, Any] = {}
            if skill_name in FLOOR_SKILLS and isinstance(floor, int):
                inputs["floor"] = floor
            if skill_name in POINT_SKILLS and isinstance(pose, dict):
                inputs.update({
                    "x": pose.get("x"),
                    "y": pose.get("y"),
                    "yaw": pose.get("yaw", 0.0),
                    "frame_id": envelope.target.get("frame_id", "map"),
                })
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
                    "context_manifest": local_plan.context_manifest,
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
