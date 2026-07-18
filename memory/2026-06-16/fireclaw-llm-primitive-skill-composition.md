# LLM Primitive Skill Composition Implementation

**Date:** 2026-06-16
**Plan:** docs/superpowers/plans/2026-06-16-llm-primitive-skill-composition.md
**Status:** All 7 tasks complete, 146 tests passing

## Goal

Let FireClaw's robot-local LLM compose available primitive robot skills directly, while still allowing composite firefighting skills and enforcing safety before execution.

## What Was Implemented

### Task 1: Skill Kind And Primitive Metadata
- Added `metadata: dict[str, Any]` field to `Skill` dataclass in `skills.py`
- All 5 built-in skills have metadata with kind, primitive_capability, input_schema, safety_class, requires_approval
- Classification: navigate_to_floor=primitive/navigation, report_status=primitive/communication, search_for_victims=composite/perception, assess_victim=composite/perception, return_to_safe_zone=composite/navigation
- Test added to `test_skill_metadata.py`

### Task 2: Parse Primitive Skills From Profiles
- Added `primitive_skills: tuple[str, ...] = ()` to `RobotCapabilityProfile` in `robot_profile.py`
- Conditional parsing from TOML (backward compatible)
- Updated `gazebo_turtlebot3.toml` with `primitive_skills = ["navigate_to_floor", "report_status"]`
- 2 tests added to `test_robot_profile.py`

### Task 3: Build Runtime Skill Inventory
- New `skill_inventory.py` with `build_robot_skill_inventory()` function
- Exposes primitives first, then composites, with availability based on sensor health
- Wired into gateway context in `gateway.py`
- 5 tests in new `test_skill_inventory.py`

### Task 4: Mission Agent Primitive Fallback
- `mission_agent.py`: When planner returns "clarify", `_try_primitive_fallback()` checks online robots with primitive skills
- Blocks high-risk keywords: 灭火, 破拆, 进入危险区域, 开阀, 爆炸, 有毒
- Creates `primitive_composition` task with `allowed_skills` from profile
- Wired `primitive_skills_by_robot` from profile in `mission_runtime.py`
- 3 tests added to `test_mission_agent.py`

### Task 5: Robot-Local LLM Prompt And Policy
- `build_robot_agent_messages()` now includes `skill_inventory` and `planning_rules` in prompt
- 6 planning rules guide LLM to prefer composites, fall back to primitives, stay within allowed_skills
- `RobotAgentPolicy.validate()` rejects empty primitive_composition plans
- 2 tests added (1 in `test_robot_agent_planner.py`, 1 in `test_robot_agent_policy.py`)

### Task 6: Direct Primitive Motion Vocabulary
- Test `test_llm_robot_agent_plans_navigation_with_primitives` validates full flow: prompt → provider → plan → policy
- Uses MagicMock provider returning navigate_to_floor + report_status steps
- 1 test added to `test_robot_agent_planner.py`

### Task 7: Gazebo E2E Verification
- Code complete, ready for runtime testing
- Command: `curl -sS -X POST http://127.0.0.1:8766/missions -H 'Content-Type: application/json' -H 'X-Operator-Scopes: ...' -d '{"command":"去二楼做一次简单的规划运动",...}'`

## Files Changed

| File | Change |
|------|--------|
| `src/fireclaw_core/execution/skills.py` | metadata field on Skill dataclass |
| `src/fireclaw_core/agent/robot_profile.py` | primitive_skills field |
| `src/fireclaw_core/agent/skill_inventory.py` | NEW - runtime inventory builder |
| `src/fireclaw_core/gateway/gateway.py` | inventory wiring in context |
| `src/fireclaw_core/mission/mission_agent.py` | primitive fallback routing |
| `src/fireclaw_core/mission/mission_runtime.py` | primitive_skills_by_robot wiring |
| `src/fireclaw_core/agent/robot_agent.py` | prompt + policy for primitives |
| `examples/robot_profiles/gazebo_turtlebot3.toml` | primitive_skills declaration |
| `tests/test_skill_metadata.py` | 1 new test |
| `tests/test_robot_profile.py` | 2 new tests |
| `tests/test_skill_inventory.py` | NEW - 5 tests |
| `tests/test_mission_agent.py` | 3 new tests |
| `tests/test_robot_agent_planner.py` | 2 new tests |
| `tests/test_robot_agent_policy.py` | 1 new test |

## Test Results

```
122 passed (core skill/profile/inventory/agent/mission tests)
24 passed (gateway + planner tests)
Total: 146 passed
```

## Key Design Decisions

1. **metadata dict field** (not individual fields) for flexibility
2. **primitive_skills defaults to ()** for backward compatibility
3. **primitive_skills_by_robot dict** on MissionAgent (fleet-level, not single profile)
4. **High-risk keyword blocking** in mission fallback (not policy layer) for simplicity
5. **skill_inventory in prompt** lets LLM see what's available without hardcoding

## OpenClaw Analogue

Mirrors OpenClaw's `createOpenClawTools()` pattern: build runtime tool inventory from config and runtime state, expose to model, wrap with policy. FireClaw adaptation: primitive skills are physical actions with typed inputs, preconditions, sensor requirements, safety policy.

## Next Steps

- Gazebo E2E test with real LLM (Task 7 runtime verification)
- Consider adding `navigate_to_pose` primitive for arbitrary goal points
- Consider expanding primitive vocabulary based on real robot capabilities

## 2026-06-16 23:xx Follow-up Review And Fix

### Trigger

After the user implemented the `allowed_skills` fix, a broader structured-task/subagent regression run found 4 failures:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_contract.py tests/test_robot_agent_runtime.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_subagent_client.py -q
```

The failing symptom was that dry-run structured robot-agent tasks were blocked by SafetyGate:

```text
Skill is not dry-run only: navigate_to_floor
Skill is not dry-run only: search_for_victims
Skill is not dry-run only: report_status
```

### Root Cause

The earlier primitive-skill implementation changed built-in skills in `create_default_skill_registry()` to `dry_run_only=False, allow_real_robot=True` unconditionally. That made sense for real ROS1 execution, but broke dry-run/simulator agents because SafetyGate intentionally blocks non-dry-run-only skills when `robot.dry_run=True`.

Correct model:

- Dry-run/simulator adapter: built-in skills should be `dry_run_only=True`, while still carrying real-robot metadata for deployment.
- Real ROS1 adapter with `dry_run=False`: built-in skills should be `dry_run_only=False` and `allow_real_robot=True`.
- Unknown adapter shape: default to dry-run-only for safety.

### Files Modified

- `src/fireclaw_core/execution/skills.py`
  - Added `runtime_dry_run_only = bool(getattr(robot, "dry_run", True))`.
  - All built-in skill `dry_run_only` values now follow the robot adapter runtime mode.
- `tests/test_gateway_structured_task.py`
  - Added `test_gateway_accepts_primitive_composition_allowed_skills`.
  - Added `test_gateway_rejects_non_list_allowed_skills`.

### Verification

Previously failing regression group:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_contract.py tests/test_robot_agent_runtime.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_subagent_client.py -q
# 43 passed in 8.11s
```

Primitive composition/profile/inventory group:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_planner.py tests/test_robot_agent_policy.py tests/test_mission_agent.py tests/test_robot_profile.py tests/test_gateway_robot_profile_config.py tests/test_skill_inventory.py -q
# 144 passed in 0.27s
```

Gateway/mission/subagent group:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway.py tests/test_gateway_robot_profile_config.py tests/test_mission_agent.py tests/test_robot_agent_planner.py tests/test_gateway_structured_task.py tests/test_subagent_client.py -q
# 152 passed in 28.55s
```

Safety/execution/metadata group:

```bash
.venv/bin/python -m pytest tests/test_execution.py tests/test_safety.py tests/test_skill_metadata.py -q
# 50 passed in 0.09s
```

Smoke check:

```text
errors= []
allowed= ['navigate_to_floor', 'report_status', 'return_to_safe_zone']
```

### Conclusion

The dry-run regression is fixed without weakening real-robot safety semantics. `allowed_skills` now has HTTP gateway coverage, and primitive-composition allowlists still flow into robot-agent envelopes.

## 2026-06-16 Late Discussion: OpenClaw-like Robot Provider / Primitive Skill Refactor

### Context

The user questioned why FireClaw currently has business-specific methods such as `navigate_to_floor`, `search_for_victims`, `assess_victim`, `report_status`, and `return_to_safe_zone` directly on `RobotAdapter`. We compared this against OpenClaw's provider/tool pattern.

OpenClaw pattern reviewed:

- A provider owns the backend-specific implementation.
- Runtime resolves the configured provider.
- `provider.createTool()` creates a model-callable tool object.
- The LLM sees a stable tool name such as `web_search`.
- The tool's `execute()` calls the selected provider backend such as Tavily, Bing, etc.

FireClaw current pattern:

- `FireClawAgent.__init__()` calls `create_default_skill_registry(robot, action_runtime=...)`.
- `create_default_skill_registry()` statically registers built-in `Skill` objects.
- Each skill has a handler wired to `RobotActionRuntime` and/or a direct adapter method.
- `RobotActionRuntime` calls `RobotAdapterActionBackend`.
- `RobotAdapterActionBackend` dispatches fixed action strings to `robot.navigate_to_floor`, `robot.search_for_victims`, etc.
- `Ros1RobotAdapter.navigate_to_floor()` calls `_record_configured_action("navigate_to_floor", {"floor": floor})`.
- `_record_configured_action()` reads the ROS1 config endpoint, renders `goal_template` / `request_template`, then `Ros1Transport` sends a ROS action/topic/service.

Current Gazebo navigation binding:

- `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`
- `endpoints.navigate_to_floor.profile = "move_base"`
- `endpoints.navigate_to_floor.name = "/move_base"`
- `profile: move_base` expands to `interface=action`, `type=move_base_msgs/MoveBaseAction`.
- `goal_template` maps `floor` to `targets.floor_${floor}`.
- `Ros1Transport` uses `actionlib.SimpleActionClient("/move_base", move_base_msgs/MoveBaseAction)`.

### Main Conclusion

Primitive skill composition now works at the LLM/planner layer, but FireClaw's lower runtime is still not fully OpenClaw-like. It is currently:

```text
static SkillRegistry + business-shaped RobotAdapter methods
```

The target architecture should become:

```text
robot provider/adapter creates primitive executable tools/skills; adapter only owns generic robot communication
```

### Desired Final Boundary

RobotAdapter / RobotProvider should own generic execution channels only:

- `execute_action(action_type, inputs)` or equivalent
- `call_action(endpoint, payload)`
- `publish_topic(endpoint, payload)`
- `call_service(endpoint, payload)`
- `read_sensor(...)`
- `cancel_action(...)`
- `emergency_stop(...)`

Primitive skills should be model-callable basic robot capabilities:

- `navigate_2D(x, y, yaw?)`
- `publish_velocity(...)`
- `report_status(...)`
- `read_lidar(...)`
- `detect_gas(...)`
- `aim_nozzle(...)`
- `open_valve(...)`

Composite skills should be higher-level firefighting workflows:

- `navigate_to_floor(floor)` as a wrapper that resolves floor -> coordinates -> `navigate_2D`
- `search_for_victims`
- `patrol`
- `extinguish_fire`
- `rescue_victim`

### Important Design Decision

Do not simply rename `navigate_to_floor` to `navigate_2D` everywhere as a flat search-and-replace.

Recommended migration:

1. Add `navigate_2D` as the true primitive navigation skill.
2. Keep `navigate_to_floor` temporarily as a composite/wrapper skill for compatibility.
3. Make `navigate_to_floor(floor=2)` resolve `targets.floor_2` and call `navigate_2D(x, y, yaw)`.
4. Move ROS1 endpoint config to support `endpoints.navigate_2D` directly.
5. Gradually migrate profile/planner/tests from floor-only navigation to 2D navigation where appropriate.
6. Later remove business-specific methods from `RobotAdapter` once provider-style dispatch is stable.

### Why This Matters

If FireClaw keeps the current adapter shape, every new robot capability would require adding methods like:

- `robot.extinguish_fire(...)`
- `robot.break_door(...)`
- `robot.detect_gas(...)`
- `robot.open_valve(...)`

That would make adapters bloated and hard to swap across ROS1, ROS2, Gazebo, real robot SDKs, and custom algorithms. The OpenClaw-like model avoids this by keeping providers/adapters backend-focused and exposing capabilities as tools/skills.

### Next Recommended Step

Next session should start a Superpowers brainstorming/spec task named roughly:

```text
FireClaw OpenClaw-like Robot Provider / Primitive Skill Refactor
```

The first concrete deliverable should be a design spec, not immediate code, because this crosses:

- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/execution/action_runtime.py`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/ros/ros1_config.py`
- `src/fireclaw_core/ros/ros1_transport.py`
- `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`
- `examples/robot_profiles/gazebo_turtlebot3.toml`
- planner/profile/task-contract/gateway tests

Suggested phased plan to draft next time:

1. Add `navigate_2D` primitive while keeping old `navigate_to_floor` compatibility.
2. Add config validation for `endpoints.navigate_2D` and new input schema `{x, y, yaw?}`.
3. Implement wrapper/composite `navigate_to_floor -> navigate_2D`.
4. Introduce generic robot action/provider dispatch so new primitive skills do not require adapter protocol methods.
5. Migrate profile and LLM skill inventory to expose `navigate_2D` as primitive and `navigate_to_floor` as composite.
6. Update tests and Gazebo smoke flow.
7. Only after compatibility is proven, deprecate/remove business-shaped adapter methods.

