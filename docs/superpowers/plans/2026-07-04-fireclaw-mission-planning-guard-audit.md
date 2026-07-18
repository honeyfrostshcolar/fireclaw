# FireClaw Mission Planning Guard Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mission-level guard and audit evidence so LLM mission planning cannot fabricate robot IDs and every planning allow/block decision can be reconstructed.

**Architecture:** `LLMMissionPlanner` builds a runtime-constrained tool schema, performs preflight and parser guards, and returns a `MissionPlanningAuditRecord` on `MissionPlanningResult`. `MissionAgent` appends deterministic validator decisions and records the finalized audit through an optional sink before any mission is dispatched.

**Tech Stack:** Python dataclasses/protocols, existing provider/tool-calling interfaces, existing `MissionAgent` and `MissionPlanValidator`, pytest.

**Commit Policy:** Do not commit during execution unless the user explicitly asks. This repository's `AGENTS.md` overrides the generic Superpowers commit-step guidance.

---

## Files

- Create: `src/fireclaw_core/mission/mission_planning_audit.py`
  - Holds audit dataclasses, stable reason constants through plain strings, serialization helpers, snapshot helper, and audit finalization helper.
- Modify: `src/fireclaw_core/mission/mission_planner.py`
  - Adds optional `audit_record` to `MissionPlanningResult`.
- Modify: `src/fireclaw_core/planner/llm_planner.py`
  - Adds constrained mission tool schema builder, preflight guard, parser guard decisions, and raw tool-call capture.
- Modify: `src/fireclaw_core/mission/mission_agent.py`
  - Accepts optional audit sink, appends validator decision, records blocked/planned audit before returning or dispatching.
- Test: `tests/test_mission_planning_audit.py`
  - Unit tests for audit dataclasses, snapshots, serialization, and sink failure helper.
- Test: `tests/test_llm_planner.py`
  - Tests no-robot preflight, dynamic schema enum, unknown robot parser block, parser guard details, and successful audit record.
- Test: `tests/test_mission_agent.py`
  - Tests validator decisions are recorded and audit sink failures block allowed plans.

## Task 1: Audit Data Model

**Files:**
- Create: `src/fireclaw_core/mission/mission_planning_audit.py`
- Modify: `src/fireclaw_core/mission/mission_planner.py`
- Test: `tests/test_mission_planning_audit.py`

- [ ] **Step 1: Write failing audit model tests**

Append this new test file:

```python
from __future__ import annotations

from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.mission.mission_planning_audit import (
    GuardDecision,
    MissionPlanningAuditRecord,
    build_available_robot_snapshot,
    append_guard_decision,
)


def test_guard_decision_serializes_stable_fields():
    decision = GuardDecision(
        layer="preflight",
        status="block",
        reason="no_available_robots",
        message="No available robots for mission planning.",
        details={"available_robot_count": 0},
    )

    assert decision.to_dict() == {
        "layer": "preflight",
        "status": "block",
        "reason": "no_available_robots",
        "message": "No available robots for mission planning.",
        "details": {"available_robot_count": 0},
    }


def test_audit_record_serializes_robot_snapshot_and_decisions():
    record = MissionPlanningAuditRecord(
        command="去二楼救人",
        available_robots=[
            {
                "robot_id": "gazebo_turtlebot3",
                "capabilities": ["search_for_victims"],
                "enabled": True,
                "zone": "training",
            }
        ],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {"intent": "search"}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="ready",
        created_at="2026-07-04T00:00:00+00:00",
        mission_id="mission-1",
    )

    data = record.to_dict()

    assert data["command"] == "去二楼救人"
    assert data["available_robots"][0]["robot_id"] == "gazebo_turtlebot3"
    assert data["tool_schema"] == {"type": "function"}
    assert data["llm_tool_call"]["name"] == "create_mission_plan"
    assert data["decisions"][0]["reason"] == "mission_plan_parsed"
    assert data["final_status"] == "planned"
    assert data["mission_id"] == "mission-1"


def test_build_available_robot_snapshot_is_serializable():
    snapshot = build_available_robot_snapshot(
        [
            RobotRegistryEntry(
                robot_id="r1",
                base_url="http://r1:8765",
                capabilities=("search_for_victims", "patrol"),
                zone=None,
                enabled=True,
            )
        ]
    )

    assert snapshot == [
        {
            "robot_id": "r1",
            "capabilities": ["search_for_victims", "patrol"],
            "enabled": True,
            "zone": None,
        }
    ]


def test_append_guard_decision_returns_updated_record_without_mutating_original():
    original = MissionPlanningAuditRecord(
        command="cmd",
        available_robots=[],
        tool_schema=None,
        llm_tool_call=None,
        decisions=[],
        final_status="error",
        final_message="blocked",
        created_at="2026-07-04T00:00:00+00:00",
    )
    decision = GuardDecision(
        layer="validator",
        status="block",
        reason="mission_plan_invalid",
        message="Mission plan failed deterministic validation.",
        details={"errors": ["Robot r2 is not registered."]},
    )

    updated = append_guard_decision(
        original,
        decision,
        final_status="blocked",
        final_message="Mission plan failed deterministic validation.",
    )

    assert original.decisions == []
    assert updated.decisions == [decision]
    assert updated.final_status == "blocked"
    assert updated.final_message == "Mission plan failed deterministic validation."
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_planning_audit.py -q
```

Expected: FAIL because `fireclaw_core.mission.mission_planning_audit` does not exist.

- [ ] **Step 3: Add the audit module**

Create `src/fireclaw_core/mission/mission_planning_audit.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from fireclaw_core.agent.robot_registry import RobotRegistryEntry


@dataclass(frozen=True)
class GuardDecision:
    layer: str
    status: str
    reason: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MissionPlanningAuditRecord:
    command: str
    available_robots: list[dict[str, Any]]
    tool_schema: dict[str, Any] | None
    llm_tool_call: dict[str, Any] | None
    decisions: list[GuardDecision]
    final_status: str
    final_message: str
    created_at: str
    mission_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "available_robots": [dict(robot) for robot in self.available_robots],
            "tool_schema": self.tool_schema,
            "llm_tool_call": self.llm_tool_call,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "final_status": self.final_status,
            "final_message": self.final_message,
            "created_at": self.created_at,
            "mission_id": self.mission_id,
        }


class MissionPlanningAuditSink(Protocol):
    def record(self, record: MissionPlanningAuditRecord) -> None:
        ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_available_robot_snapshot(robots: list[RobotRegistryEntry]) -> list[dict[str, Any]]:
    return [
        {
            "robot_id": robot.robot_id,
            "capabilities": list(robot.capabilities),
            "enabled": robot.enabled,
            "zone": robot.zone,
        }
        for robot in robots
    ]


def append_guard_decision(
    record: MissionPlanningAuditRecord,
    decision: GuardDecision,
    *,
    final_status: str,
    final_message: str,
    mission_id: str | None = None,
) -> MissionPlanningAuditRecord:
    return replace(
        record,
        decisions=[*record.decisions, decision],
        final_status=final_status,
        final_message=final_message,
        mission_id=mission_id if mission_id is not None else record.mission_id,
    )
```

- [ ] **Step 4: Add `audit_record` to `MissionPlanningResult`**

In `src/fireclaw_core/mission/mission_planner.py`, update the dataclass:

```python
from fireclaw_core.mission.mission_planning_audit import MissionPlanningAuditRecord
```

```python
@dataclass(frozen=True)
class MissionPlanningResult:
    status: str
    message: str
    intent: str | None = None
    plan: MissionPlan | None = None
    audit_record: MissionPlanningAuditRecord | None = None
```

- [ ] **Step 5: Run audit model tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_planning_audit.py -q
```

Expected: PASS.

## Task 2: Dynamic Tool Schema And Preflight Guard

**Files:**
- Modify: `src/fireclaw_core/planner/llm_planner.py`
- Test: `tests/test_llm_planner.py`

- [ ] **Step 1: Write failing tests for constrained schema and no-robot preflight**

In `tests/test_llm_planner.py`, add imports:

```python
from fireclaw_core.planner.llm_planner import build_constrained_mission_plan_tool
```

Append tests:

```python
def test_constrained_mission_plan_tool_adds_robot_id_enum_without_mutating_base_tool():
    ctx = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("patrol",)),
    )

    tool = build_constrained_mission_plan_tool(ctx)

    robot_id_schema = tool["function"]["parameters"]["properties"]["subtasks"]["items"]["properties"]["robot_id"]
    assert robot_id_schema == {"type": "string", "enum": ["r1", "r2"]}
    base_robot_id_schema = MISSION_PLAN_TOOL["function"]["parameters"]["properties"]["subtasks"]["items"]["properties"]["robot_id"]
    assert base_robot_id_schema == {"type": "string"}


def test_llm_planner_blocks_without_provider_call_when_no_available_robots():
    provider = MagicMock()
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=MissionPlannerContext())

    assert result.status == "error"
    assert result.message == "No available robots for mission planning."
    provider.chat_completion.assert_not_called()
    assert result.audit_record is not None
    assert result.audit_record.tool_schema is None
    assert result.audit_record.llm_tool_call is None
    assert result.audit_record.final_status == "error"
    assert result.audit_record.decisions[0].layer == "preflight"
    assert result.audit_record.decisions[0].status == "block"
    assert result.audit_record.decisions[0].reason == "no_available_robots"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py::test_constrained_mission_plan_tool_adds_robot_id_enum_without_mutating_base_tool tests/test_llm_planner.py::test_llm_planner_blocks_without_provider_call_when_no_available_robots -q
```

Expected: FAIL because the helper and preflight guard do not exist.

- [ ] **Step 3: Implement constrained schema helper and preflight guard**

In `src/fireclaw_core/planner/llm_planner.py`, add imports:

```python
from copy import deepcopy
from fireclaw_core.mission.mission_planning_audit import (
    GuardDecision,
    MissionPlanningAuditRecord,
    build_available_robot_snapshot,
    utc_now_iso,
)
```

Add helper after `MISSION_PLAN_TOOL`:

```python
def build_constrained_mission_plan_tool(context: MissionPlannerContext) -> dict[str, Any]:
    tool = deepcopy(MISSION_PLAN_TOOL)
    robot_ids = [robot.robot_id for robot in context.available_robots]
    robot_id_schema = (
        tool["function"]["parameters"]["properties"]["subtasks"]["items"]["properties"]["robot_id"]
    )
    if robot_ids:
        robot_id_schema["enum"] = robot_ids
    return tool
```

At the start of `LLMMissionPlanner.plan()`, after `context` is normalized and before building provider messages, add:

```python
        if not context.available_robots:
            decision = GuardDecision(
                layer="preflight",
                status="block",
                reason="no_available_robots",
                message="No available robots for mission planning.",
                details={"available_robot_count": 0},
            )
            audit = MissionPlanningAuditRecord(
                command=command,
                available_robots=build_available_robot_snapshot(context.available_robots),
                tool_schema=None,
                llm_tool_call=None,
                decisions=[decision],
                final_status="error",
                final_message=decision.message,
                created_at=utc_now_iso(),
            )
            return MissionPlanningResult(
                status="error",
                message=decision.message,
                audit_record=audit,
            )
```

Then compute the tool once and pass it to either provider path:

```python
        mission_tool = build_constrained_mission_plan_tool(context)
```

Replace both `tools=[MISSION_PLAN_TOOL]` calls with:

```python
                    tools=[mission_tool],
```

Do not remove `MISSION_PLAN_TOOL`; tests still verify the base schema.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py::test_constrained_mission_plan_tool_adds_robot_id_enum_without_mutating_base_tool tests/test_llm_planner.py::test_llm_planner_blocks_without_provider_call_when_no_available_robots -q
```

Expected: PASS.

## Task 3: Parser Guard And Planner Audit Records

**Files:**
- Modify: `src/fireclaw_core/planner/llm_planner.py`
- Test: `tests/test_llm_planner.py`

- [ ] **Step 1: Write failing parser guard tests**

Append tests:

```python
def test_llm_planner_uses_constrained_schema_for_provider_call():
    ctx = _make_context(
        RobotRegistryEntry(robot_id="gazebo_turtlebot3", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(_make_tool_call_response(
        subtasks=[
            {
                "robot_id": "gazebo_turtlebot3",
                "command": "去2楼搜索受困人员",
                "floor": 2,
                "capability_required": "search_for_victims",
                "execution_group": 0,
            }
        ]
    ))
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=ctx)

    assert result.status == "planned"
    tool = provider.chat_completion.call_args.kwargs["tools"][0]
    robot_id_schema = tool["function"]["parameters"]["properties"]["subtasks"]["items"]["properties"]["robot_id"]
    assert robot_id_schema["enum"] == ["gazebo_turtlebot3"]


def test_llm_planner_blocks_unknown_robot_id_with_audit_record():
    ctx = _make_context(
        RobotRegistryEntry(robot_id="gazebo_turtlebot3", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(_make_tool_call_response(
        subtasks=[
            {
                "robot_id": "robot_001",
                "command": "去2楼搜索受困人员",
                "floor": 2,
                "capability_required": "search_for_victims",
                "execution_group": 0,
            }
        ]
    ))
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=ctx)

    assert result.status == "error"
    assert result.plan is None
    assert "不存在的机器人" in result.message
    assert result.audit_record is not None
    assert result.audit_record.llm_tool_call == {
        "id": "call_001",
        "name": "create_mission_plan",
        "arguments": {
            "intent": "search",
            "subtasks": [
                {
                    "robot_id": "robot_001",
                    "command": "去2楼搜索受困人员",
                    "floor": 2,
                    "capability_required": "search_for_victims",
                    "execution_group": 0,
                }
            ],
        },
    }
    assert result.audit_record.decisions[-1].layer == "parser"
    assert result.audit_record.decisions[-1].status == "block"
    assert result.audit_record.decisions[-1].reason == "unknown_robot_id"


def test_llm_planner_records_parser_allow_for_valid_plan():
    ctx = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(_make_tool_call_response())
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=ctx)

    assert result.status == "planned"
    assert result.audit_record is not None
    assert result.audit_record.final_status == "planned"
    assert result.audit_record.final_message == result.message
    assert result.audit_record.tool_schema is not None
    assert result.audit_record.decisions[-1].reason == "mission_plan_parsed"
    assert result.audit_record.decisions[-1].status == "allow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py::test_llm_planner_uses_constrained_schema_for_provider_call tests/test_llm_planner.py::test_llm_planner_blocks_unknown_robot_id_with_audit_record tests/test_llm_planner.py::test_llm_planner_records_parser_allow_for_valid_plan -q
```

Expected: FAIL because parser audit is not returned yet.

- [ ] **Step 3: Pass schema and command into parser**

Change the parser signature in `src/fireclaw_core/planner/llm_planner.py`:

```python
    def _parse_response(
        self,
        response: ChatCompletion,
        context: MissionPlannerContext,
        *,
        command: str,
        tool_schema: dict[str, Any],
    ) -> MissionPlanningResult:
```

Change the call site:

```python
        result = self._parse_response(
            response,
            context,
            command=command,
            tool_schema=mission_tool,
        )
```

- [ ] **Step 4: Add local audit helpers inside `_parse_response()`**

At the top of `_parse_response()`, add:

```python
        available_robots = build_available_robot_snapshot(context.available_robots)
        decisions: list[GuardDecision] = []
        llm_tool_call: dict[str, Any] | None = None

        def make_result(
            *,
            status: str,
            message: str,
            decision: GuardDecision,
            intent: str | None = None,
            plan: MissionPlan | None = None,
        ) -> MissionPlanningResult:
            audit = MissionPlanningAuditRecord(
                command=command,
                available_robots=available_robots,
                tool_schema=tool_schema,
                llm_tool_call=llm_tool_call,
                decisions=[*decisions, decision],
                final_status=status,
                final_message=message,
                created_at=utc_now_iso(),
            )
            return MissionPlanningResult(
                status=status,
                message=message,
                intent=intent,
                plan=plan,
                audit_record=audit,
            )
```

- [ ] **Step 5: Convert parser error returns into guarded results**

For no tool call:

```python
        if not response.tool_calls:
            return make_result(
                status="error",
                message="LLM 未返回工具调用。",
                decision=GuardDecision(
                    layer="parser",
                    status="block",
                    reason="missing_tool_call",
                    message="LLM did not return a mission planning tool call.",
                ),
            )
```

After `tool_call = response.tool_calls[0]`, capture the raw call and check tool name:

```python
        tool_call = response.tool_calls[0]
        arguments = tool_call.arguments
        llm_tool_call = {
            "id": tool_call.id,
            "name": tool_call.name,
            "arguments": arguments,
        }
        if tool_call.name != "create_mission_plan":
            return make_result(
                status="error",
                message=f"LLM 调用了未知工具：{tool_call.name}",
                decision=GuardDecision(
                    layer="parser",
                    status="block",
                    reason="unexpected_tool_name",
                    message="LLM called an unexpected tool.",
                    details={"tool_name": tool_call.name},
                ),
            )
```

For invalid intent:

```python
        if intent not in VALID_INTENTS:
            return make_result(
                status="error",
                message=f"LLM 返回了无效的意图：{intent}",
                decision=GuardDecision(
                    layer="parser",
                    status="block",
                    reason="invalid_intent",
                    message="LLM returned an invalid mission intent.",
                    details={"intent": intent},
                ),
            )
```

For empty subtasks:

```python
        if not isinstance(raw_subtasks, list) or not raw_subtasks:
            return make_result(
                status="error",
                message="LLM 未返回子任务列表。",
                decision=GuardDecision(
                    layer="parser",
                    status="block",
                    reason="empty_subtasks",
                    message="LLM did not return a non-empty subtask list.",
                ),
            )
```

- [ ] **Step 6: Make robot ID parsing fail closed**

Replace the subtask loop body with:

```python
        for index, item in enumerate(raw_subtasks):
            if not isinstance(item, dict):
                return make_result(
                    status="error",
                    message="LLM 返回了无效的子任务。",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="invalid_subtask",
                        message="LLM returned a non-object subtask.",
                        details={"index": index},
                    ),
                )
            robot_id = str(item.get("robot_id") or "")
            if not robot_id:
                return make_result(
                    status="error",
                    message="LLM 子任务缺少 robot_id。",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="missing_robot_id",
                        message="LLM subtask did not include robot_id.",
                        details={"index": index},
                    ),
                )
            if robot_id not in known_robot_ids:
                return make_result(
                    status="error",
                    message=f"LLM 指定了不存在的机器人：{robot_id}",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="unknown_robot_id",
                        message="LLM selected a robot outside the available robot set.",
                        details={
                            "index": index,
                            "robot_id": robot_id,
                            "known_robot_ids": sorted(known_robot_ids),
                        },
                    ),
                )
            command_value = str(item.get("command") or "")
            if not command_value:
                return make_result(
                    status="error",
                    message="LLM 子任务缺少 command。",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="missing_command",
                        message="LLM subtask did not include command.",
                        details={"index": index, "robot_id": robot_id},
                    ),
                )
            try:
                floor = int(item.get("floor", 0))
            except (TypeError, ValueError):
                floor = 0
            if floor <= 0:
                return make_result(
                    status="error",
                    message=f"LLM 返回了无效楼层：{item.get('floor')}",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="invalid_floor",
                        message="LLM subtask returned an invalid floor.",
                        details={"index": index, "robot_id": robot_id, "floor": item.get("floor")},
                    ),
                )
            capability_required = str(item.get("capability_required") or "")
            if not capability_required:
                return make_result(
                    status="error",
                    message="LLM 子任务缺少 capability_required。",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="missing_capability",
                        message="LLM subtask did not include capability_required.",
                        details={"index": index, "robot_id": robot_id},
                    ),
                )
            try:
                execution_group = int(item.get("execution_group", 0))
            except (TypeError, ValueError):
                execution_group = -1
            if execution_group < 0:
                return make_result(
                    status="error",
                    message=f"LLM 返回了无效执行组：{item.get('execution_group')}",
                    decision=GuardDecision(
                        layer="parser",
                        status="block",
                        reason="invalid_execution_group",
                        message="LLM subtask returned an invalid execution_group.",
                        details={"index": index, "robot_id": robot_id, "execution_group": item.get("execution_group")},
                    ),
                )
            subtasks.append(
                MissionSubtask(
                    robot_id=robot_id,
                    command=command_value,
                    floor=floor,
                    capability_required=capability_required,
                    execution_group=execution_group,
                )
            )
```

- [ ] **Step 7: Return parser allow audit for valid plans**

Replace the final success return with:

```python
        message = f"已生成任务计划：{len(subtasks)} 个子任务，{plan.execution_groups} 个执行组。"
        return make_result(
            status="planned",
            message=message,
            intent=intent,
            plan=plan,
            decision=GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
                details={"subtask_count": len(subtasks), "execution_groups": plan.execution_groups},
            ),
        )
```

- [ ] **Step 8: Run parser audit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_planner.py::test_llm_planner_uses_constrained_schema_for_provider_call tests/test_llm_planner.py::test_llm_planner_blocks_unknown_robot_id_with_audit_record tests/test_llm_planner.py::test_llm_planner_records_parser_allow_for_valid_plan -q
```

Expected: PASS.

## Task 4: MissionAgent Validator Audit And Sink Recording

**Files:**
- Modify: `src/fireclaw_core/mission/mission_agent.py`
- Test: `tests/test_mission_agent.py`

- [ ] **Step 1: Write failing MissionAgent audit tests**

Add imports to `tests/test_mission_agent.py`:

```python
from fireclaw_core.mission.mission_planning_audit import GuardDecision, MissionPlanningAuditRecord
```

Add helper classes near existing fakes:

```python
class FakeAuditSink:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FailingAuditSink:
    def record(self, record):
        raise RuntimeError("audit unavailable")
```

Append tests:

```python
def test_mission_agent_records_validator_allow_decision_before_dispatch():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search_for_victims",),
            )
        ]
    )
    client = FakeSubagentClient()
    audit_sink = FakeAuditSink()
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索受困人员",
        available_robots=[{"robot_id": "robot-1", "capabilities": ["search_for_victims"], "enabled": True, "zone": None}],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-04T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-1",
                    command="去2楼搜索受困人员",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
    )

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1")

    assert result["status"] == "accepted"
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    assert record.mission_id == "mission-1"
    assert record.final_status == "planned"
    assert record.decisions[-1].layer == "validator"
    assert record.decisions[-1].status == "allow"
    assert record.decisions[-1].reason == "mission_plan_valid"
    assert client.calls


def test_mission_agent_records_validator_block_decision_without_dispatching():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search_for_victims",),
            )
        ]
    )
    client = FakeSubagentClient()
    audit_sink = FakeAuditSink()
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索受困人员",
        available_robots=[{"robot_id": "robot-1", "capabilities": ["search_for_victims"], "enabled": True, "zone": None}],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-04T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-1",
                    command="去2楼搜索受困人员",
                    floor=0,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
    )

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1")

    assert result["status"] == "blocked"
    assert "Subtask floor must be positive" in result["errors"][0]
    assert client.calls == []
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    assert record.final_status == "blocked"
    assert record.decisions[-1].layer == "validator"
    assert record.decisions[-1].status == "block"
    assert record.decisions[-1].reason == "mission_plan_invalid"
    assert "errors" in record.decisions[-1].details


def test_mission_agent_blocks_allowed_plan_when_audit_sink_fails():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search_for_victims",),
            )
        ]
    )
    client = FakeSubagentClient()
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-1",
                    command="去2楼搜索受困人员",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=MissionPlanningAuditRecord(
            command="去二楼搜索受困人员",
            available_robots=[],
            tool_schema={"type": "function"},
            llm_tool_call=None,
            decisions=[],
            final_status="planned",
            final_message="planned",
            created_at="2026-07-04T00:00:00+00:00",
        ),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=FailingAuditSink(),
    )

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1")

    assert result["status"] == "blocked"
    assert result["message"] == "Mission planning audit could not be recorded."
    assert result["subtask_results"] == []
    assert client.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_agent_records_validator_allow_decision_before_dispatch tests/test_mission_agent.py::test_mission_agent_records_validator_block_decision_without_dispatching tests/test_mission_agent.py::test_mission_agent_blocks_allowed_plan_when_audit_sink_fails -q
```

Expected: FAIL because `MissionAgent` has no `mission_planning_audit_sink` argument and does not record audit decisions.

- [ ] **Step 3: Wire audit sink into MissionAgent**

In `src/fireclaw_core/mission/mission_agent.py`, import:

```python
from fireclaw_core.mission.mission_planning_audit import (
    GuardDecision,
    MissionPlanningAuditRecord,
    MissionPlanningAuditSink,
    append_guard_decision,
)
```

Add `mission_planning_audit_sink` to `MissionAgent.__init__`:

```python
        mission_planning_audit_sink: MissionPlanningAuditSink | None = None,
```

Store it:

```python
        self.mission_planning_audit_sink = mission_planning_audit_sink
```

- [ ] **Step 4: Add helper methods for finalizing and recording audit**

Add methods inside `MissionAgent`:

```python
    def _record_mission_planning_audit(
        self,
        record: MissionPlanningAuditRecord | None,
    ) -> str | None:
        if record is None or self.mission_planning_audit_sink is None:
            return None
        try:
            self.mission_planning_audit_sink.record(record)
            return None
        except Exception:
            logger.warning("Failed to record mission planning audit", exc_info=True)
            return "Mission planning audit could not be recorded."

    def _with_validator_decision(
        self,
        record: MissionPlanningAuditRecord | None,
        *,
        validation_errors: list[str],
        final_status: str,
        final_message: str,
        mission_id: str | None,
    ) -> MissionPlanningAuditRecord | None:
        if record is None:
            return None
        if validation_errors:
            decision = GuardDecision(
                layer="validator",
                status="block",
                reason="mission_plan_invalid",
                message="Mission plan failed deterministic validation.",
                details={"errors": list(validation_errors)},
            )
        else:
            decision = GuardDecision(
                layer="validator",
                status="allow",
                reason="mission_plan_valid",
                message="Mission plan passed deterministic validation.",
            )
        return append_guard_decision(
            record,
            decision,
            final_status=final_status,
            final_message=final_message,
            mission_id=mission_id,
        )
```

- [ ] **Step 5: Record blocked planner results**

In `plan_and_submit()`, before returning a non-planned result:

```python
        if planning_result.status != "planned" or planning_result.plan is None:
            audit_error = self._record_mission_planning_audit(planning_result.audit_record)
            response = {
                "status": planning_result.status,
                "message": planning_result.message,
                "subtask_results": [],
            }
            if audit_error is not None:
                response["audit_warning"] = audit_error
            return response
```

- [ ] **Step 6: Record validator block decisions**

Replace the validation block with:

```python
        validation_errors = MissionPlanValidator().validate(planning_result.plan, self.registry)
        mission_id = _mission_id(session_id)
        if validation_errors:
            audit_record = self._with_validator_decision(
                planning_result.audit_record,
                validation_errors=validation_errors,
                final_status="blocked",
                final_message="Mission plan failed deterministic validation.",
                mission_id=mission_id,
            )
            audit_error = self._record_mission_planning_audit(audit_record)
            response = {
                "status": "blocked",
                "message": "Mission plan failed deterministic validation.",
                "errors": validation_errors,
                "subtask_results": [],
            }
            if audit_error is not None:
                response["audit_warning"] = audit_error
            return response
```

Remove the later duplicate `mission_id = _mission_id(session_id)` assignment or keep only one assignment before validation.

- [ ] **Step 7: Record validator allow decision before dispatch**

After validation succeeds and before creating mission registry records, add:

```python
        audit_record = self._with_validator_decision(
            planning_result.audit_record,
            validation_errors=[],
            final_status=planning_result.status,
            final_message=planning_result.message,
            mission_id=mission_id,
        )
        audit_error = self._record_mission_planning_audit(audit_record)
        if audit_error is not None:
            return {
                "status": "blocked",
                "message": audit_error,
                "subtask_results": [],
            }
```

- [ ] **Step 8: Run MissionAgent audit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_agent_records_validator_allow_decision_before_dispatch tests/test_mission_agent.py::test_mission_agent_records_validator_block_decision_without_dispatching tests/test_mission_agent.py::test_mission_agent_blocks_allowed_plan_when_audit_sink_fails -q
```

Expected: PASS.

## Task 5: Regression Sweep

**Files:**
- No new production files.
- Tests across planner, mission agent, task contracts, and gateway structured task path.

- [ ] **Step 1: Run focused planner and audit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_planning_audit.py tests/test_llm_planner.py tests/test_mission_agent.py -q
```

Expected: PASS.

- [ ] **Step 2: Run mission and structured task regression group**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_contract.py tests/test_robot_agent_runtime.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_subagent_client.py -q
```

Expected: PASS. This protects the primitive-composition and structured-task fixes from 2026-06-16.

- [ ] **Step 3: Run provider/runtime regression group**

Run:

```bash
.venv/bin/python -m pytest tests/test_provider.py tests/test_provider_runtime.py tests/test_llm_planner.py -q
```

Expected: PASS. This protects the proxy/trust-env and provider-runtime behavior.

- [ ] **Step 4: Run full test suite if focused groups pass**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS, with any existing skips unchanged.

## Self-Review

- Spec coverage:
  - No available robots are blocked before provider calls in Task 2.
  - Runtime schema constrains `robot_id.enum` in Task 2.
  - Unknown robot IDs are parser-blocked even with fail-closed logic in Task 3.
  - Parser and validator decisions are captured in Tasks 3 and 4.
  - Audit record includes command, available robots, schema, tool call, decisions, final status, timestamp, and mission ID in Tasks 1, 3, and 4.
  - Audit sink failure blocks allowed plans in Task 4.
- Placeholder scan:
  - No unfinished placeholder steps are present.
- Type consistency:
  - `MissionPlanningResult.audit_record` uses `MissionPlanningAuditRecord | None`.
  - `MissionAgent.__init__` receives `MissionPlanningAuditSink | None`.
  - `append_guard_decision()` returns a new frozen dataclass instance and does not mutate existing audit records.
