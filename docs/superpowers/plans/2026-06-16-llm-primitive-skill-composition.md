# LLM Primitive Skill Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let FireClaw's robot-local LLM compose available primitive robot skills directly, while still allowing composite firefighting skills and enforcing safety before execution.

**Architecture:** Mirror OpenClaw's `createOpenClawTools()` pattern conceptually: expose a bounded runtime tool/skill inventory to the model, let the model choose or compose tools, then validate through policy before execution. FireClaw adapts this for robotics by separating primitive skills from composite skills, validating robot support from the profile/runtime state, and blocking unsafe or out-of-envelope plans instead of free-form trial-and-error.

**Tech Stack:** Python 3.11, pytest, FireClaw mission/robot agent modules, TOML robot profiles, ROS1 adapter config, existing `ProviderRuntime` tool-calling path.

---

## OpenClaw Analogue Checked

- `openclaw-main/src/agents/openclaw-tools.ts::createOpenClawTools()` builds the tool inventory exposed to the model from core tools, optional tools, config, allow/deny lists, hooks, workspace context, and runtime state.
- FireClaw should not copy OpenClaw's UI/channel assumptions. The reusable structure is:
  - build runtime tool inventory from config and runtime state;
  - expose that inventory to the model;
  - wrap calls with policy/hooks;
  - let specialized skills coexist with primitive tools.

FireClaw-specific adaptation:

- Primitive robot skills are physical actions and therefore require typed inputs, preconditions, sensor requirements, runtime support checks, safety policy, cancellation, and audit logs.
- Composite skills are reusable workflows such as `search_for_victims` or `firefight`; they are not required for every possible user phrasing.

---

## File Structure

- Modify `src/fireclaw_core/execution/skills.py`
  - Add skill metadata fields for `kind`, `primitive_capability`, `input_schema`, `safety_class`, and `requires_approval`.
  - Keep the existing default registry API compatible.
- Modify `src/fireclaw_core/agent/robot_profile.py`
  - Parse profile sections for primitive skill support and composite capability chains.
  - Keep old `capabilities` and `capability_skill_chains` as compatibility aliases.
- Modify `examples/robot_profiles/gazebo_turtlebot3.toml`
  - Declare primitive support explicitly, such as `navigate_to_floor`, `report_status`, `return_to_safe_zone`.
- Create `src/fireclaw_core/agent/skill_inventory.py`
  - Build the OpenClaw-like runtime inventory visible to robot-local LLM.
  - Filter by profile, robot adapter support, sensor health, and operator policy.
- Modify `src/fireclaw_core/task/task_contract.py`
  - Allow mission subtasks to carry `allowed_skills` generated from primitive inventory when no composite capability matches.
- Modify `src/fireclaw_core/mission/mission_agent.py`
  - For unknown high-level capabilities, dispatch a robot-local task with a broad but bounded primitive skill envelope instead of returning `clarify`.
  - Do not allow dispatch when no robot has required primitive support for the requested motion/perception domain.
- Modify `src/fireclaw_core/agent/robot_agent.py`
  - Improve `build_robot_agent_messages()` so the LLM sees primitive and composite skills separately.
  - Validate the produced plan against allowed skills, input schemas, target envelope, and safety class.
- Modify `src/fireclaw_core/planner/llm_planner.py`
  - Keep mission-level LLM focused on robot selection and high-level routing.
  - Add hard constraints for robot IDs and avoid making every user intent a capability enum.
- Modify `tests/test_execution_skills.py`
  - Add primitive/composite metadata tests.
- Modify `tests/test_robot_profile.py`
  - Add profile parsing compatibility and primitive support tests.
- Create `tests/test_skill_inventory.py`
  - Test runtime inventory construction and filtering.
- Modify `tests/test_robot_agent.py`
  - Add LLM primitive composition tests.
- Modify `tests/test_mission_agent.py`
  - Add end-to-end dispatch tests for unknown user tasks that can be solved with primitives.

---

### Task 1: Add Skill Kind And Primitive Metadata

**Files:**
- Modify: `src/fireclaw_core/execution/skills.py`
- Test: `tests/test_execution_skills.py`

- [ ] **Step 1: Write failing test for primitive/composite metadata**

Add this test to `tests/test_execution_skills.py`:

```python
def test_default_skill_registry_marks_primitive_and_composite_skills():
    from fireclaw_core.agent.robot import DryRunRobotAdapter
    from fireclaw_core.execution.skills import create_default_skill_registry

    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="r1"))

    navigate = registry.get("navigate_to_floor")
    search = registry.get("search_for_victims")

    assert navigate is not None
    assert search is not None
    assert navigate.metadata["kind"] == "primitive"
    assert navigate.metadata["primitive_capability"] == "navigation"
    assert navigate.metadata["input_schema"]["required"] == ["floor"]
    assert search.metadata["kind"] == "composite"
    assert search.metadata["primitive_capability"] == "perception"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution_skills.py::test_default_skill_registry_marks_primitive_and_composite_skills -q
```

Expected: FAIL because existing skill metadata does not contain `kind` and `primitive_capability`.

- [ ] **Step 3: Add metadata to built-in skills**

In `src/fireclaw_core/execution/skills.py`, extend the built-in skill definitions so these skills have metadata:

```python
{
    "kind": "primitive",
    "primitive_capability": "navigation",
    "input_schema": {
        "type": "object",
        "properties": {"floor": {"type": "integer", "minimum": 1}},
        "required": ["floor"],
    },
    "safety_class": "motion",
    "requires_approval": False,
}
```

Use this classification:

- `navigate_to_floor`: primitive, `navigation`, safety class `motion`
- `report_status`: primitive, `communication`, safety class `reporting`
- `return_to_safe_zone`: composite for now, `navigation`, safety class `motion`
- `search_for_victims`: composite, `perception`, safety class `perception`
- `assess_victim`: composite, `perception`, safety class `perception`
- `emergency_stop`: primitive, `safety`, safety class `emergency`

Keep old metadata keys untouched.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution_skills.py::test_default_skill_registry_marks_primitive_and_composite_skills -q
```

Expected: PASS.

---

### Task 2: Parse Primitive Skill Support From Robot Profiles

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Modify: `examples/robot_profiles/gazebo_turtlebot3.toml`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing profile parsing test**

Add this test to `tests/test_robot_profile.py`:

```python
def test_robot_profile_parses_primitive_skills(tmp_path):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "r1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
capabilities = ["search_for_victims"]
primitive_skills = ["navigate_to_floor", "report_status"]

[capability_skill_chains]
search_for_victims = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    profile = load_robot_capability_profile(profile_path)

    assert profile.primitive_skills == ("navigate_to_floor", "report_status")
    assert "search_for_victims" in profile.capabilities
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py::test_robot_profile_parses_primitive_skills -q
```

Expected: FAIL because `primitive_skills` is not parsed.

- [ ] **Step 3: Add `primitive_skills` field**

In `src/fireclaw_core/agent/robot_profile.py`, add an immutable field to the profile dataclass:

```python
primitive_skills: tuple[str, ...] = ()
```

When loading TOML, parse:

```python
primitive_skills = tuple(str(item) for item in robot_table.get("primitive_skills", []) if str(item).strip())
```

Validation rule:

- every `primitive_skills` entry must exist in the runtime skill registry;
- every listed primitive must have metadata `kind == "primitive"`;
- old `capabilities` remains accepted for mission-level composite labels.

- [ ] **Step 4: Update Gazebo profile**

In `examples/robot_profiles/gazebo_turtlebot3.toml`, add:

```toml
primitive_skills = ["navigate_to_floor", "report_status", "return_to_safe_zone"]
```

Keep existing `capabilities = ["search_for_victims", "patrol"]` for compatibility during migration.

- [ ] **Step 5: Run profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py tests/test_gateway_robot_profile_config.py -q
```

Expected: PASS.

---

### Task 3: Build Runtime Skill Inventory Like OpenClaw Tools

**Files:**
- Create: `src/fireclaw_core/agent/skill_inventory.py`
- Test: `tests/test_skill_inventory.py`
- Modify: `src/fireclaw_core/gateway/gateway.py`

- [ ] **Step 1: Write failing inventory test**

Create `tests/test_skill_inventory.py`:

```python
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.skill_inventory import build_robot_skill_inventory
from fireclaw_core.execution.skills import create_default_skill_registry


def test_build_robot_skill_inventory_exposes_profile_primitives():
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    inventory = build_robot_skill_inventory(
        registry=registry,
        primitive_skills=("navigate_to_floor", "report_status"),
        composite_chains={"search_for_victims": ("navigate_to_floor", "search_for_victims", "report_status")},
        verified_sensors={"lidar"},
    )

    names = [item["name"] for item in inventory["skills"]]
    assert names == ["navigate_to_floor", "report_status", "search_for_victims"]
    navigate = next(item for item in inventory["skills"] if item["name"] == "navigate_to_floor")
    assert navigate["kind"] == "primitive"
    assert navigate["primitive_capability"] == "navigation"
    assert navigate["input_schema"]["required"] == ["floor"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_skill_inventory.py -q
```

Expected: FAIL because `skill_inventory.py` does not exist.

- [ ] **Step 3: Implement inventory builder**

Create `src/fireclaw_core/agent/skill_inventory.py`:

```python
from __future__ import annotations

from typing import Any

from fireclaw_core.execution.skills import SkillRegistry


def build_robot_skill_inventory(
    *,
    registry: SkillRegistry,
    primitive_skills: tuple[str, ...],
    composite_chains: dict[str, tuple[str, ...]],
    verified_sensors: set[str],
) -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    added: set[str] = set()

    for name in primitive_skills:
        skill = registry.get(name)
        if skill is None:
            continue
        metadata = dict(skill.metadata)
        if metadata.get("kind") != "primitive":
            continue
        skills.append(_inventory_item(name, metadata, verified_sensors))
        added.add(name)

    for capability, chain in composite_chains.items():
        if capability in added:
            continue
        skills.append({
            "name": capability,
            "kind": "composite",
            "chain": list(chain),
            "description": f"Composite capability {capability}",
            "input_schema": {
                "type": "object",
                "properties": {"floor": {"type": "integer", "minimum": 1}},
                "required": ["floor"],
            },
            "available": all(step in primitive_skills or registry.get(step) is not None for step in chain),
        })
        added.add(capability)

    return {"skills": skills}


def _inventory_item(name: str, metadata: dict[str, Any], verified_sensors: set[str]) -> dict[str, Any]:
    required_sensors = set(metadata.get("required_sensors") or [])
    return {
        "name": name,
        "kind": metadata.get("kind", "primitive"),
        "primitive_capability": metadata.get("primitive_capability"),
        "description": metadata.get("description", name),
        "input_schema": metadata.get("input_schema", {"type": "object"}),
        "safety_class": metadata.get("safety_class", "unknown"),
        "requires_approval": bool(metadata.get("requires_approval", False)),
        "required_sensors": sorted(required_sensors),
        "available": required_sensors.issubset(verified_sensors),
    }
```

- [ ] **Step 4: Wire inventory into gateway robot-agent context**

In `src/fireclaw_core/gateway/gateway.py`, where robot-local context currently builds `skill_tools`, add `skill_inventory`:

```python
context["skill_inventory"] = build_robot_skill_inventory(
    registry=agent.registry,
    primitive_skills=self.robot_profile.primitive_skills if self.robot_profile else tuple(agent.registry.names()),
    composite_chains=self.robot_profile.capability_skill_chains if self.robot_profile else {},
    verified_sensors=set(self.robot.get_robot_state().available_sensors),
)
```

If the exact local variable names differ, keep the same data shape.

- [ ] **Step 5: Run inventory and gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_skill_inventory.py tests/test_gateway_robot_profile_config.py tests/test_mission_agent.py -q
```

Expected: PASS.

---

### Task 4: Let Mission Agent Dispatch Primitive-Bounded Tasks When No Composite Matches

**Files:**
- Modify: `src/fireclaw_core/mission/mission_agent.py`
- Modify: `src/fireclaw_core/task/task_contract.py`
- Test: `tests/test_mission_agent.py`

- [ ] **Step 1: Write failing mission dispatch test**

Add this test to `tests/test_mission_agent.py`:

```python
def test_plan_and_submit_dispatches_navigation_request_to_robot_local_primitives(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="gazebo_turtlebot3",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = FakeSubagentClient()
    planner = FakeMissionPlanner(MissionPlanningResult(status="clarify", message="unknown intent"))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
    )

    result = mission.plan_and_submit("导航到 x=2 y=0", session_id="m1")

    assert result["status"] in {"planned", "succeeded", "accepted"}
    submitted = client.submitted_tasks[0]
    assert submitted["structured_task"]["task_type"] == "primitive_composition"
    assert "navigate_to_floor" in submitted["structured_task"]["allowed_skills"]
```

If `FakeSubagentClient` stores submissions under a different property, adapt the assertion to the existing helper.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_plan_and_submit_dispatches_navigation_request_to_robot_local_primitives -q
```

Expected: FAIL because mission agent currently returns `clarify`.

- [ ] **Step 3: Add primitive fallback routing**

In `MissionAgent.plan_and_submit()`:

1. Try the configured mission planner.
2. If planner returns `planned`, keep current behavior.
3. If planner returns `clarify`, inspect online robots.
4. If at least one online robot exposes navigation primitive support, create one structured task:

```python
structured_task = {
    "task_id": f"{mission_id}:{robot_id}:primitive-composition",
    "mission_id": mission_id,
    "robot_id": robot_id,
    "task_type": "primitive_composition",
    "command": command,
    "target": {},
    "required_skills": [],
    "allowed_skills": list(primitive_skills),
    "risk_level": "low",
    "constraints": {"source": "mission_primitive_fallback"},
}
```

5. Submit to robot-gateway unchanged; robot-local LLM now performs the actual composition.

Do not dispatch if no robot is online.
Do not dispatch if no primitive skills are available.

- [ ] **Step 4: Preserve safety boundary**

If command contains high-risk keywords such as `灭火`, `破拆`, `进入危险区域`, `开阀`, and no matching composite exists, return:

```python
{
    "status": "clarify",
    "message": "该任务需要专用复合技能或人工确认，不能仅靠 primitive skills 自动执行。",
    "subtask_results": [],
}
```

- [ ] **Step 5: Run mission agent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py -q
```

Expected: PASS.

---

### Task 5: Improve Robot-Local LLM Prompt And Policy For Primitive Composition

**Files:**
- Modify: `src/fireclaw_core/agent/robot_agent.py`
- Test: `tests/test_robot_agent.py`

- [ ] **Step 1: Write failing prompt test**

Add this test to `tests/test_robot_agent.py`:

```python
def test_robot_agent_messages_describe_primitive_and_composite_inventory():
    from fireclaw_core.agent.robot_agent import build_robot_agent_messages
    from fireclaw_core.task.task_contract import RobotAgentTaskEnvelope

    envelope = RobotAgentTaskEnvelope(
        task_id="t1",
        mission_id="m1",
        robot_id="r1",
        command="导航到 x=2 y=0",
        task_type="primitive_composition",
        target={},
        allowed_skills=("navigate_to_floor", "report_status"),
        required_skills=(),
        constraints={},
        risk_level="low",
    )

    messages = build_robot_agent_messages(
        envelope,
        context={
            "skill_inventory": {
                "skills": [
                    {"name": "navigate_to_floor", "kind": "primitive", "primitive_capability": "navigation"},
                    {"name": "search_for_victims", "kind": "composite", "chain": ["navigate_to_floor", "search_for_victims"]},
                ]
            }
        },
    )

    text = messages[1]["content"]
    assert "primitive" in text
    assert "composite" in text
    assert "navigate_to_floor" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent.py::test_robot_agent_messages_describe_primitive_and_composite_inventory -q
```

Expected: FAIL if prompt does not include the inventory clearly.

- [ ] **Step 3: Update robot-local prompt**

In `build_robot_agent_messages()`, include:

```python
"skill_inventory": context.get("skill_inventory", {}),
"planning_rules": [
    "优先使用可用 composite skill 完成明确的消防任务。",
    "没有合适 composite skill 时，可以组合 primitive skills。",
    "只能使用 allowed_skills 中的技能。",
    "不能扩大目标、楼层、区域、风险级别。",
    "运动类 primitive 必须保持在 target/constraints 允许范围内。",
    "不确定时返回空 steps 并说明需要澄清。"
]
```

- [ ] **Step 4: Add policy rejection for unsafe primitive plans**

Extend `RobotAgentPolicy.validate()`:

```python
if envelope.task_type == "primitive_composition" and not plan.steps:
    reasons.append("primitive composition produced no executable steps")

for step in plan.steps:
    if step.skill_name not in allowed:
        reasons.append(f"skill {step.skill_name!r} is outside allowed_skills")
```

For high-risk envelope:

```python
if envelope.risk_level in {"high", "critical"}:
    return RobotAgentPolicyDecision(status="approval_required", reasons=[...])
```

Keep existing floor checks.

- [ ] **Step 5: Run robot agent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent.py -q
```

Expected: PASS.

---

### Task 6: Add Direct Primitive Motion Vocabulary Without Creating New Capabilities

**Files:**
- Modify: `src/fireclaw_core/agent/robot_agent.py`
- Modify: `src/fireclaw_core/ros/ros1_config.py` if `navigate_to_pose` or `navigate_relative` endpoint mapping is added.
- Modify: `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml` only if adding `navigate_to_pose`.
- Test: `tests/test_robot_agent.py`

- [ ] **Step 1: Decide first primitive surface**

Implement only one new primitive in this pass:

```text
navigate_to_floor
```

Do not add `navigate_to_pose` yet unless the current ROS1 config already has an endpoint for arbitrary pose goals. The immediate goal is to stop creating mission capabilities for every phrasing, not to expand the ROS adapter surface.

- [ ] **Step 2: Write LLM parser regression using existing primitive**

Add a test with a fake provider that returns:

```json
{
  "intent": "navigation test",
  "steps": [
    {"skill_name": "navigate_to_floor", "inputs": {"floor": 2}},
    {"skill_name": "report_status", "inputs": {"floor": 2}}
  ]
}
```

The envelope command should be:

```text
去二楼做一次简单的规划运动
```

Assert the local plan contains those two primitive steps and passes policy.

- [ ] **Step 3: Run the test to verify it fails if prompt/policy is insufficient**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent.py::test_llm_robot_agent_plans_navigation_with_primitives -q
```

Expected before implementation: FAIL or missing test.

- [ ] **Step 4: Implement only the minimal prompt/schema change**

Ensure `LLMRobotAgentPlanner` can accept `create_robot_local_plan` output with primitive skill steps and that `RobotAgentPolicy.validate()` accepts it when the skills are allowed.

- [ ] **Step 5: Run the test**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent.py::test_llm_robot_agent_plans_navigation_with_primitives -q
```

Expected: PASS.

---

### Task 7: Gazebo End-To-End Verification

**Files:**
- No code changes unless tests reveal a bug.
- Runtime artifacts under `data/debug-gazebo/`.

- [ ] **Step 1: Start Gazebo**

Run:

```bash
source /opt/ros/noetic/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch turtlebot3_gazebo turtlebot3_world.launch
```

Expected: `/scan`, `/odom`, `/tf`, and `/clock` publish.

- [ ] **Step 2: Start full navigation stack**

Run:

```bash
source /opt/ros/noetic/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch turtlebot3_navigation turtlebot3_navigation.launch map_file:=$(rospack find turtlebot3_navigation)/maps/map.yaml
```

Expected:

```bash
rosnode list | rg 'amcl|map_server|move_base'
```

shows `/amcl`, `/map_server`, `/move_base`.

- [ ] **Step 3: Start robot-gateway with robot-local LLM enabled**

Run:

```bash
cd /home/nankai/fireclaw
source /opt/ros/noetic/setup.bash
.venv/bin/python -m fireclaw_core robot-gateway \
  --config fireclaw.toml \
  --robot-profile examples/robot_profiles/gazebo_turtlebot3.toml
```

Expected:

```json
{"status":"starting","robot_id":"gazebo_turtlebot3"}
```

- [ ] **Step 4: Start serve with LLM trace**

Run:

```bash
cd /home/nankai/fireclaw
.venv/bin/python -m fireclaw_core serve \
  --config fireclaw.toml \
  --robot-profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --data-dir data/debug-gazebo \
  --planner llm \
  --llm-trace-path data/debug-gazebo/traces/primitive-composition.jsonl
```

Expected: server listens on `http://127.0.0.1:8766`.

- [ ] **Step 5: Submit primitive-composition command**

Run:

```bash
curl -sS -X POST http://127.0.0.1:8766/missions \
  -H 'Content-Type: application/json' \
  -H 'X-Operator-Scopes: task.submit,task.cancel,state.read,mission.submit,mission.cancel,mission.plan,mission.read' \
  -d '{"command":"去二楼做一次简单的规划运动","operator":{"operator_id":"nankai","role":"operator"}}'
```

Expected:

- mission status is `succeeded` or accepted and later terminal state is completed;
- robot-local plan includes primitive steps;
- `/move_base/goal` receives a map-frame pose;
- `/move_base/result` returns `Goal reached`.

- [ ] **Step 6: Submit unknown-but-safe primitive command**

Run:

```bash
curl -sS -X POST http://127.0.0.1:8766/missions \
  -H 'Content-Type: application/json' \
  -H 'X-Operator-Scopes: task.submit,task.cancel,state.read,mission.submit,mission.cancel,mission.plan,mission.read' \
  -d '{"command":"做一次导航测试，到二楼目标点后上报状态","operator":{"operator_id":"nankai","role":"operator"}}'
```

Expected:

- no new mission capability is required;
- robot-local LLM composes primitives;
- safety policy allows only if steps are within `allowed_skills`.

- [ ] **Step 7: Submit unsafe unknown command**

Run:

```bash
curl -sS -X POST http://127.0.0.1:8766/missions \
  -H 'Content-Type: application/json' \
  -H 'X-Operator-Scopes: task.submit,task.cancel,state.read,mission.submit,mission.cancel,mission.plan,mission.read' \
  -d '{"command":"进入危险区域破拆障碍物","operator":{"operator_id":"nankai","role":"operator"}}'
```

Expected:

- response is `clarify`, `blocked`, or `approval_required`;
- it must not silently compose arbitrary motion primitives.

---

## Final Verification

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_execution_skills.py \
  tests/test_robot_profile.py \
  tests/test_skill_inventory.py \
  tests/test_robot_agent.py \
  tests/test_mission_agent.py \
  tests/test_llm_planner.py \
  tests/test_gateway_robot_profile_config.py \
  -q
```

Expected: all selected tests pass.

For Gazebo:

```bash
source /opt/ros/noetic/setup.bash
timeout 90 rostopic echo -n 1 /move_base/result --noarr
```

Expected:

```text
status: 3
text: "Goal reached."
```

---

## Self-Review

- Spec coverage: The plan covers primitive/composite skill split, profile support, OpenClaw-like inventory exposure, mission fallback, robot-local LLM composition, safety policy, and Gazebo verification.
- Placeholder scan: No `TBD` or open-ended implementation placeholders remain.
- Type consistency: Uses existing concepts `MissionAgent`, `RobotAgentTaskEnvelope`, `RobotLocalPlan`, `SkillRegistry`, `RobotRegistryEntry`, and `ProviderRuntime`.
- Scope check: This plan intentionally does not add arbitrary `navigate_to_pose` yet. That should be a follow-up once primitive composition works using the existing `navigate_to_floor` ROS1 path.
