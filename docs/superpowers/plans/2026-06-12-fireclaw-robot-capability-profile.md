# FireClaw Robot Capability Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an OpenClaw-like robot capability profile layer so FireClaw can maintain robot skills, LLM-visible tool schemas, ROS1 bindings, robot registry entries, and robot-local gateway paths from one source of truth.

**Architecture:** Keep provider/model config separate from robot capabilities, following OpenClaw's pattern where model runtime config and tool construction are distinct concerns. Add a `RobotCapabilityProfile` loader that combines profile TOML, existing `SkillRegistry` metadata, and existing ROS1 remaps into a validated runtime profile. Let robot-local LLM planning receive concrete skill tools such as `navigate_to_floor` in addition to the existing `create_robot_local_plan` wrapper, while the executor still routes through `SafetyGate`, `PlanExecutor`, and the adapter boundary.

**Tech Stack:** Python 3.11, stdlib `tomllib`, existing `SkillRegistry`, existing `tool_schema.py`, existing `Ros1AdapterConfig`, existing `ProviderRuntime`, pytest.

---

## OpenClaw References Inspected

- `/home/nankai/fireclaw/openclaw/src/agents/openclaw-tools.ts`
  - `createOpenClawTools(...)` collects tool factories from config, plugins, workspace, session context, and policy gates before presenting tools to model runs.
  - Pattern to reuse: model provider config is not the tool binding; tool visibility is resolved from runtime config and context.
- `/home/nankai/fireclaw/openclaw/src/plugins/tools.ts`
  - Plugin tools expose a stable `execute(...)` boundary and are resolved before invocation.
  - Pattern to reuse: tool metadata and execution remain separated, and validation happens before execution.
- `/home/nankai/fireclaw/openclaw/src/gateway/server-methods/tools-invoke.js|ts`
  - Gateway-side tool invocation is a controlled runtime boundary, not arbitrary model output execution.
  - Pattern to reuse: FireClaw LLM chooses skills, but `SafetyGate` and adapter execution remain authoritative.
- `/home/nankai/fireclaw/openclaw/openclaw.mjs`
  - Config path resolution searches user/state config paths and separates launcher config from runtime tools.
  - Pattern to reuse later: FireClaw can add user-level robot profiles, but this plan keeps repo-local TOML first.

## Current FireClaw Findings

- `src/fireclaw_core/execution/skills.py` already has structured skill metadata and input/output schemas.
- `src/fireclaw_core/devtools/tool_schema.py` can convert skill metadata into OpenAI-compatible function tool schemas.
- `src/fireclaw_core/agent/robot_agent.py` currently gives the LLM only one tool, `create_robot_local_plan`; concrete skills are listed as strings under `allowed_skills`.
- `src/fireclaw_core/task/task_contract.py` hard-codes capability-to-skills mapping in `_skills_from_capability()`.
- `src/fireclaw_core/ros/ros1_config.py` parses ROS1 remaps, but those remaps are not unified with `SkillRegistry` or LLM tool exposure.
- `src/fireclaw_core/devtools/fleet_doctor.py` can warn about missing ROS1 remaps, but this is diagnostic only.

## Target Profile Shape

The first supported profile is TOML:

```toml
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"]
```

Derived outputs:

- robot registry entry for `data/mission/robots.json`;
- robot gateway paths for `memory.jsonl`, `events.jsonl`, `tasks.jsonl`;
- LLM-visible skill tools from `SkillRegistry.list_metadata()`;
- ROS1 remap validation against `Ros1AdapterConfig.endpoints`;
- allowed skill envelope for robot-local planning.

## File Structure

- Create `src/fireclaw_core/agent/robot_profile.py`
  - Owns `RobotCapabilityProfile`, TOML loading, registry-entry generation, gateway-path generation, and profile validation.
- Create `src/fireclaw_core/agent/robot_tools.py`
  - Owns conversion from a `SkillRegistry` plus exposed skill names to LLM tool schemas.
  - Owns conversion from direct skill tool calls into `RobotLocalPlan`.
- Modify `src/fireclaw_core/agent/robot_agent.py`
  - Allow `LLMRobotAgentPlanner` to pass concrete skill tools to the provider.
  - Accept either `create_robot_local_plan` or direct skill tool calls.
- Modify `src/fireclaw_core/gateway/config.py`
  - Load `[robot_gateway].profile_path`.
- Modify `src/fireclaw_core/gateway/gateway.py`
  - Load profile during robot gateway startup.
  - Use profile paths and robot id when present.
  - Add `skill_tools` and `skill_metadata` to robot-local LLM context.
- Modify `src/fireclaw_core/task/task_contract.py`
  - Add profile-backed capability-to-skills resolution while preserving the current default mapping.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Add `robot-profile export` to write a registry entry from the configured profile.
- Modify `fireclaw.example.toml`
  - Add `profile_path` to `[robot_gateway]`.
- Create `examples/robot_profiles/gazebo_turtlebot3.toml`
  - Provides the first maintained profile for current Gazebo debugging.
- Tests:
  - `tests/test_robot_profile.py`
  - `tests/test_robot_tools.py`
  - `tests/test_robot_agent_planner.py`
  - `tests/test_gateway_robot_agent_cli.py`
  - `tests/test_task_contract.py`
  - `tests/test_mission_cli.py`

---

### Task 1: Robot Capability Profile Contract

**Files:**
- Create: `src/fireclaw_core/agent/robot_profile.py`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing tests for profile loading**

Add `tests/test_robot_profile.py`:

```python
from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.robot_profile import (
    RobotCapabilityProfile,
    load_robot_capability_profile,
)


def test_load_robot_capability_profile_from_toml(tmp_path: Path) -> None:
    profile_path = tmp_path / "debug-robot-1.toml"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile == RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims", "report_status"),
    )


def test_profile_derives_gateway_paths_and_registry_entry(tmp_path: Path) -> None:
    profile_path = tmp_path / "debug-robot-1.toml"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor"]
""".strip(),
        encoding="utf-8",
    )
    profile = load_robot_capability_profile(profile_path)

    assert profile.memory_path == Path("data/robots/debug-robot-1/memory.jsonl")
    assert profile.event_path == Path("data/robots/debug-robot-1/events.jsonl")
    assert profile.task_queue_path == Path("data/robots/debug-robot-1/tasks.jsonl")
    assert profile.to_robot_registry_entry() == {
        "robot_id": "debug-robot-1",
        "base_url": "http://127.0.0.1:8765",
        "capabilities": ["search_for_victims"],
        "enabled": True,
    }
```

- [ ] **Step 2: Run profile tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'fireclaw_core.agent.robot_profile'`.

- [ ] **Step 3: Implement profile dataclass and loader**

Create `src/fireclaw_core/agent/robot_profile.py`:

```python
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class RobotCapabilityProfile:
    robot_id: str
    base_url: str
    adapter: str
    ros1_config: str | None
    data_dir: Path
    capabilities: tuple[str, ...]
    enabled_skills: tuple[str, ...]
    llm_exposed_skills: tuple[str, ...]
    enabled: bool = True

    @property
    def memory_path(self) -> Path:
        return self.data_dir / "memory.jsonl"

    @property
    def event_path(self) -> Path:
        return self.data_dir / "events.jsonl"

    @property
    def task_queue_path(self) -> Path:
        return self.data_dir / "tasks.jsonl"

    def to_robot_registry_entry(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "base_url": self.base_url,
            "capabilities": list(self.capabilities),
            "enabled": self.enabled,
        }


def load_robot_capability_profile(path: str | Path) -> RobotCapabilityProfile:
    target = Path(path)
    with target.open("rb") as handle:
        raw = tomllib.load(handle)
    robot = raw.get("robot")
    if not isinstance(robot, dict):
        raise ValueError("robot profile requires a [robot] table.")
    robot_id = _required_string(robot, "id")
    base_url = _required_string(robot, "base_url")
    adapter = _required_string(robot, "adapter")
    data_dir = Path(_required_string(robot, "data_dir"))
    capabilities = _string_tuple(robot, "capabilities")
    enabled_skills = _string_tuple(robot, "enabled_skills")
    llm_exposed_skills = _string_tuple(robot, "llm_exposed_skills")
    return RobotCapabilityProfile(
        robot_id=robot_id,
        base_url=base_url.rstrip("/"),
        adapter=adapter,
        ros1_config=_optional_string(robot, "ros1_config"),
        data_dir=data_dir,
        capabilities=capabilities,
        enabled_skills=enabled_skills,
        llm_exposed_skills=llm_exposed_skills,
        enabled=bool(robot.get("enabled", True)),
    )


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"robot.{key} must be a non-empty string.")
    return value.strip()


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"robot.{key} must be a non-empty string when provided.")
    return value.strip()


def _string_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"robot.{key} must be a non-empty list of strings.")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(items) != len(value):
        raise ValueError(f"robot.{key} must contain only non-empty strings.")
    return items
```

- [ ] **Step 4: Run profile tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/agent/robot_profile.py tests/test_robot_profile.py
git commit -m "feat: add robot capability profile contract"
```

---

### Task 2: Profile Validation Against Skills and ROS1 Remaps

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing validation tests**

Append to `tests/test_robot_profile.py`:

```python
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.execution.skills import create_default_skill_registry
from fireclaw_core.agent.robot_profile import validate_robot_capability_profile
from fireclaw_core.ros.ros1_config import load_ros1_adapter_config


def test_profile_validation_rejects_unknown_enabled_skill() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "unknown_skill"),
        llm_exposed_skills=("navigate_to_floor",),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    errors = validate_robot_capability_profile(profile, registry)

    assert "enabled skill 'unknown_skill' is not registered" in errors


def test_profile_validation_rejects_ros1_skill_without_remap() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "assess_victim"),
        llm_exposed_skills=("navigate_to_floor", "assess_victim"),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    errors = validate_robot_capability_profile(profile, registry, ros1_config=ros1_config)

    assert "enabled skill 'assess_victim' has no ROS1 remap" in errors


def test_profile_validation_accepts_gazebo_profile() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    errors = validate_robot_capability_profile(profile, registry, ros1_config=ros1_config)

    assert errors == []
```

- [ ] **Step 2: Run validation tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py::test_profile_validation_rejects_unknown_enabled_skill tests/test_robot_profile.py::test_profile_validation_rejects_ros1_skill_without_remap tests/test_robot_profile.py::test_profile_validation_accepts_gazebo_profile -q
```

Expected: fail with `ImportError` for `validate_robot_capability_profile`.

- [ ] **Step 3: Implement profile validation**

Append to `src/fireclaw_core/agent/robot_profile.py`:

```python
from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig


def validate_robot_capability_profile(
    profile: RobotCapabilityProfile,
    registry: SkillRegistry,
    *,
    ros1_config: Ros1AdapterConfig | None = None,
) -> list[str]:
    errors: list[str] = []
    registered = registry.names()
    for skill_name in profile.enabled_skills:
        if skill_name not in registered:
            errors.append(f"enabled skill {skill_name!r} is not registered")
    for skill_name in profile.llm_exposed_skills:
        if skill_name not in profile.enabled_skills:
            errors.append(f"LLM-exposed skill {skill_name!r} is not in enabled_skills")
    if profile.adapter == "ros1":
        if profile.ros1_config is None:
            errors.append("ros1 profile requires robot.ros1_config")
        if ros1_config is not None:
            remapped = set(ros1_config.endpoints)
            for skill_name in profile.enabled_skills:
                if skill_name not in remapped:
                    errors.append(f"enabled skill {skill_name!r} has no ROS1 remap")
    if not profile.capabilities:
        errors.append("profile must declare at least one capability")
    return errors
```

- [ ] **Step 4: Run validation tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/agent/robot_profile.py tests/test_robot_profile.py
git commit -m "feat: validate robot capability profiles"
```

---

### Task 3: Build LLM Skill Tools From SkillRegistry

**Files:**
- Create: `src/fireclaw_core/agent/robot_tools.py`
- Test: `tests/test_robot_tools.py`

- [ ] **Step 1: Write failing tests for skill tool generation**

Create `tests/test_robot_tools.py`:

```python
from __future__ import annotations

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.robot_tools import (
    build_robot_skill_tools,
    local_plan_from_direct_tool_calls,
)
from fireclaw_core.execution.skills import create_default_skill_registry


def test_build_robot_skill_tools_filters_and_preserves_input_schema() -> None:
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    tools = build_robot_skill_tools(
        registry,
        exposed_skill_names=("navigate_to_floor", "report_status"),
    )

    names = [tool["function"]["name"] for tool in tools]
    assert names == ["navigate_to_floor", "report_status"]
    nav = tools[0]["function"]
    assert nav["parameters"]["properties"]["floor"]["type"] == "integer"
    assert nav["parameters"]["required"] == ["floor"]
    assert tools[0]["x-fireclaw"]["domain"] == "navigation"


def test_local_plan_from_direct_tool_calls_preserves_order() -> None:
    calls = [
        {"name": "navigate_to_floor", "arguments": {"floor": 2}},
        {"name": "report_status", "arguments": {"floor": 2}},
    ]

    plan = local_plan_from_direct_tool_calls(calls, intent="search")

    assert plan.intent == "search"
    assert [step.skill_name for step in plan.steps] == ["navigate_to_floor", "report_status"]
    assert plan.steps[0].inputs == {"floor": 2}
```

- [ ] **Step 2: Run robot tools tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_tools.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'fireclaw_core.agent.robot_tools'`.

- [ ] **Step 3: Implement robot tool helpers**

Create `src/fireclaw_core/agent/robot_tools.py`:

```python
from __future__ import annotations

from typing import Any

from fireclaw_core.agent.robot_agent import RobotLocalPlan, RobotLocalPlanStep
from fireclaw_core.devtools.tool_schema import skill_metadata_to_tool_schema
from fireclaw_core.execution.skills import SkillRegistry


def build_robot_skill_tools(
    registry: SkillRegistry,
    *,
    exposed_skill_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    exposed = set(exposed_skill_names)
    tools: list[dict[str, Any]] = []
    for metadata in registry.list_metadata():
        if metadata["name"] in exposed:
            tools.append(skill_metadata_to_tool_schema(metadata))
    return sorted(tools, key=lambda item: str(item["function"]["name"]))


def local_plan_from_direct_tool_calls(
    calls: list[dict[str, Any]],
    *,
    intent: str,
) -> RobotLocalPlan:
    steps = [
        RobotLocalPlanStep(
            skill_name=str(call["name"]),
            inputs=dict(call.get("arguments") or {}),
            reason="Direct robot skill tool call from LLM.",
        )
        for call in calls
    ]
    return RobotLocalPlan(
        intent=intent,
        steps=steps,
        rationale="LLM selected concrete robot skill tools.",
        confidence=None,
    )
```

- [ ] **Step 4: Run robot tools tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_tools.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/agent/robot_tools.py tests/test_robot_tools.py
git commit -m "feat: expose robot skills as LLM tools"
```

---

### Task 4: Let Robot-Local LLM Use Concrete Skill Tools

**Files:**
- Modify: `src/fireclaw_core/agent/robot_agent.py`
- Test: `tests/test_robot_agent_planner.py`

- [ ] **Step 1: Write failing planner tests for direct skill tool calls**

Append to `tests/test_robot_agent_planner.py`:

```python
def test_llm_robot_agent_planner_accepts_direct_skill_tool_calls():
    runtime = FakeProviderRuntime(
        tool_calls=[
            FakeToolCall("navigate_to_floor", {"floor": 2}),
            FakeToolCall("report_status", {"floor": 2}),
        ]
    )
    planner = LLMRobotAgentPlanner(runtime)
    envelope = _envelope()
    skill_tools = [
        {
            "type": "function",
            "function": {
                "name": "navigate_to_floor",
                "description": "Navigate robot to a target floor.",
                "parameters": {"type": "object", "properties": {"floor": {"type": "integer"}}, "required": ["floor"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "report_status",
                "description": "Report status to operator.",
                "parameters": {"type": "object", "properties": {"floor": {"type": "integer"}}, "required": ["floor"]},
            },
        },
    ]

    plan = planner.plan(envelope, context={"skill_tools": skill_tools})

    assert [step.skill_name for step in plan.steps] == ["navigate_to_floor", "report_status"]
    assert plan.steps[0].inputs == {"floor": 2}
    assert runtime.last_tools[0]["function"]["name"] == "create_robot_local_plan"
    assert runtime.last_tools[1]["function"]["name"] == "navigate_to_floor"
```

If `FakeProviderRuntime` does not retain `last_tools`, extend its fake `chat_completion()` in the same test file:

```python
self.last_tools = tools
```

- [ ] **Step 2: Run planner test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_planner.py::test_llm_robot_agent_planner_accepts_direct_skill_tool_calls -q
```

Expected: fail with `unexpected tool call 'navigate_to_floor'`.

- [ ] **Step 3: Modify `LLMRobotAgentPlanner.plan()`**

In `src/fireclaw_core/agent/robot_agent.py`, import:

```python
from fireclaw_core.agent.robot_tools import local_plan_from_direct_tool_calls
```

Replace the `tools=[ROBOT_LOCAL_PLAN_TOOL]` argument with:

```python
skill_tools = context.get("skill_tools") if isinstance(context, dict) else None
tools = [ROBOT_LOCAL_PLAN_TOOL]
if isinstance(skill_tools, list):
    tools.extend(tool for tool in skill_tools if isinstance(tool, dict))
```

Pass `tools=tools` into `chat_completion(...)`.

Replace the tool-call parsing block with:

```python
if not response.tool_calls:
    raise RobotAgentPlannerError("LLM did not return a robot-local plan tool call")
tool_call = response.tool_calls[0]
if tool_call.name == "create_robot_local_plan":
    return _local_plan_from_arguments(tool_call.arguments)
allowed_direct = {
    tool["function"]["name"]
    for tool in tools[1:]
    if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
}
direct_calls = [
    {"name": call.name, "arguments": call.arguments}
    for call in response.tool_calls
]
if direct_calls and all(call["name"] in allowed_direct for call in direct_calls):
    return local_plan_from_direct_tool_calls(direct_calls, intent=envelope.task_type)
raise RobotAgentPlannerError(f"unexpected tool call {tool_call.name!r}")
```

- [ ] **Step 4: Run planner tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_planner.py tests/test_robot_agent_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/agent/robot_agent.py tests/test_robot_agent_planner.py
git commit -m "feat: allow robot-local LLM direct skill tools"
```

---

### Task 5: Load Profile in Robot Gateway and Add Skill Tools to Context

**Files:**
- Modify: `src/fireclaw_core/gateway/config.py`
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Test: `tests/test_gateway_robot_agent_cli.py`
- Test: `tests/test_gateway_structured_task.py`

- [ ] **Step 1: Write failing config test for profile path**

Append to `tests/test_gateway_robot_agent_cli.py`:

```python
def test_config_loads_robot_gateway_profile_path(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[robot_gateway]
profile_path = "examples/robot_profiles/gazebo_turtlebot3.toml"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["robot_gateway_profile_path"] == "examples/robot_profiles/gazebo_turtlebot3.toml"
```

- [ ] **Step 2: Write failing gateway context test**

Append to `tests/test_gateway_structured_task.py`:

```python
def test_gateway_robot_agent_context_includes_skill_tools(tmp_path):
    captured_context = {}

    class CapturingPlanner:
        def plan(self, envelope, *, context, cancellation_requested=None):
            captured_context.update(context)
            from fireclaw_core.agent.robot_agent import RobotLocalPlan, RobotLocalPlanStep
            return RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
                    RobotLocalPlanStep("report_status", {"floor": 2}),
                ],
            )

    from fireclaw_core.agent.robot_agent import RobotAgentRuntime

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="debug-robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.robot_agent_runtime = RobotAgentRuntime(planner=CapturingPlanner())
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去二楼救人",
                "structured_task": {
                    "task_id": "task-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                },
            },
        )
        _wait_for_task_done(gateway.base_url, accepted["task_id"])
    finally:
        gateway.stop()

    names = [tool["function"]["name"] for tool in captured_context["skill_tools"]]
    assert names == ["navigate_to_floor", "report_status"]
    assert captured_context["skill_metadata"][0]["name"] == "navigate_to_floor"
```

- [ ] **Step 3: Run gateway tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_agent_cli.py::test_config_loads_robot_gateway_profile_path tests/test_gateway_structured_task.py::test_gateway_robot_agent_context_includes_skill_tools -q
```

Expected:

- config test fails with `KeyError: 'robot_gateway_profile_path'`;
- gateway context test fails with `KeyError: 'skill_tools'`.

- [ ] **Step 4: Load profile path in config**

In `src/fireclaw_core/gateway/config.py`, inside the `[robot_gateway]` load block, add:

```python
cfg["robot_gateway_profile_path"] = rg.get("profile_path")
```

- [ ] **Step 5: Add profile path to `GatewayConfig`**

In `src/fireclaw_core/gateway/gateway.py`, add to `GatewayConfig`:

```python
robot_profile_path: str | None = None
```

In `main(...)`, merge CLI/config:

```python
"robot_gateway_profile_path": args.robot_profile,
```

Add parser argument:

```python
parser.add_argument("--robot-profile", default=None, help="Path to robot capability profile TOML.")
```

Pass to `GatewayConfig`:

```python
robot_profile_path=merged.get("robot_gateway_profile_path"),
```

- [ ] **Step 6: Build skill tools in `_run_robot_agent_structured_task()`**

Import:

```python
from fireclaw_core.agent.robot_tools import build_robot_skill_tools
```

In `_run_robot_agent_structured_task(...)`, before `context = {...}`, add:

```python
exposed_skill_names = tuple(
    skill_name
    for skill_name in agent.registry.names()
    if skill_name in set(task_object.required_skills) | {"report_status", "return_to_safe_zone"}
)
skill_tools = build_robot_skill_tools(
    agent.registry,
    exposed_skill_names=exposed_skill_names,
)
skill_metadata = [
    metadata
    for metadata in agent.registry.list_metadata()
    if metadata["name"] in exposed_skill_names
]
```

Extend `context`:

```python
"skill_tools": skill_tools,
"skill_metadata": skill_metadata,
```

- [ ] **Step 7: Run gateway tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/fireclaw_core/gateway/config.py src/fireclaw_core/gateway/gateway.py tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py
git commit -m "feat: pass robot skill tools to local agent"
```

---

### Task 6: Profile-Backed Capability-to-Skills Resolution

**Files:**
- Modify: `src/fireclaw_core/task/task_contract.py`
- Test: `tests/test_task_contract.py`

- [ ] **Step 1: Write failing tests for profile-backed skill chains**

Append to `tests/test_task_contract.py`:

```python
from fireclaw_core.task.task_contract import skills_from_capability


def test_skills_from_capability_uses_profile_skill_chain():
    chains = {
        "search_for_victims": ["navigate_to_floor", "search_for_victims", "report_status"],
        "return_to_safe_zone": ["return_to_safe_zone"],
    }

    assert skills_from_capability("search_for_victims", capability_skill_chains=chains) == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]


def test_skills_from_capability_preserves_default_mapping():
    assert skills_from_capability("search_for_victims") == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]
```

- [ ] **Step 2: Run task contract tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py::test_skills_from_capability_uses_profile_skill_chain tests/test_task_contract.py::test_skills_from_capability_preserves_default_mapping -q
```

Expected: fail with `ImportError` for `skills_from_capability`.

- [ ] **Step 3: Add public resolver**

In `src/fireclaw_core/task/task_contract.py`, add:

```python
def skills_from_capability(
    capability: str,
    *,
    capability_skill_chains: dict[str, list[str]] | None = None,
) -> list[str]:
    if capability_skill_chains is not None and capability in capability_skill_chains:
        return list(capability_skill_chains[capability])
    return _skills_from_capability(capability)
```

Change `structured_task_from_mission_subtask(...)` signature:

```python
def structured_task_from_mission_subtask(
    *,
    mission_id: str,
    subtask: MissionSubtask,
    operator_id: str | None = None,
    task_id: str | None = None,
    capability_skill_chains: dict[str, list[str]] | None = None,
) -> StructuredRobotTask:
```

Replace:

```python
required_skills = _skills_from_capability(subtask.capability_required)
```

with:

```python
required_skills = skills_from_capability(
    subtask.capability_required,
    capability_skill_chains=capability_skill_chains,
)
```

- [ ] **Step 4: Run task contract tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/task/task_contract.py tests/test_task_contract.py
git commit -m "feat: support profile-backed capability skill chains"
```

---

### Task 7: Robot Profile Export Command for `robots.json`

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_mission_cli.py`

- [ ] **Step 1: Write failing CLI export test**

Append to `tests/test_mission_cli.py`:

```python
def test_mission_cli_robot_profile_export_writes_robot_registry(tmp_path):
    profile_path = tmp_path / "robot.toml"
    output_path = tmp_path / "robots.json"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core",
            "robot-profile",
            "export",
            "--profile",
            str(profile_path),
            "--output",
            str(output_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    body = json.loads(completed.stdout)
    registry = json.loads(output_path.read_text(encoding="utf-8"))
    assert body["status"] == "written"
    assert registry["robots"][0]["robot_id"] == "debug-robot-1"
    assert registry["robots"][0]["capabilities"] == ["search_for_victims"]
```

- [ ] **Step 2: Run CLI export test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_mission_cli_robot_profile_export_writes_robot_registry -q
```

Expected: command exits non-zero because `robot-profile` is not a known subcommand.

- [ ] **Step 3: Add `robot-profile export` command**

In `src/fireclaw_core/mission/mission_cli.py`, add `robot-profile` to `KNOWN_SUBCOMMANDS` in `src/fireclaw_core/__main__.py`:

```python
"robot-profile",
```

In `mission_cli.main()`, add parser:

```python
robot_profile = subparsers.add_parser("robot-profile", help="Manage robot capability profiles.")
robot_profile_sub = robot_profile.add_subparsers(dest="robot_profile_command", required=True)
profile_export = robot_profile_sub.add_parser("export", help="Export a profile to robots.json.")
profile_export.add_argument("--profile", required=True, help="Path to robot profile TOML.")
profile_export.add_argument("--output", required=True, help="Path to robots.json output.")
```

Add handler:

```python
if args.command_name == "robot-profile":
    return _handle_robot_profile(args)
```

Add function:

```python
def _handle_robot_profile(args: argparse.Namespace) -> int:
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    if args.robot_profile_command == "export":
        profile = load_robot_capability_profile(args.profile)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"robots": [profile.to_robot_registry_entry()]}
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_json({"status": "written", "output": str(output), "robot_id": profile.robot_id})
        return 0
    print(f"Error: unknown robot-profile subcommand: {args.robot_profile_command}", file=sys.stderr)
    return 1
```

- [ ] **Step 4: Run CLI tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_mission_cli_robot_profile_export_writes_robot_registry tests/test_gateway_robot_agent_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/__main__.py src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: export robot registry from capability profile"
```

---

### Task 8: Example Gazebo Profile and Config Integration

**Files:**
- Create: `examples/robot_profiles/gazebo_turtlebot3.toml`
- Modify: `fireclaw.example.toml`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing example profile test**

Append to `tests/test_robot_profile.py`:

```python
def test_example_gazebo_turtlebot3_profile_loads_and_validates() -> None:
    profile = load_robot_capability_profile("examples/robot_profiles/gazebo_turtlebot3.toml")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id=profile.robot_id))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    assert profile.robot_id == "gazebo_turtlebot3"
    assert profile.adapter == "ros1"
    assert "navigate_to_floor" in profile.llm_exposed_skills
    assert validate_robot_capability_profile(profile, registry, ros1_config=ros1_config) == []
```

- [ ] **Step 2: Run example profile test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py::test_example_gazebo_turtlebot3_profile_loads_and_validates -q
```

Expected: fail with `FileNotFoundError` for `examples/robot_profiles/gazebo_turtlebot3.toml`.

- [ ] **Step 3: Create example profile**

Create `examples/robot_profiles/gazebo_turtlebot3.toml`:

```toml
[robot]
id = "gazebo_turtlebot3"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml"
data_dir = "data/robots/gazebo_turtlebot3"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"]
```

- [ ] **Step 4: Add profile path to example config**

In `fireclaw.example.toml`, under `[robot_gateway]`, add:

```toml
profile_path = "examples/robot_profiles/gazebo_turtlebot3.toml"
```

- [ ] **Step 5: Run example profile tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py tests/test_gateway_robot_agent_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add examples/robot_profiles/gazebo_turtlebot3.toml fireclaw.example.toml tests/test_robot_profile.py
git commit -m "docs: add gazebo turtlebot3 robot profile"
```

---

### Task 9: Documentation and Debugging Workflow Update

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Test: command probes only

- [ ] **Step 1: Update README robot gateway section**

Replace the robot gateway startup section with:

```markdown
Start the robot-local gateway from a profile-backed config:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
```

Export the profile into the mission registry:

```bash
.venv/bin/python -m fireclaw_core robot-profile export \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/mission/robots.json
```
```

- [ ] **Step 2: Update Gazebo guide commands**

In `docs/deployment/ros1-gazebo-debugging-guide.md`, replace stale `python -m fireclaw_core.gateway` commands with:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
```

Replace stale robot registry instructions with:

```bash
.venv/bin/python -m fireclaw_core robot-profile export \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/mission/robots.json
```

Replace `turtlebo3_navigation.launch` with:

```bash
roslaunch turtlebot3_navigation turtlebot3_navigation.launch
```

- [ ] **Step 3: Run documentation command probes**

Run:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --help
.venv/bin/python -m fireclaw_core robot-profile export --help
.venv/bin/python -m fireclaw_core serve --help
```

Expected: each command exits `0`.

- [ ] **Step 4: Scan docs for stale commands**

Run:

```bash
rg -n "fireclaw_core.gateway|mission_cli submit-subtask|turtlebo3_navigation" README.md docs/deployment/ros1-gazebo-debugging-guide.md
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/deployment/ros1-gazebo-debugging-guide.md
git commit -m "docs: document profile-backed robot gateway workflow"
```

---

### Task 10: Focused Verification Suite

**Files:**
- No production files modified.
- Verification only.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_robot_profile.py \
  tests/test_robot_tools.py \
  tests/test_robot_agent_planner.py \
  tests/test_gateway_structured_task.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_task_contract.py \
  tests/test_mission_cli.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run existing integration tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_embodied_gateway_e2e.py \
  tests/test_mission_agent_structured_task.py \
  tests/test_subagent_client.py \
  tests/test_ros1_config.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run CLI probes**

Run:

```bash
.venv/bin/python -m fireclaw_core --help
.venv/bin/python -m fireclaw_core robot-gateway --help
.venv/bin/python -m fireclaw_core.gateway --help
.venv/bin/python -m fireclaw_core robot-profile export --help
```

Expected: each command exits `0`.

- [ ] **Step 4: Record memory handoff**

Append to `memory/2026-06-12/fireclaw-debugging-readiness-check.md`:

```markdown
## Update 2026-06-12 — Robot Capability Profile Plan Executed

- Added robot capability profile contract.
- Added profile validation against SkillRegistry and ROS1 remaps.
- Added concrete robot skill tools for robot-local LLM planning.
- Added profile-backed robot registry export.
- Added Gazebo TurtleBot3 profile.
- Focused verification:
  - <paste exact commands and pass/fail counts>
```

- [ ] **Step 5: Commit memory update**

```bash
git add memory/2026-06-12/fireclaw-debugging-readiness-check.md
git commit -m "docs: record robot capability profile implementation"
```

---

## Self-Review

- Spec coverage: The plan covers profile contract, ROS1 remap validation, concrete skill tool exposure to LLM, gateway context integration, capability-to-skills resolution, profile export to `robots.json`, examples, docs, and verification.
- Placeholder scan: No placeholder tokens are intentionally left for implementers. Each code-changing task includes concrete file paths, code snippets, commands, and expected results.
- Type consistency: `RobotCapabilityProfile`, `load_robot_capability_profile`, `validate_robot_capability_profile`, `build_robot_skill_tools`, `local_plan_from_direct_tool_calls`, and `skills_from_capability` are introduced before later tasks use them.
- Research impact: This plan changes FireClaw from a collection of hand-maintained config files into a profile-backed capability system. That is a stronger architecture for embodied-agent research because experiments can report a robot's exposed capabilities, ROS bindings, and LLM-visible tools as an auditable artifact instead of implicit code/config state.
