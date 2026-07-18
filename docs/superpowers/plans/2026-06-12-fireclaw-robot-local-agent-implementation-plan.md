# FireClaw Robot-Local Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenClaw-style robot-local agent path where each robot-local `FireClawGateway` can call an LLM for constrained local planning before executing ROS1/Gazebo skills.

**Architecture:** Keep the mission-level `MissionAgent` as the high-level planner and dispatcher. Add a robot-local `RobotAgentRuntime` inside `FireClawGateway` for structured tasks, guarded by `RobotAgentPolicy` and falling back to the current `planning_result_from_structured_task()` path when the model path fails or is disabled.

**Tech Stack:** Python dataclasses, existing `ProviderRuntime` / `OpenAICompatProvider`, existing `PlanningResult` / `PlanExecutor` / `SafetyGate`, pytest, existing HTTP gateway tests, optional ROS1/Gazebo smoke validation outside the default pytest suite.

---

## File Structure

- Create `src/fireclaw_core/robot_agent.py`
  - Owns `RobotAgentTaskEnvelope`, `RobotLocalPlan`, `RobotLocalPlanStep`, `RobotAgentPolicy`, `RobotAgentPlanner`, `LLMRobotAgentPlanner`, `DeterministicRobotAgentPlanner`, and `RobotAgentRuntime`.
- Modify `src/fireclaw_core/gateway.py`
  - Add robot-agent config fields, CLI flags, runtime construction, and optional structured-task execution path.
- Modify `src/fireclaw_core/planner_builder.py`
  - Reuse provider construction logic or add a small helper for robot-local planner runtime construction if needed.
- Create `tests/test_robot_agent_contract.py`
  - Covers envelope conversion, local-plan parsing, and planning-result conversion.
- Create `tests/test_robot_agent_policy.py`
  - Covers bounded autonomy policy decisions.
- Create `tests/test_robot_agent_runtime.py`
  - Covers planner success, failure fallback, policy fallback, and SafetyGate non-bypass behavior at the runtime layer.
- Modify `tests/test_gateway_structured_task.py`
  - Adds gateway-level integration tests for robot-agent mode.
- Create `tests/test_gateway_robot_agent_cli.py`
  - Covers new CLI/config flags without requiring ROS.
- Create `src/fireclaw_core/gazebo_smoke.py`
  - Non-default smoke runner for `MissionGateway -> robot-local RobotAgent -> ROS1/Gazebo` proof.
- Create `tests/test_gazebo_smoke.py`
  - Tests argument parsing and dry validation helpers only; does not require ROS/Gazebo.
- Update `docs/deployment/ros1-gazebo-debugging-guide.md`
  - Adds robot-local agent mode commands.

---

### Task 1: Robot-Local Agent Data Contract

**Files:**
- Create: `src/fireclaw_core/robot_agent.py`
- Create: `tests/test_robot_agent_contract.py`

- [ ] **Step 1: Write failing envelope conversion tests**

Add `tests/test_robot_agent_contract.py`:

```python
from __future__ import annotations

from fireclaw_core.robot_agent import (
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
    envelope_from_structured_task,
    planning_result_from_local_plan,
)
from fireclaw_core.task_contract import StructuredRobotTask


def test_envelope_from_structured_task_adds_safe_allowed_skills():
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索受困人员",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims"],
        constraints={"execution_group": 0},
        risk_level="low",
        operator_id="operator-1",
    )

    envelope = envelope_from_structured_task(task, fallback_robot_id="robot-fallback")

    assert envelope == RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索受困人员",
        task_type="search",
        target={"floor": 2},
        allowed_skills=[
            "navigate_to_floor",
            "search_for_victims",
            "report_status",
            "return_to_safe_zone",
        ],
        required_skills=["navigate_to_floor", "search_for_victims"],
        constraints={"execution_group": 0},
        risk_level="low",
        operator_id="operator-1",
    )


def test_envelope_uses_fallback_robot_id_when_task_has_none():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor"],
    )

    envelope = envelope_from_structured_task(task, fallback_robot_id="gateway-robot")

    assert envelope.robot_id == "gateway-robot"


def test_planning_result_from_local_plan_preserves_step_order_and_floor():
    envelope = RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["report_status", "navigate_to_floor"],
        required_skills=["navigate_to_floor"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )
    local_plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("report_status", {"floor": 2}, reason="announce start"),
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}, reason="move"),
        ],
        rationale="Robot reports before moving.",
        confidence=0.8,
    )

    result = planning_result_from_local_plan(envelope, local_plan)

    assert result.status == "planned"
    assert result.intent == "search"
    assert result.target_floor == 2
    assert [step.skill_name for step in result.plan.steps] == [
        "report_status",
        "navigate_to_floor",
    ]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_contract.py -q
```

Expected: import failure for `fireclaw_core.robot_agent`.

- [ ] **Step 3: Implement minimal data contract**

Create `src/fireclaw_core/robot_agent.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fireclaw_core.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.task_contract import StructuredRobotTask

SAFE_SUPPLEMENTAL_SKILLS = ("report_status", "return_to_safe_zone")
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
    allowed_skills = list(dict.fromkeys([*task.required_skills, *SAFE_SUPPLEMENTAL_SKILLS]))
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


class RobotAgentPlanner(Protocol):
    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested=None,
    ) -> RobotLocalPlan:
        ...
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_contract.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/robot_agent.py tests/test_robot_agent_contract.py
git commit -m "feat: add robot-local agent task contract"
```

---

### Task 2: Robot-Agent Policy Gate

**Files:**
- Modify: `src/fireclaw_core/robot_agent.py`
- Create: `tests/test_robot_agent_policy.py`

- [ ] **Step 1: Write failing policy tests**

Add `tests/test_robot_agent_policy.py`:

```python
from __future__ import annotations

from fireclaw_core.robot_agent import (
    RobotAgentPolicy,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)


def _envelope(risk_level: str = "low") -> RobotAgentTaskEnvelope:
    return RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["navigate_to_floor", "search_for_victims", "report_status"],
        required_skills=["navigate_to_floor", "report_status"],
        constraints={},
        risk_level=risk_level,
        operator_id="operator-1",
    )


def test_policy_allows_plan_inside_envelope():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "allow"
    assert decision.reasons == []


def test_policy_rejects_skill_outside_allowed_set():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("firefight", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("firefight" in reason for reason in decision.reasons)


def test_policy_rejects_mutated_floor():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 3}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("floor" in reason for reason in decision.reasons)


def test_policy_rejects_missing_required_skill():
    plan = RobotLocalPlan(
        intent="search",
        steps=[RobotLocalPlanStep("navigate_to_floor", {"floor": 2})],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("report_status" in reason for reason in decision.reasons)


def test_policy_requires_approval_for_high_risk_execution():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(risk_level="high"), plan)

    assert decision.status == "approval_required"
    assert any("risk" in reason.lower() for reason in decision.reasons)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_policy.py -q
```

Expected: import failure for `RobotAgentPolicy`.

- [ ] **Step 3: Implement policy decision and validation**

Append to `src/fireclaw_core/robot_agent.py`:

```python
@dataclass(frozen=True)
class RobotAgentPolicyDecision:
    status: str
    reasons: list[str]


class RobotAgentPolicy:
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
```

- [ ] **Step 4: Run policy and contract tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_contract.py tests/test_robot_agent_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/robot_agent.py tests/test_robot_agent_policy.py
git commit -m "feat: add robot-local agent policy gate"
```

---

### Task 3: Robot-Local LLM Planner

**Files:**
- Modify: `src/fireclaw_core/robot_agent.py`
- Create: `tests/test_robot_agent_planner.py`

- [ ] **Step 1: Write failing LLM planner tests**

Add `tests/test_robot_agent_planner.py`:

```python
from __future__ import annotations

import pytest

from fireclaw_core.provider import ChatCompletion, TokenUsage, ToolCall, ProviderTimeoutError
from fireclaw_core.robot_agent import (
    LLMRobotAgentPlanner,
    RobotAgentPlannerError,
    RobotAgentTaskEnvelope,
)


class FakeRuntime:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def chat_completion(self, *, messages, tools, temperature=0.0, max_tokens=4096):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response

    def status(self):
        return {"type": "fake", "model": "fake-model"}


def _envelope() -> RobotAgentTaskEnvelope:
    return RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["navigate_to_floor", "report_status"],
        required_skills=["navigate_to_floor"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )


def test_llm_robot_agent_planner_parses_tool_call():
    runtime = FakeRuntime(
        response=ChatCompletion(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="create_robot_local_plan",
                    arguments={
                        "intent": "search",
                        "steps": [
                            {
                                "skill_name": "report_status",
                                "inputs": {"floor": 2},
                                "reason": "announce",
                            },
                            {
                                "skill_name": "navigate_to_floor",
                                "inputs": {"floor": 2},
                            },
                        ],
                        "rationale": "Report before moving.",
                        "confidence": 0.7,
                    },
                )
            ],
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="tool_calls",
        )
    )

    plan = LLMRobotAgentPlanner(runtime).plan(_envelope(), context={"robot_state": {}})

    assert [step.skill_name for step in plan.steps] == ["report_status", "navigate_to_floor"]
    assert plan.steps[0].reason == "announce"
    assert runtime.calls[0]["tools"][0]["function"]["name"] == "create_robot_local_plan"


def test_llm_robot_agent_planner_raises_on_missing_tool_call():
    runtime = FakeRuntime(
        response=ChatCompletion(
            content="free text",
            tool_calls=None,
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="stop",
        )
    )

    with pytest.raises(RobotAgentPlannerError, match="tool"):
        LLMRobotAgentPlanner(runtime).plan(_envelope(), context={})


def test_llm_robot_agent_planner_wraps_provider_timeout():
    runtime = FakeRuntime(error=ProviderTimeoutError("timed out"))

    with pytest.raises(RobotAgentPlannerError, match="timed out"):
        LLMRobotAgentPlanner(runtime).plan(_envelope(), context={})
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_planner.py -q
```

Expected: import failure for `LLMRobotAgentPlanner`.

- [ ] **Step 3: Implement planner schema and parser**

Add to `src/fireclaw_core/robot_agent.py`:

```python
from fireclaw_core.provider import ProviderError
from fireclaw_core.provider_runtime import FallbackSummaryError, ProviderRuntime


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
    system = (
        "你是消防机器人本地子 agent。"
        "你只能在 allowed_skills 内规划，不能改变 target，不能扩大任务权限。"
        "请调用 create_robot_local_plan 工具返回结构化局部执行计划。"
    )
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
        "context": context,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": str(payload)},
    ]


class LLMRobotAgentPlanner:
    def __init__(self, provider_runtime: ProviderRuntime) -> None:
        self._provider_runtime = provider_runtime

    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested=None,
    ) -> RobotLocalPlan:
        if cancellation_requested is not None and cancellation_requested():
            raise RobotAgentPlannerError("planning cancelled before provider call")
        try:
            response = self._provider_runtime.chat_completion(
                messages=build_robot_agent_messages(envelope, context=context),
                tools=[ROBOT_LOCAL_PLAN_TOOL],
                temperature=0.0,
                max_tokens=2048,
            )
        except (ProviderError, FallbackSummaryError) as exc:
            raise RobotAgentPlannerError(str(exc)) from exc
        if cancellation_requested is not None and cancellation_requested():
            raise RobotAgentPlannerError("planning cancelled after provider call")
        if not response.tool_calls:
            raise RobotAgentPlannerError("LLM did not return a robot-local plan tool call")
        tool_call = response.tool_calls[0]
        if tool_call.name != "create_robot_local_plan":
            raise RobotAgentPlannerError(f"unexpected tool call {tool_call.name!r}")
        return _local_plan_from_arguments(tool_call.arguments)


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
```

- [ ] **Step 4: Run planner tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_planner.py tests/test_robot_agent_contract.py tests/test_robot_agent_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/robot_agent.py tests/test_robot_agent_planner.py
git commit -m "feat: add robot-local LLM planner"
```

---

### Task 4: Robot-Agent Runtime and Fallback

**Files:**
- Modify: `src/fireclaw_core/robot_agent.py`
- Create: `tests/test_robot_agent_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Add `tests/test_robot_agent_runtime.py`:

```python
from __future__ import annotations

from fireclaw_core.robot_agent import (
    RobotAgentPlannerError,
    RobotAgentRuntime,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)
from fireclaw_core.task_contract import StructuredRobotTask


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self.plan_value = plan
        self.error = error

    def plan(self, envelope, *, context, cancellation_requested=None):
        if self.error is not None:
            raise self.error
        return self.plan_value


def _task(risk_level: str = "low") -> StructuredRobotTask:
    return StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "report_status"],
        risk_level=risk_level,
        robot_id="robot-1",
        mission_id="mission-1",
        command="去2楼搜索",
    )


def test_runtime_accepts_valid_local_plan():
    runtime = RobotAgentRuntime(
        planner=FakePlanner(
            RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("report_status", {"floor": 2}),
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "planned"
    assert [step.skill_name for step in result.plan.steps] == ["report_status", "navigate_to_floor"]
    assert any(event_type == "robot_agent.plan_accepted" for event_type, _ in events)


def test_runtime_falls_back_when_planner_fails():
    runtime = RobotAgentRuntime(planner=FakePlanner(error=RobotAgentPlannerError("bad model")))
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "planned"
    assert [step.skill_name for step in result.plan.steps] == ["navigate_to_floor", "report_status"]
    assert any(event_type == "robot_agent.plan_failed" for event_type, _ in events)
    assert any(event_type == "robot_agent.fallback_used" for event_type, _ in events)


def test_runtime_falls_back_when_policy_rejects_plan():
    runtime = RobotAgentRuntime(
        planner=FakePlanner(
            RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 3}),
                    RobotLocalPlanStep("report_status", {"floor": 3}),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert [step.inputs.get("floor") for step in result.plan.steps] == [2, 2]
    assert any(event_type == "robot_agent.policy_rejected" for event_type, _ in events)


def test_runtime_returns_clarify_for_high_risk_without_executing_local_plan():
    runtime = RobotAgentRuntime(
        planner=FakePlanner(
            RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
                    RobotLocalPlanStep("report_status", {"floor": 2}),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(risk_level="high"),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "clarify"
    assert "approval" in result.message.lower()
    assert any(event_type == "robot_agent.policy_rejected" for event_type, _ in events)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_runtime.py -q
```

Expected: import failure for `RobotAgentRuntime`.

- [ ] **Step 3: Implement deterministic planner and runtime**

Append to `src/fireclaw_core/robot_agent.py`:

```python
from fireclaw_core.task_contract import planning_result_from_structured_task


class DeterministicRobotAgentPlanner:
    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested=None,
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
        event_sink=None,
        cancellation_requested=None,
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
        event_sink,
        reason: str,
    ) -> PlanningResult:
        self._emit(event_sink, "robot_agent.fallback_used", {"task_id": task.task_id, "reason": reason})
        return planning_result_from_structured_task(task)

    @staticmethod
    def _emit(event_sink, event_type: str, payload: dict[str, Any]) -> None:
        if event_sink is not None:
            event_sink(event_type, payload)
```

- [ ] **Step 4: Run runtime tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_contract.py tests/test_robot_agent_policy.py tests/test_robot_agent_planner.py tests/test_robot_agent_runtime.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/robot_agent.py tests/test_robot_agent_runtime.py
git commit -m "feat: add robot-local agent runtime fallback"
```

---

### Task 5: Gateway Integration and CLI Flags

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway_structured_task.py`
- Create: `tests/test_gateway_robot_agent_cli.py`

- [ ] **Step 1: Write failing gateway integration tests**

Append to `tests/test_gateway_structured_task.py`:

```python
def _events_for_task(base_url: str, task_id: str) -> list[dict]:
    body = _json_request(base_url, "GET", f"/events?task_id={task_id}")
    return body.get("events", [])


def test_gateway_robot_agent_mode_emits_robot_agent_events(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-robot-agent-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])
        events = _events_for_task(gateway.base_url, accepted["task_id"])

        assert trace["result"]["status"] in {"completed", "succeeded"}
        assert any(event.get("type") == "robot_agent.plan_requested" for event in events)
        assert any(event.get("type") == "robot_agent.plan_accepted" for event in events)
    finally:
        gateway.stop()


def test_gateway_robot_agent_mode_falls_back_for_high_risk_task(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-robot-agent-high-risk",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                    "risk_level": "high",
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])
        events = _events_for_task(gateway.base_url, accepted["task_id"])

        assert trace["result"]["status"] in {"clarify", "awaiting_confirmation"}
        assert any(event.get("type") == "robot_agent.policy_rejected" for event in events)
    finally:
        gateway.stop()
```

- [ ] **Step 2: Write failing CLI help test**

Add `tests/test_gateway_robot_agent_cli.py`:

```python
from __future__ import annotations

import subprocess


def test_gateway_cli_exposes_robot_agent_flags():
    completed = subprocess.run(
        [".venv/bin/python", "-m", "fireclaw_core.gateway", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout
    assert "--robot-agent-provider-base-url" in completed.stdout
    assert "--robot-agent-model" in completed.stdout
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_structured_task.py::test_gateway_robot_agent_mode_emits_robot_agent_events tests/test_gateway_structured_task.py::test_gateway_robot_agent_mode_falls_back_for_high_risk_task tests/test_gateway_robot_agent_cli.py -q
```

Expected: `GatewayConfig.__init__()` rejects `robot_agent_enabled` and CLI flags are missing.

- [ ] **Step 4: Add gateway config fields and runtime construction**

Modify `src/fireclaw_core/gateway.py` imports:

```python
from fireclaw_core.planner_builder import build_provider_runtime
from fireclaw_core.robot_agent import (
    DeterministicRobotAgentPlanner,
    LLMRobotAgentPlanner,
    RobotAgentRuntime,
)
```

Add fields to `GatewayConfig`:

```python
    robot_agent_enabled: bool = False
    robot_agent_planner: str = "deterministic"
    robot_agent_provider_base_url: str | None = None
    robot_agent_provider_api_key: str | None = None
    robot_agent_model: str | None = None
    robot_agent_llm_trace_path: str | None = None
```

Add to `FireClawGateway.__init__()`:

```python
        self.robot_agent_runtime = self._build_robot_agent_runtime()
```

Add helper:

```python
    def _build_robot_agent_runtime(self) -> RobotAgentRuntime | None:
        if not self.config.robot_agent_enabled:
            return None
        if self.config.robot_agent_planner == "deterministic":
            return RobotAgentRuntime(planner=DeterministicRobotAgentPlanner())
        if self.config.robot_agent_planner == "llm":
            runtime = build_provider_runtime(
                provider_base_url=self.config.robot_agent_provider_base_url,
                provider_api_key=self.config.robot_agent_provider_api_key,
                model=self.config.robot_agent_model,
            )
            return RobotAgentRuntime(planner=LLMRobotAgentPlanner(runtime))
        raise ValueError(f"unsupported robot_agent_planner: {self.config.robot_agent_planner}")
```

- [ ] **Step 5: Add robot-agent planning path in `_execute_agent_task()`**

Replace the structured-task branch:

```python
        if structured_task is not None:
            task_object = StructuredRobotTask.from_dict(structured_task)
            result = agent.run_structured_task(task_object)
        else:
            result = agent.run(command)
```

with:

```python
        if structured_task is not None:
            task_object = StructuredRobotTask.from_dict(structured_task)
            if self.robot_agent_runtime is not None:
                result = self._run_robot_agent_structured_task(
                    agent=agent,
                    task_object=task_object,
                    session_id=session_id,
                    task_id=task_id,
                    cancellation_requested=cancellation_requested,
                )
            else:
                result = agent.run_structured_task(task_object)
        else:
            result = agent.run(command)
```

Add helper:

```python
    def _run_robot_agent_structured_task(
        self,
        *,
        agent: FireClawAgent,
        task_object: StructuredRobotTask,
        session_id: str,
        task_id: str,
        cancellation_requested=None,
    ) -> dict[str, Any]:
        assert self.robot_agent_runtime is not None

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type=event_type,
                payload=payload,
            )

        context = {
            "robot_state": agent._state_snapshot(agent._get_robot_state()),
            "environment_state": agent._state_snapshot(agent._get_environment_state()),
            "available_sensors": sorted(agent.available_sensors),
        }
        planning_result = self.robot_agent_runtime.plan_structured_task(
            task_object,
            fallback_robot_id=self.config.robot_id,
            context=context,
            event_sink=emit,
            cancellation_requested=cancellation_requested,
        )
        return agent.run_planning_result(
            command=task_object.command or task_object.task_type,
            structured_task=task_object,
            planning_result=planning_result,
        )
```

Implementation note: apply the `FireClawAgent.run_planning_result()` helper from the next task before running this task's gateway tests. The gateway helper above is written against that reusable execution method.

- [ ] **Step 6: Add CLI flags**

Modify `gateway.main()` parser:

```python
    parser.add_argument("--robot-agent", action="store_true", help="Enable robot-local agent planning for structured tasks.")
    parser.add_argument("--robot-agent-planner", choices=["deterministic", "llm"], default="deterministic")
    parser.add_argument("--robot-agent-provider-base-url", default=None)
    parser.add_argument("--robot-agent-provider-api-key", default=None)
    parser.add_argument("--robot-agent-model", default=None)
    parser.add_argument("--robot-agent-llm-trace-path", default=None)
```

Pass fields into `GatewayConfig`:

```python
            robot_agent_enabled=args.robot_agent,
            robot_agent_planner=args.robot_agent_planner,
            robot_agent_provider_base_url=args.robot_agent_provider_base_url,
            robot_agent_provider_api_key=args.robot_agent_provider_api_key,
            robot_agent_model=args.robot_agent_model,
            robot_agent_llm_trace_path=args.robot_agent_llm_trace_path,
```

- [ ] **Step 7: Run gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_structured_task.py tests/test_gateway_robot_agent_cli.py -q
```

Expected: all tests pass after the reusable `FireClawAgent.run_planning_result()` helper has been added.

- [ ] **Step 8: Commit**

```bash
git add src/fireclaw_core/gateway.py tests/test_gateway_structured_task.py tests/test_gateway_robot_agent_cli.py
git commit -m "feat: enable robot-local agent mode in gateway"
```

---

### Task 6: Reusable FireClawAgent Planning-Result Execution

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Create: `tests/test_robot_agent_fireclaw_agent_execution.py`

- [ ] **Step 1: Write failing agent helper test**

Add `tests/test_robot_agent_fireclaw_agent_execution.py`:

```python
from __future__ import annotations

from fireclaw_core.agent import FireClawAgent
from fireclaw_core.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.task_contract import StructuredRobotTask


def test_fireclaw_agent_can_execute_precomputed_structured_planning_result(tmp_path):
    agent = FireClawAgent(
        memory=None,
        workspace_skills_dir=None,
        dry_run=True,
        session_id="session-1",
    )
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "report_status"],
        command="去2楼搜索",
    )
    planning_result = PlanningResult(
        status="planned",
        message="precomputed",
        intent="search",
        target_floor=2,
        plan=Plan(
            intent="search",
            steps=[
                PlanStep("report_status", {"floor": 2}),
                PlanStep("navigate_to_floor", {"floor": 2}),
            ],
        ),
    )

    result = agent.run_planning_result(
        command="去2楼搜索",
        structured_task=task,
        planning_result=planning_result,
    )

    assert result["status"] in {"completed", "succeeded"}
    assert result["structured_task"]["task_id"] == "task-1"
    assert [step["skill_name"] for step in result["planning"]["plan"]["steps"]] == [
        "report_status",
        "navigate_to_floor",
    ]
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_fireclaw_agent_execution.py -q
```

Expected: `FireClawAgent` has no `run_planning_result`.

- [ ] **Step 3: Add `run_planning_result()` to `FireClawAgent`**

Modify `src/fireclaw_core/agent.py` inside `FireClawAgent`:

```python
    def run_planning_result(
        self,
        *,
        command: str,
        planning_result: PlanningResult,
        structured_task: StructuredRobotTask | None = None,
    ) -> dict[str, Any]:
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        safety_decision = self.safety.evaluate(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self.available_sensors,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
        )
        if structured_task is not None:
            self._emit_event("task.structured_received", structured_task.to_dict())
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result: ExecutionResult | None = None
        if safety_decision.status == "allow" and planning_result.plan is not None:
            execution_result = self.executor.execute(planning_result.plan)

        status = self._resolve_status(safety_decision, execution_result)
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": status,
            "message": self._resolve_message(planning_result, safety_decision, execution_result),
            "dry_run": self.dry_run,
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "confirmation": self._confirmation_to_dict(safety_decision),
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }
        if structured_task is not None:
            result["structured_task"] = structured_task.to_dict()
        self._append_memory_result(result)
        return result
```

- [ ] **Step 4: Refactor `run_structured_task()` to use helper**

Replace `run_structured_task()` body with:

```python
    def run_structured_task(self, task: StructuredRobotTask) -> dict[str, Any]:
        return self.run_planning_result(
            command=task.command or task.task_type,
            structured_task=task,
            planning_result=planning_result_from_structured_task(task),
        )
```

- [ ] **Step 5: Run agent and structured-task tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_fireclaw_agent_execution.py tests/test_gateway_structured_task.py tests/test_task_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/agent.py tests/test_robot_agent_fireclaw_agent_execution.py
git commit -m "refactor: execute precomputed robot planning results"
```

---

### Task 7: Provider Runtime Helper and Gazebo Smoke Command

**Files:**
- Modify: `src/fireclaw_core/planner_builder.py`
- Create: `src/fireclaw_core/gazebo_smoke.py`
- Create: `tests/test_gazebo_smoke.py`
- Update: `docs/deployment/ros1-gazebo-debugging-guide.md`

- [ ] **Step 1: Write failing provider helper and smoke parser tests**

Add `tests/test_gazebo_smoke.py`:

```python
from __future__ import annotations

from fireclaw_core.gazebo_smoke import build_parser, build_robot_registry_payload
from fireclaw_core.planner_builder import build_provider_runtime


def test_build_provider_runtime_requires_llm_fields():
    try:
        build_provider_runtime(provider_base_url=None, provider_api_key="key", model="model")
    except ValueError as exc:
        assert "provider_base_url" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_gazebo_smoke_parser_accepts_robot_agent_args():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--robot-id",
            "gazebo_turtlebot3",
            "--robot-gateway-url",
            "http://127.0.0.1:8765",
            "--mission-gateway-url",
            "http://127.0.0.1:8766",
            "--command",
            "去2楼救人",
            "--output-dir",
            "results/gazebo-smoke/local",
        ]
    )

    assert args.robot_id == "gazebo_turtlebot3"
    assert args.command == "去2楼救人"


def test_build_robot_registry_payload_points_to_robot_gateway():
    payload = build_robot_registry_payload(
        robot_id="gazebo_turtlebot3",
        base_url="http://127.0.0.1:8765",
    )

    assert payload["robots"][0]["robot_id"] == "gazebo_turtlebot3"
    assert payload["robots"][0]["base_url"] == "http://127.0.0.1:8765"
    assert "search_for_victims" in payload["robots"][0]["capabilities"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_gazebo_smoke.py -q
```

Expected: import failure for `gazebo_smoke` and `build_provider_runtime`.

- [ ] **Step 3: Add provider runtime helper**

Modify `src/fireclaw_core/planner_builder.py`:

```python
def build_provider_runtime(
    *,
    provider_base_url: str | None,
    provider_api_key: str | None,
    model: str | None,
) -> SimpleProviderRuntime:
    if not provider_base_url:
        raise ValueError("provider_base_url is required for LLM provider runtime")
    if not provider_api_key:
        raise ValueError("provider_api_key is required for LLM provider runtime")
    if not model:
        raise ValueError("model is required for LLM provider runtime")
    provider = OpenAICompatProvider(base_url=provider_base_url, api_key=provider_api_key)
    return SimpleProviderRuntime(provider=provider, model_id=model)
```

Then simplify `build_planner()` LLM branch:

```python
        runtime = build_provider_runtime(
            provider_base_url=provider_base_url,
            provider_api_key=provider_api_key,
            model=model,
        )
```

- [ ] **Step 4: Add Gazebo smoke module**

Create `src/fireclaw_core/gazebo_smoke.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_robot_registry_payload(*, robot_id: str, base_url: str) -> dict:
    return {
        "robots": [
            {
                "robot_id": robot_id,
                "base_url": base_url.rstrip("/"),
                "capabilities": [
                    "navigate_to_floor",
                    "search_for_victims",
                    "report_status",
                    "return_to_safe_zone",
                ],
            }
        ]
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a FireClaw ROS1/Gazebo robot-local agent smoke workflow.")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-gateway-url", required=True)
    parser.add_argument("--mission-gateway-url", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_payload = build_robot_registry_payload(
        robot_id=args.robot_id,
        base_url=args.robot_gateway_url,
    )
    (output_dir / "robots.json").write_text(
        json.dumps(registry_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "robot_id": args.robot_id,
                "robot_gateway_url": args.robot_gateway_url,
                "mission_gateway_url": args.mission_gateway_url,
                "command": args.command,
                "robots_path": str(output_dir / "robots.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Update Gazebo guide with robot-agent mode**

Add a section to `docs/deployment/ros1-gazebo-debugging-guide.md` after the direct subtask section:

````markdown
### Robot-Local Agent Mode

Start the robot-local gateway with constrained robot-agent planning:

```bash
.venv/bin/python -m fireclaw_core.gateway \
  --adapter ros1 \
  --real-run \
  --robot-id gazebo_turtlebot3 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml \
  --robot-agent \
  --robot-agent-planner llm \
  --robot-agent-provider-base-url "$OPENAI_BASE_URL" \
  --robot-agent-provider-api-key "$OPENAI_API_KEY" \
  --robot-agent-model "$MODEL"
```

Then start mission control and submit commands through `fireclaw_core mission`.
````

- [ ] **Step 6: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gazebo_smoke.py tests/test_planner_builder.py tests/test_gateway_robot_agent_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/fireclaw_core/planner_builder.py src/fireclaw_core/gazebo_smoke.py tests/test_gazebo_smoke.py docs/deployment/ros1-gazebo-debugging-guide.md
git commit -m "feat: add robot-local agent gazebo smoke entrypoint"
```

---

## Final Verification

- [ ] **Run focused robot-local agent suite**

```bash
.venv/bin/python -m pytest \
  tests/test_robot_agent_contract.py \
  tests/test_robot_agent_policy.py \
  tests/test_robot_agent_planner.py \
  tests/test_robot_agent_runtime.py \
  tests/test_robot_agent_fireclaw_agent_execution.py \
  tests/test_gateway_structured_task.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_gazebo_smoke.py \
  -q
```

Expected: all focused tests pass.

- [ ] **Run existing structured gateway and mission tests**

```bash
.venv/bin/python -m pytest \
  tests/test_task_contract.py \
  tests/test_gateway.py \
  tests/test_mission_agent.py \
  tests/test_mission_scheduler.py \
  tests/test_subagent_client.py \
  -q
```

Expected: all tests pass.

- [ ] **Run full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes with the existing skipped tests unchanged.

- [ ] **Manual Gazebo smoke, not default CI**

```bash
source /opt/ros/noetic/setup.bash
export ROS_MASTER_URI=http://localhost:11311
export ROS_IP=127.0.0.1

.venv/bin/python -m fireclaw_core.gateway \
  --adapter ros1 \
  --real-run \
  --robot-id gazebo_turtlebot3 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml \
  --robot-agent \
  --robot-agent-planner llm \
  --robot-agent-provider-base-url "$OPENAI_BASE_URL" \
  --robot-agent-provider-api-key "$OPENAI_API_KEY" \
  --robot-agent-model "$MODEL"
```

In another shell:

```bash
.venv/bin/python -m fireclaw_core serve \
  --planner llm \
  --provider-base-url "$OPENAI_BASE_URL" \
  --provider-api-key "$OPENAI_API_KEY" \
  --model "$MODEL" \
  --data-dir data/gazebo \
  --port 8766
```

In another shell:

```bash
.venv/bin/python -m fireclaw_core mission --server http://127.0.0.1:8766
```

Submit:

```text
去二楼救人
```

Expected:

- robot-local gateway emits `robot_agent.plan_requested`;
- robot-local gateway emits `robot_agent.plan_accepted` or a documented fallback event;
- ROS1 adapter sends `/move_base` goal through Gazebo config;
- mission trace reaches a terminal state;
- no raw API key appears in events or logs.
