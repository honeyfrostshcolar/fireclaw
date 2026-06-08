from __future__ import annotations

import time
import uuid
from typing import Any

from fireclaw_core.llm_trace import LLMTraceRecord, LLMTraceStore
from fireclaw_core.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.provider import (
    ChatCompletion,
    ModelProvider,
    ProviderAPIError,
    ProviderError,
    ProviderTimeoutError,
    TokenUsage,
)
from fireclaw_core.robot_registry import RobotRegistryEntry


# ---------------------------------------------------------------------------
# LLM Mission Planner (tool-calling based)
# ---------------------------------------------------------------------------

VALID_INTENTS: set[str] = {"search", "patrol", "firefight", "recon", "transport"}

MISSION_PLAN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_mission_plan",
        "description": "创建消防机器人任务计划",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": sorted(VALID_INTENTS),
                },
                "subtasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "command": {"type": "string"},
                            "floor": {"type": "integer"},
                            "capability_required": {"type": "string"},
                            "execution_group": {"type": "integer", "default": 0},
                        },
                        "required": ["robot_id", "command", "floor", "capability_required"],
                    },
                },
            },
            "required": ["intent", "subtasks"],
        },
    },
}


def build_system_prompt(context: MissionPlannerContext) -> str:
    """Build a system prompt listing available robots for the LLM."""
    lines = [
        "你是消防机器人任务规划助手。",
        "请根据操作员的指令，调用 create_mission_plan 工具生成任务计划。",
        "",
        "可用机器人：",
    ]
    for robot in context.available_robots:
        status = "已启用" if robot.enabled else "已禁用"
        caps = ", ".join(robot.capabilities) if robot.capabilities else "无"
        zone = robot.zone or "未分配"
        lines.append(f"- {robot.robot_id}: 能力=[{caps}], 区域={zone}, 状态={status}")
    if not context.available_robots:
        lines.append("- (无可用机器人)")
    lines.append("")
    lines.append("## 输出要求")
    lines.append("请调用 create_mission_plan 工具，输出结构化的任务计划。")
    lines.append("- intent: 任务意图（search/patrol/firefight/recon/transport）")
    lines.append("- subtasks: 子任务列表，每个子任务包含 robot_id, command, floor, capability_required, execution_group")
    lines.append("- execution_group: 执行组编号，同组可并行，不同组按顺序执行")
    lines.append("- robot_id 必须是上面列出的可用机器人之一")
    lines.append("- capability_required 必须是该机器人具备的能力之一")
    return "\n".join(lines)


class LLMMissionPlanner:
    """LLM-driven mission planner that uses tool calling to produce structured plans."""

    def __init__(
        self,
        provider: ModelProvider,
        model_id: str,
        trace_store: LLMTraceStore | None = None,
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        self._trace_store = trace_store

    def plan(
        self,
        command: str,
        context: MissionPlannerContext | None = None,
    ) -> MissionPlanningResult:
        """Generate a mission plan for the given operator command."""
        if context is None:
            context = MissionPlannerContext()

        system_prompt = build_system_prompt(context)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": command},
        ]

        start_time = time.monotonic()
        try:
            response = self._provider.chat_completion(
                messages=messages,
                model=self._model_id,
                tools=[MISSION_PLAN_TOOL],
                temperature=0.0,
                max_tokens=4096,
            )
        except ProviderTimeoutError:
            return self._record_and_return(
                messages=messages,
                response=None,
                start_time=start_time,
                status="error",
                error_message="LLM 调用超时，请稍后重试。",
                token_usage=None,
            )
        except ProviderAPIError as exc:
            return self._record_and_return(
                messages=messages,
                response=None,
                start_time=start_time,
                status="error",
                error_message=f"LLM API 错误：{exc}",
                token_usage=None,
            )
        except ProviderError as exc:
            return self._record_and_return(
                messages=messages,
                response=None,
                start_time=start_time,
                status="error",
                error_message=f"LLM 调用错误：{exc}",
                token_usage=None,
            )

        result = self._parse_response(response, context)

        self._record_trace(
            messages=messages,
            response=response,
            start_time=start_time,
            status="success" if result.status == "planned" else "error",
            error=None if result.status == "planned" else result.message,
        )

        return result

    def _parse_response(
        self,
        response: ChatCompletion,
        context: MissionPlannerContext,
    ) -> MissionPlanningResult:
        """Parse a ChatCompletion into a MissionPlanningResult."""
        if not response.tool_calls:
            return MissionPlanningResult(
                status="error",
                message="LLM 未返回工具调用。",
            )

        tool_call = response.tool_calls[0]
        arguments = tool_call.arguments

        intent = arguments.get("intent", "")
        if intent not in VALID_INTENTS:
            return MissionPlanningResult(
                status="error",
                message=f"LLM 返回了无效的意图：{intent}",
            )

        raw_subtasks = arguments.get("subtasks", [])
        if not isinstance(raw_subtasks, list) or not raw_subtasks:
            return MissionPlanningResult(
                status="error",
                message="LLM 未返回子任务列表。",
            )

        # Build a lookup of available robot_ids from context.
        known_robot_ids = {r.robot_id for r in context.available_robots}

        subtasks: list[MissionSubtask] = []
        for item in raw_subtasks:
            robot_id = item.get("robot_id", "")
            if known_robot_ids and robot_id not in known_robot_ids:
                return MissionPlanningResult(
                    status="error",
                    message=f"LLM 指定了不存在的机器人：{robot_id}",
                )
            subtasks.append(
                MissionSubtask(
                    robot_id=robot_id,
                    command=item.get("command", ""),
                    floor=int(item.get("floor", 0)),
                    capability_required=item.get("capability_required", ""),
                    execution_group=int(item.get("execution_group", 0)),
                )
            )

        plan = MissionPlan(
            intent=intent,
            command=arguments.get("command", ""),
            subtasks=subtasks,
        )
        # Use the original user command if the LLM didn't include one.
        object.__setattr__(plan, "command", arguments.get("command", "") or "")

        return MissionPlanningResult(
            status="planned",
            message=f"已生成任务计划：{len(subtasks)} 个子任务，{plan.execution_groups} 个执行组。",
            intent=intent,
            plan=plan,
        )

    def _record_and_return(
        self,
        *,
        messages: list[dict[str, Any]],
        response: ChatCompletion | None,
        start_time: float,
        status: str,
        error_message: str,
        token_usage: TokenUsage | None,
    ) -> MissionPlanningResult:
        """Record a trace and return an error result."""
        self._record_trace(
            messages=messages,
            response=response,
            start_time=start_time,
            status=status,
            error=error_message,
        )
        return MissionPlanningResult(status="error", message=error_message)

    def _record_trace(
        self,
        *,
        messages: list[dict[str, Any]],
        response: ChatCompletion | None,
        start_time: float,
        status: str,
        error: str | None,
    ) -> None:
        """Append a trace record to the trace store if configured."""
        if self._trace_store is None:
            return

        latency_ms = (time.monotonic() - start_time) * 1000
        tool_calls_data: list[dict[str, Any]] | None = None
        if response and response.tool_calls:
            tool_calls_data = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]

        trace = LLMTraceRecord(
            trace_id=uuid.uuid4().hex,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            provider="llm_mission_planner",
            model=self._model_id,
            messages=messages,
            response=None,
            tool_calls=tool_calls_data,
            latency_ms=round(latency_ms, 2),
            token_usage=response.usage if response else None,
            status=status,
            error=error,
        )
        self._trace_store.record(trace)
