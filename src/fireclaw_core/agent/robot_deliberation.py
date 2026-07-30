"""Bounded tool-result deliberation for a persistent Robot Agent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, Protocol

from fireclaw_core.agent.bounded_loop import (
    AgentLoopLimits,
    AgentLoopResult,
    AgentLoopTransition,
    AgentLoopTurn,
    BoundedAgentLoop,
)
from fireclaw_core.agent.harness import (
    AgentHarness,
    AgentHarnessAttempt,
    AgentHarnessError,
    ProviderAgentHarness,
    register_agent_harness,
)
from fireclaw_core.agent.loop_checkpoint import (
    AgentLoopCheckpoint,
    AgentLoopCheckpointStore,
    AgentLoopPendingOperation,
)
from fireclaw_core.agent.robot_agent import (
    RobotAgentPlannerError,
    RobotAgentPolicy,
    RobotAgentTaskEnvelope,
    RobotLocalPlanStep,
    envelope_from_structured_task,
)
from fireclaw_core.context.manager import (
    ContextManagementPolicy,
    ModelAwareContextManager,
    TokenCounter,
)
from fireclaw_core.provider.provider_runtime import ProviderRuntime
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.task.task_contract import StructuredRobotTask


ROBOT_TASK_COMPLETE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "complete_robot_task",
        "description": (
            "Declare the assigned robot task complete after required skills "
            "have succeeded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["message"],
        },
    },
}

ROBOT_TASK_BLOCKED_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_robot_task_blocked",
        "description": (
            "Report that the assigned task cannot continue within its current "
            "target, authority, skills, or safety constraints."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "reason_code": {"type": "string"},
            },
            "required": ["reason", "reason_code"],
        },
    },
}

ROBOT_TASK_ESCALATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "escalate_robot_task",
        "description": (
            "Request Mission Coordinator or operator intervention without "
            "expanding local robot authority."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "reason_code": {"type": "string"},
            },
            "required": ["reason", "reason_code"],
        },
    },
}


RobotAgentOperation = Literal[
    "execute_skill",
    "execute_agent_tool",
    "query_context",
    "complete",
    "blocked",
    "escalate",
]


@dataclass(frozen=True)
class RobotAgentDecision:
    operation: RobotAgentOperation
    message: str
    tool_name: str | None = None
    inputs: dict[str, Any] | None = None
    tool_effect: str | None = None
    reason_code: str | None = None
    evidence_ids: tuple[str, ...] = ()
    context_manifest: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "message": self.message,
            "tool_name": self.tool_name,
            "inputs": dict(self.inputs or {}),
            "tool_effect": self.tool_effect,
            "reason_code": self.reason_code,
            "evidence_ids": list(self.evidence_ids),
            "context_manifest": (
                dict(self.context_manifest)
                if self.context_manifest is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RobotAgentDecision:
        operation = str(value.get("operation") or "")
        if operation not in {
            "execute_skill",
            "execute_agent_tool",
            "query_context",
            "complete",
            "blocked",
            "escalate",
        }:
            raise ValueError(
                f"Unsupported Robot Agent operation: {operation!r}"
            )
        inputs = value.get("inputs")
        manifest = value.get("context_manifest")
        evidence_ids = value.get("evidence_ids")
        return cls(
            operation=operation,  # type: ignore[arg-type]
            message=str(value.get("message") or ""),
            tool_name=(
                value["tool_name"]
                if isinstance(value.get("tool_name"), str)
                else None
            ),
            inputs=dict(inputs) if isinstance(inputs, dict) else None,
            tool_effect=(
                value["tool_effect"]
                if isinstance(value.get("tool_effect"), str)
                else None
            ),
            reason_code=(
                value["reason_code"]
                if isinstance(value.get("reason_code"), str)
                else None
            ),
            evidence_ids=tuple(
                str(item)
                for item in (
                    evidence_ids if isinstance(evidence_ids, list) else []
                )
                if isinstance(item, str) and item
            ),
            context_manifest=(
                dict(manifest) if isinstance(manifest, dict) else None
            ),
        )


@dataclass(frozen=True)
class RobotAgentExecutionObservation:
    iteration: int
    operation: str
    status: str
    message: str
    tool_name: str | None = None
    inputs: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    reason_code: str | None = None
    authoritative: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "operation": self.operation,
            "status": self.status,
            "message": self.message,
            "tool_name": self.tool_name,
            "inputs": dict(self.inputs or {}),
            "output": dict(self.output or {}),
            "reason_code": self.reason_code,
            "authoritative": self.authoritative,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> RobotAgentExecutionObservation:
        inputs = value.get("inputs")
        output = value.get("output")
        return cls(
            iteration=int(value.get("iteration") or 0),
            operation=str(value.get("operation") or ""),
            status=str(value.get("status") or ""),
            message=str(value.get("message") or ""),
            tool_name=(
                value["tool_name"]
                if isinstance(value.get("tool_name"), str)
                else None
            ),
            inputs=dict(inputs) if isinstance(inputs, dict) else None,
            output=dict(output) if isinstance(output, dict) else None,
            reason_code=(
                value["reason_code"]
                if isinstance(value.get("reason_code"), str)
                else None
            ),
            authoritative=bool(value.get("authoritative", True)),
        )


@dataclass(frozen=True)
class RobotAgentDeliberationRequest:
    envelope: RobotAgentTaskEnvelope
    iteration: int
    remaining_iterations: int
    context: dict[str, Any]
    observations: tuple[RobotAgentExecutionObservation, ...]


class RobotAgentDecisionPolicy(Protocol):
    def decide(
        self,
        request: RobotAgentDeliberationRequest,
    ) -> RobotAgentDecision:
        ...


@dataclass(frozen=True)
class RobotAgentDeliberationLimits:
    max_iterations: int = 8
    timeout_seconds: float = 30.0
    max_skill_executions: int = 6
    max_context_queries: int = 2
    max_agent_tool_executions: int = 4

    def __post_init__(self) -> None:
        AgentLoopLimits(
            max_iterations=self.max_iterations,
            timeout_seconds=self.timeout_seconds,
        )
        if self.max_skill_executions <= 0:
            raise ValueError("max_skill_executions must be positive")
        if self.max_context_queries < 0:
            raise ValueError("max_context_queries must be non-negative")
        if self.max_agent_tool_executions < 0:
            raise ValueError(
                "max_agent_tool_executions must be non-negative"
            )

    def loop_limits(self) -> AgentLoopLimits:
        return AgentLoopLimits(
            max_iterations=self.max_iterations,
            timeout_seconds=self.timeout_seconds,
        )


class LLMRobotAgentDecisionPolicy:
    """Choose one Robot Agent operation per model turn."""

    def __init__(
        self,
        provider_runtime: ProviderRuntime,
        *,
        context_manager: ModelAwareContextManager | None = None,
        token_counter: TokenCounter | None = None,
        agent_harness: AgentHarness | None = None,
        plugin_host: FireClawPluginHost | None = None,
    ) -> None:
        self._provider_runtime = provider_runtime
        self._context_manager = (
            context_manager
            or ModelAwareContextManager(
                runtime=provider_runtime,
                task="robot_local_deliberation",
                policy=ContextManagementPolicy(output_reserve_tokens=2048),
                token_counter=token_counter,
            )
        )
        self._agent_harness = agent_harness or ProviderAgentHarness(
            provider_runtime=provider_runtime,
            context_manager=self._context_manager,
            harness_id="fireclaw.provider.robot-deliberation",
        )
        self.plugin_host = plugin_host or FireClawPluginHost()
        register_agent_harness(
            self.plugin_host,
            self._agent_harness,
            owner_plugin_id="fireclaw.agent-harness.robot-deliberation",
        )

    def decide(
        self,
        request: RobotAgentDeliberationRequest,
    ) -> RobotAgentDecision:
        context = request.context
        skill_tools = [
            item
            for item in context.get("skill_tools", [])
            if isinstance(item, dict)
        ]
        memory_tools = [
            item
            for item in context.get("memory_tools", [])
            if isinstance(item, dict)
        ]
        agent_tools = [
            item
            for item in context.get("agent_tools", [])
            if isinstance(item, dict)
        ]
        tools = [
            *skill_tools,
            *memory_tools,
            *agent_tools,
            ROBOT_TASK_COMPLETE_TOOL,
            ROBOT_TASK_BLOCKED_TOOL,
            ROBOT_TASK_ESCALATE_TOOL,
        ]
        skill_names = _tool_names(skill_tools)
        memory_names = _tool_names(memory_tools)
        agent_tool_names = _tool_names(agent_tools)
        overlap = (
            (skill_names & memory_names)
            | (skill_names & agent_tool_names)
            | (memory_names & agent_tool_names)
        )
        if overlap:
            raise RobotAgentPlannerError(
                "Robot Agent tool namespaces overlap: "
                f"{sorted(overlap)}"
            )
        try:
            attempt = self._build_harness_attempt(request, tools=tools)
            harness_result = self._agent_harness.run_attempt(
                attempt,
            )
        except AgentHarnessError as exc:
            raise RobotAgentPlannerError(str(exc)) from exc
        calls = list(harness_result.tool_calls)
        call = calls[0]
        manifest = harness_result.context_manifest
        if call.name in skill_names:
            return RobotAgentDecision(
                operation="execute_skill",
                message=f"Execute Robot Agent skill {call.name}.",
                tool_name=call.name,
                inputs=dict(call.arguments),
                context_manifest=manifest,
            )
        if call.name in memory_names:
            return RobotAgentDecision(
                operation="query_context",
                message=f"Query advisory Robot Agent context using {call.name}.",
                tool_name=call.name,
                inputs=dict(call.arguments),
                context_manifest=manifest,
            )
        if call.name in agent_tool_names:
            return RobotAgentDecision(
                operation="execute_agent_tool",
                message=f"Execute admitted Agent Tool {call.name}.",
                tool_name=call.name,
                inputs=dict(call.arguments),
                tool_effect=_agent_tool_effect(context, call.name),
                context_manifest=manifest,
            )
        if call.name == "complete_robot_task":
            return RobotAgentDecision(
                operation="complete",
                message=str(
                    call.arguments.get("message")
                    or "Robot Agent reports task completion."
                ),
                evidence_ids=tuple(
                    str(item)
                    for item in call.arguments.get("evidence_ids", [])
                    if isinstance(item, str) and item
                ),
                context_manifest=manifest,
            )
        if call.name == "report_robot_task_blocked":
            return RobotAgentDecision(
                operation="blocked",
                message=str(
                    call.arguments.get("reason")
                    or "Robot Agent reports that the task is blocked."
                ),
                reason_code=str(
                    call.arguments.get("reason_code") or "robot_task_blocked"
                ),
                context_manifest=manifest,
            )
        if call.name == "escalate_robot_task":
            return RobotAgentDecision(
                operation="escalate",
                message=str(
                    call.arguments.get("reason")
                    or "Robot Agent requests intervention."
                ),
                reason_code=str(
                    call.arguments.get("reason_code")
                    or "robot_task_escalation"
                ),
                context_manifest=manifest,
            )
        raise RobotAgentPlannerError(
            f"Robot Agent returned unexpected operation {call.name!r}"
        )

    def _build_harness_attempt(
        self,
        request: RobotAgentDeliberationRequest,
        *,
        tools: list[dict[str, Any]],
    ) -> AgentHarnessAttempt:
        envelope = request.envelope
        execution_observations = [
            item.to_dict()
            for item in request.observations
            if item.authoritative
        ]
        advisory_observations = [
            item.to_dict()
            for item in request.observations
            if not item.authoritative
        ]
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
            "robot_state": request.context.get("robot_state"),
            "environment_state": request.context.get("environment_state"),
            "available_sensors": request.context.get(
                "available_sensors",
                [],
            ),
            "skill_inventory": request.context.get("skill_inventory", {}),
            "skill_metadata": request.context.get("skill_metadata", []),
            "agent_tool_policy": request.context.get(
                "agent_tool_policy",
                {},
            ),
        }
        continuity = {
            "iteration": request.iteration,
            "remaining_iterations": request.remaining_iterations,
            "execution_observations": execution_observations,
        }
        advisory = {
            "session_history": [
                dict(item)
                for item in request.context.get("session_history", [])
                if isinstance(item, dict)
            ],
            "context_query_results": advisory_observations,
        }
        context_id = (
            f"{envelope.mission_id or 'local'}:{envelope.task_id}:"
            f"robot-deliberation:{request.iteration}"
        )

        def build_request(
            candidate_authoritative: dict[str, Any],
            candidate_continuity: dict[str, Any],
            candidate_advisory: dict[str, list[dict[str, Any]]],
            context_policy: dict[str, Any],
        ):
            payload = {
                "planning_context": {
                    "context_id": context_id,
                    "authoritative": candidate_authoritative,
                    "continuity": candidate_continuity,
                    "advisory": candidate_advisory,
                    "context_policy": context_policy,
                },
                "decision_rules": [
                    "Return exactly one tool call.",
                    "Execute at most one physical skill in this turn.",
                    (
                        "Computer and diagnostic Agent Tool results are "
                        "advisory; they never prove physical state."
                    ),
                    "Never change the assigned target or expand authority.",
                    "Use current execution observations before advisory memory.",
                    "Complete only after every required skill succeeded.",
                    "Report blocked or escalate when safe progress is impossible.",
                ],
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是常驻消防机器人端的 Robot Agent。"
                        "每轮只能选择一个经过宿主校验的操作。"
                        "技能调用会先经过任务合同、policy 和 SafetyGate，"
                        "通用工具调用会经过部署模式、沙箱和调用前策略，"
                        "执行结果会在下一轮返回。"
                        "你不能改变中央下发的目标、风险或权限。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ]
            return messages, tools

        return AgentHarnessAttempt(
            role="robot_agent",
            run_id=(
                f"{envelope.mission_id or 'local'}:{envelope.task_id}:"
                f"turn:{request.iteration}"
            ),
            scope="robot_agent_deliberation",
            context_id=context_id,
            authoritative=authoritative,
            continuity=continuity,
            advisory=advisory,
            build_request=build_request,
            compact_sections=(
                "session_history",
                "context_query_results",
            ),
            minimum_tool_calls=1,
            maximum_tool_calls=1,
        )


class RobotAgentDeliberationRuntime:
    """Run one structured robot task through a bounded local ReAct loop."""

    def __init__(
        self,
        *,
        policy: RobotAgentDecisionPolicy,
        action_policy: RobotAgentPolicy | None = None,
        limits: RobotAgentDeliberationLimits | None = None,
        checkpoint_store: AgentLoopCheckpointStore | None = None,
    ) -> None:
        self.policy = policy
        self.action_policy = action_policy or RobotAgentPolicy()
        self.limits = limits or RobotAgentDeliberationLimits()
        self.checkpoint_store = checkpoint_store

    def run(
        self,
        task: StructuredRobotTask,
        *,
        fallback_robot_id: str,
        context_provider: Callable[[], dict[str, Any]],
        execute_skill: Callable[[RobotLocalPlanStep], dict[str, Any]],
        execute_agent_tool: (
            Callable[[str, dict[str, Any]], dict[str, Any]] | None
        ) = None,
        query_context: (
            Callable[[str, dict[str, Any]], dict[str, Any]] | None
        ) = None,
        reconcile_skill: (
            Callable[[str, RobotLocalPlanStep], dict[str, Any] | None] | None
        ) = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> AgentLoopResult[RobotAgentExecutionObservation, dict[str, Any]]:
        envelope = envelope_from_structured_task(
            task,
            fallback_robot_id=fallback_robot_id,
        )
        contract_hash = _task_contract_hash(task)
        checkpoint_key = (
            f"robot:{envelope.robot_id}:task:{envelope.task_id}"
        )
        run_id = checkpoint_key
        resume_checkpoint: AgentLoopCheckpoint | None = None
        adapter_state: dict[str, Any] = {}
        if self.checkpoint_store is not None:
            latest = self.checkpoint_store.latest(checkpoint_key)
            if latest is not None and latest.is_recoverable:
                adapter_state = dict(latest.adapter_state or {})
                if (
                    adapter_state.get("task_contract_hash") != contract_hash
                    or adapter_state.get("task_id") != envelope.task_id
                    or adapter_state.get("robot_id") != envelope.robot_id
                ):
                    raise RobotAgentPlannerError(
                        "Robot Agent checkpoint does not match the task "
                        "contract or robot identity"
                    )
                resume_checkpoint = latest
        succeeded_skills = {
            str(item)
            for item in adapter_state.get("succeeded_skills", [])
            if isinstance(item, str) and item
        }
        skill_execution_count = int(
            adapter_state.get("skill_execution_count") or 0
        )
        context_query_count = int(
            adapter_state.get("context_query_count") or 0
        )
        agent_tool_execution_count = int(
            adapter_state.get("agent_tool_execution_count") or 0
        )
        latest_context: dict[str, Any] = {}

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            if event_sink is not None:
                event_sink(event_type, payload)

        def current_adapter_state() -> dict[str, Any]:
            return {
                "task_contract_hash": contract_hash,
                "task_id": envelope.task_id,
                "mission_id": envelope.mission_id,
                "robot_id": envelope.robot_id,
                "succeeded_skills": sorted(succeeded_skills),
                "skill_execution_count": skill_execution_count,
                "context_query_count": context_query_count,
                "agent_tool_execution_count": agent_tool_execution_count,
            }

        def decide(
            turn: AgentLoopTurn[RobotAgentExecutionObservation],
        ) -> RobotAgentDecision:
            nonlocal latest_context
            latest_context = context_provider()
            request = RobotAgentDeliberationRequest(
                envelope=envelope,
                iteration=turn.iteration,
                remaining_iterations=turn.remaining_iterations,
                context=latest_context,
                observations=turn.observations,
            )
            decision = self.policy.decide(request)
            emit(
                "robot_agent.decision",
                {
                    "iteration": turn.iteration,
                    "operation": decision.operation,
                    "tool_name": decision.tool_name,
                    "message": decision.message,
                    "reason_code": decision.reason_code,
                    "context_manifest": decision.context_manifest,
                },
            )
            return decision

        def skill_transition(
            *,
            decision: RobotAgentDecision,
            turn: AgentLoopTurn[RobotAgentExecutionObservation],
            step: RobotLocalPlanStep,
            output: dict[str, Any],
            reconciled: bool = False,
        ) -> AgentLoopTransition[
            RobotAgentExecutionObservation,
            dict[str, Any],
        ]:
            nonlocal skill_execution_count
            skill_execution_count += 1
            safety = output.get("safety")
            safety_status = (
                safety.get("status")
                if isinstance(safety, dict)
                else None
            )
            execution = output.get("execution")
            execution_status = (
                execution.get("status")
                if isinstance(execution, dict)
                else None
            )
            successful = execution_status == "succeeded"
            if successful:
                succeeded_skills.add(step.skill_name)
            observation = RobotAgentExecutionObservation(
                iteration=turn.iteration,
                operation=decision.operation,
                status=(
                    "succeeded"
                    if successful
                    else str(
                        execution_status
                        or output.get("status")
                        or "failed"
                    )
                ),
                message=decision.message,
                tool_name=step.skill_name,
                inputs=step.inputs,
                output=output,
                reason_code=(
                    None if successful else _execution_reason_code(output)
                ),
            )
            emit("robot_agent.observation", observation.to_dict())
            if reconciled:
                emit(
                    "robot_agent.pending_operation_reconciled",
                    {
                        "operation_id": step.operation_id,
                        "status": observation.status,
                        "tool_name": step.skill_name,
                    },
                )
            if output.get("status") == "cancelled":
                return AgentLoopTransition(
                    status="cancelled",
                    operation=decision.operation,
                    message="Robot Agent skill execution was cancelled.",
                    observation=observation,
                    result=output,
                    reason_code="cancelled",
                )
            if safety_status != "allow":
                terminal_status = (
                    "escalated"
                    if safety_status == "require_confirmation"
                    else "blocked"
                )
                return AgentLoopTransition(
                    status=terminal_status,
                    operation=decision.operation,
                    message=(
                        "Robot Agent skill was stopped by the local "
                        f"SafetyGate with status {safety_status!r}."
                    ),
                    observation=observation,
                    result=output,
                    reason_code="safety_gate_terminal",
                )
            return AgentLoopTransition.continuing(
                operation=decision.operation,
                message=observation.message,
                observation=observation,
                result=output,
                reason_code=observation.reason_code,
            )

        def execute(
            decision: RobotAgentDecision,
            turn: AgentLoopTurn[RobotAgentExecutionObservation],
        ) -> AgentLoopTransition[RobotAgentExecutionObservation, dict[str, Any]]:
            nonlocal skill_execution_count
            nonlocal context_query_count
            nonlocal agent_tool_execution_count
            if decision.operation == "query_context":
                if (
                    query_context is None
                    or decision.tool_name is None
                    or context_query_count >= self.limits.max_context_queries
                ):
                    observation = RobotAgentExecutionObservation(
                        iteration=turn.iteration,
                        operation=decision.operation,
                        status="rejected",
                        message="Robot Agent context query is unavailable or exhausted.",
                        tool_name=decision.tool_name,
                        inputs=decision.inputs,
                        reason_code="context_query_limit",
                        authoritative=False,
                    )
                else:
                    context_query_count += 1
                    output = query_context(
                        decision.tool_name,
                        dict(decision.inputs or {}),
                    )
                    observation = RobotAgentExecutionObservation(
                        iteration=turn.iteration,
                        operation=decision.operation,
                        status=str(output.get("status") or "observed"),
                        message=decision.message,
                        tool_name=decision.tool_name,
                        inputs=decision.inputs,
                        output=output,
                        authoritative=False,
                    )
                emit("robot_agent.observation", observation.to_dict())
                return AgentLoopTransition.continuing(
                    operation=decision.operation,
                    message=observation.message,
                    observation=observation,
                    reason_code=observation.reason_code,
                )

            if decision.operation == "execute_agent_tool":
                if (
                    execute_agent_tool is None
                    or decision.tool_name is None
                    or agent_tool_execution_count
                    >= self.limits.max_agent_tool_executions
                ):
                    observation = RobotAgentExecutionObservation(
                        iteration=turn.iteration,
                        operation=decision.operation,
                        status="rejected",
                        message=(
                            "Robot Agent Tool execution is unavailable or "
                            "exhausted."
                        ),
                        tool_name=decision.tool_name,
                        inputs=decision.inputs,
                        reason_code="agent_tool_execution_limit",
                        authoritative=False,
                    )
                else:
                    agent_tool_execution_count += 1
                    output = execute_agent_tool(
                        decision.tool_name,
                        dict(decision.inputs or {}),
                    )
                    observation = RobotAgentExecutionObservation(
                        iteration=turn.iteration,
                        operation=decision.operation,
                        status=str(output.get("status") or "error"),
                        message=decision.message,
                        tool_name=decision.tool_name,
                        inputs=decision.inputs,
                        output=output,
                        reason_code=(
                            str(output["error_code"])
                            if isinstance(output.get("error_code"), str)
                            else None
                        ),
                        authoritative=False,
                    )
                emit("robot_agent.observation", observation.to_dict())
                if observation.status == "approval_required":
                    return AgentLoopTransition(
                        status="escalated",
                        operation=decision.operation,
                        message=(
                            "Robot Agent Tool requires exact backend "
                            "authorization before execution."
                        ),
                        observation=observation,
                        reason_code="agent_tool_approval_required",
                    )
                return AgentLoopTransition.continuing(
                    operation=decision.operation,
                    message=observation.message,
                    observation=observation,
                    reason_code=observation.reason_code,
                )

            if decision.operation == "execute_skill":
                if (
                    decision.tool_name is None
                    or skill_execution_count
                    >= self.limits.max_skill_executions
                ):
                    return AgentLoopTransition(
                        status="blocked",
                        operation=decision.operation,
                        message="Robot Agent skill execution limit was exceeded.",
                        reason_code="skill_execution_limit",
                    )
                step = RobotLocalPlanStep(
                    skill_name=decision.tool_name,
                    inputs=dict(decision.inputs or {}),
                    reason=decision.message,
                    operation_id=turn.operation_id,
                )
                policy_decision = self.action_policy.validate_step(
                    envelope,
                    step,
                )
                if policy_decision.status != "allow":
                    observation = RobotAgentExecutionObservation(
                        iteration=turn.iteration,
                        operation=decision.operation,
                        status=policy_decision.status,
                        message="; ".join(policy_decision.reasons),
                        tool_name=step.skill_name,
                        inputs=step.inputs,
                        reason_code="action_policy_rejected",
                    )
                    emit("robot_agent.observation", observation.to_dict())
                    terminal_status = (
                        "escalated"
                        if policy_decision.status == "approval_required"
                        else "blocked"
                    )
                    return AgentLoopTransition(
                        status=terminal_status,
                        operation=decision.operation,
                        message=observation.message,
                        observation=observation,
                        reason_code=observation.reason_code,
                    )
                output = execute_skill(step)
                return skill_transition(
                    decision=decision,
                    turn=turn,
                    step=step,
                    output=output,
                )

            if decision.operation == "complete":
                missing = [
                    name
                    for name in envelope.required_skills
                    if name not in succeeded_skills
                ]
                if missing:
                    observation = RobotAgentExecutionObservation(
                        iteration=turn.iteration,
                        operation=decision.operation,
                        status="rejected",
                        message=(
                            "Completion rejected; required skills have not "
                            f"succeeded: {missing}."
                        ),
                        reason_code="required_skills_incomplete",
                    )
                    emit("robot_agent.observation", observation.to_dict())
                    return AgentLoopTransition.continuing(
                        operation=decision.operation,
                        message=observation.message,
                        observation=observation,
                        reason_code=observation.reason_code,
                    )
                return AgentLoopTransition(
                    status="completed",
                    operation=decision.operation,
                    message=decision.message,
                    result={
                        "status": "completed",
                        "succeeded_skills": sorted(succeeded_skills),
                        "evidence_ids": list(decision.evidence_ids),
                        "final_context": latest_context,
                    },
                )

            if decision.operation == "blocked":
                return AgentLoopTransition(
                    status="blocked",
                    operation=decision.operation,
                    message=decision.message,
                    reason_code=decision.reason_code or "robot_task_blocked",
                )
            if decision.operation == "escalate":
                return AgentLoopTransition(
                    status="escalated",
                    operation=decision.operation,
                    message=decision.message,
                    reason_code=(
                        decision.reason_code or "robot_task_escalation"
                    ),
                )
            return AgentLoopTransition(
                status="blocked",
                operation=str(decision.operation),
                message="Robot Agent returned an unsupported operation.",
                reason_code="unsupported_operation",
            )

        def reconcile_pending(
            pending: AgentLoopPendingOperation,
            turn: AgentLoopTurn[RobotAgentExecutionObservation],
        ) -> AgentLoopTransition[
            RobotAgentExecutionObservation,
            dict[str, Any],
        ]:
            decision = RobotAgentDecision.from_dict(pending.decision)
            if decision.operation == "execute_agent_tool":
                emit(
                    "robot_agent.pending_operation_unresolved",
                    {
                        "operation_id": pending.operation_id,
                        "tool_name": decision.tool_name,
                        "reason_code": "agent_tool_effect_outcome_unknown",
                    },
                )
                return AgentLoopTransition(
                    status="escalated",
                    operation=pending.operation,
                    message=(
                        "A side-effecting Agent Tool may have run before "
                        "restart; automatic replay is prohibited."
                    ),
                    reason_code="agent_tool_effect_outcome_unknown",
                )
            if (
                decision.operation != "execute_skill"
                or decision.tool_name is None
            ):
                return AgentLoopTransition(
                    status="blocked",
                    operation=pending.operation,
                    message=(
                        "Pending Robot Agent operation is not a valid "
                        "physical skill decision."
                    ),
                    reason_code="invalid_pending_operation",
                )
            step = RobotLocalPlanStep(
                skill_name=decision.tool_name,
                inputs=dict(decision.inputs or {}),
                reason=decision.message,
                operation_id=pending.operation_id,
            )
            if reconcile_skill is None:
                emit(
                    "robot_agent.pending_operation_unresolved",
                    {
                        "operation_id": pending.operation_id,
                        "tool_name": step.skill_name,
                        "reason_code": "physical_action_outcome_unknown",
                    },
                )
                return AgentLoopTransition(
                    status="escalated",
                    operation=pending.operation,
                    message=(
                        "Robot Agent cannot prove the outcome of a pending "
                        "physical skill and will not replay it."
                    ),
                    reason_code="physical_action_outcome_unknown",
                )
            output = reconcile_skill(pending.operation_id, step)
            reconciliation_status = (
                output.get("reconciliation_status")
                if isinstance(output, dict)
                else None
            )
            if reconciliation_status == "not_started":
                observation = RobotAgentExecutionObservation(
                    iteration=turn.iteration,
                    operation=decision.operation,
                    status="not_started",
                    message=(
                        "The pending skill was confirmed not to have reached "
                        "the physical dispatcher."
                    ),
                    tool_name=step.skill_name,
                    inputs=step.inputs,
                    reason_code="physical_action_not_started",
                )
                emit("robot_agent.observation", observation.to_dict())
                return AgentLoopTransition.continuing(
                    operation=decision.operation,
                    message=observation.message,
                    observation=observation,
                    reason_code=observation.reason_code,
                )
            if output is None or reconciliation_status == "unknown":
                emit(
                    "robot_agent.pending_operation_unresolved",
                    {
                        "operation_id": pending.operation_id,
                        "tool_name": step.skill_name,
                        "reason_code": "physical_action_outcome_unknown",
                    },
                )
                return AgentLoopTransition(
                    status="escalated",
                    operation=pending.operation,
                    message=(
                        "The physical skill may have started, but no finished "
                        "evidence exists. Automatic replay is prohibited."
                    ),
                    reason_code="physical_action_outcome_unknown",
                )
            return skill_transition(
                decision=decision,
                turn=turn,
                step=step,
                output=output,
                reconciled=True,
            )

        emit(
            "robot_agent.deliberation_started",
            {
                "run_id": run_id,
                "task_id": envelope.task_id,
                "robot_id": envelope.robot_id,
                "resumed": resume_checkpoint is not None,
            },
        )
        result = BoundedAgentLoop[
            RobotAgentDecision,
            RobotAgentExecutionObservation,
            dict[str, Any],
        ](
            limits=self.limits.loop_limits(),
            cancellation_requested=cancellation_requested,
            checkpoint_store=self.checkpoint_store,
            checkpoint_role="robot_agent",
        ).run(
            run_id=run_id,
            decide=decide,
            execute=execute,
            checkpoint_key=checkpoint_key,
            resume_checkpoint=resume_checkpoint,
            observation_from_checkpoint=(
                lambda value: RobotAgentExecutionObservation.from_dict(
                    value
                )
            ),
            adapter_state_provider=current_adapter_state,
            requires_reconciliation=(
                lambda decision: (
                    decision.operation == "execute_skill"
                    or (
                        decision.operation == "execute_agent_tool"
                        and decision.tool_effect != "read"
                    )
                )
            ),
            reconcile_pending=reconcile_pending,
        )
        emit("robot_agent.deliberation_finished", result.to_dict())
        return result


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    return {
        str(function["name"])
        for tool in tools
        if isinstance(tool.get("function"), dict)
        for function in [tool["function"]]
        if isinstance(function.get("name"), str)
    }


def _agent_tool_effect(context: dict[str, Any], name: str) -> str:
    policy = context.get("agent_tool_policy")
    if not isinstance(policy, dict):
        return "unknown"
    tools = policy.get("tools")
    if not isinstance(tools, list):
        return "unknown"
    for item in tools:
        if (
            isinstance(item, dict)
            and item.get("name") == name
            and isinstance(item.get("effect"), str)
        ):
            return str(item["effect"])
    return "unknown"


def _execution_reason_code(output: dict[str, Any]) -> str:
    execution = output.get("execution")
    if not isinstance(execution, dict):
        return "skill_execution_failed"
    steps = execution.get("steps")
    if not isinstance(steps, list) or not steps:
        return "skill_execution_failed"
    last = steps[-1]
    if not isinstance(last, dict):
        return "skill_execution_failed"
    category = last.get("failure_category")
    return str(category or "skill_execution_failed")


def _task_contract_hash(task: StructuredRobotTask) -> str:
    encoded = json.dumps(
        task.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
