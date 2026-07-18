# FireClaw Robot Capability Profile Implementation

## Date: 2026-06-12

## Summary

Implemented robot capability profile layer for FireClaw, following OpenClaw's pattern of separating model config from tool/capability binding.

## What Was Built (10 tasks)

### Task 1: Robot Capability Profile Contract
- `src/fireclaw_core/agent/robot_profile.py` — `RobotCapabilityProfile` frozen dataclass, `load_robot_capability_profile()` TOML loader
- Properties: `memory_path`, `event_path`, `task_queue_path`
- Method: `to_robot_registry_entry()`

### Task 2: Profile Validation
- `validate_robot_capability_profile()` — validates skills against SkillRegistry and ROS1 remaps
- Checks: enabled_skills registered, llm_exposed_skills subset of enabled, ROS1 remaps present, capabilities non-empty

### Task 3: LLM Skill Tools
- `src/fireclaw_core/agent/robot_tools.py` — `build_robot_skill_tools()`, `local_plan_from_direct_tool_calls()`
- Converts SkillRegistry metadata to OpenAI-compatible tool schemas

### Task 4: Direct Skill Tool Calls
- `LLMRobotAgentPlanner.plan()` now accepts both `create_robot_local_plan` wrapper and direct skill tool calls
- Lazy import to avoid circular dependency

### Task 5: Gateway Integration
- `GatewayConfig.robot_profile_path` field
- `--robot-profile` CLI argument
- Profile loaded in `FireClawGateway.__init__`
- Skill tools constrained by profile's `llm_exposed_skills`

### Task 6: Profile-Backed Skill Resolution
- `skills_from_capability()` public resolver with optional `capability_skill_chains`
- `structured_task_from_mission_subtask()` accepts `capability_skill_chains`

### Task 7: Robot Profile Export CLI
- `robot-profile export` command writes robots.json from profile TOML

### Task 8: Example Gazebo Profile
- `examples/robot_profiles/gazebo_turtlebot3.toml`
- Updated `fireclaw.example.toml` with `profile_path`

### Task 9: Documentation
- Updated README.md and ros1-gazebo-debugging-guide.md
- Fixed typo: `turtlebo3_navigation` → `turtlebot3_navigation`

### Task 10: Verification
- 71 focused tests passed
- 16 integration tests passed
- All CLI probes exit 0
- Full suite: 1133 passed, 6 skipped

## Key Architecture

```
RobotCapabilityProfile (TOML)
    ↓
SkillRegistry + ROS1 config
    ↓
validate_robot_capability_profile()
    ↓
build_robot_skill_tools() → LLM tools
    ↓
LLMRobotAgentPlanner.plan() → RobotLocalPlan
    ↓
SafetyGate + PlanExecutor → Robot Adapter
```

## Files Created/Modified

Created:
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/agent/robot_tools.py`
- `examples/robot_profiles/gazebo_turtlebot3.toml`
- `tests/test_robot_profile.py`
- `tests/test_robot_tools.py`

Modified:
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/task/task_contract.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/__main__.py`
- `fireclaw.example.toml`
- `README.md`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

## Commits

- `eabca8e` feat: add robot capability profile contract
- `4b7c796` feat: validate robot capability profiles
- `71e7a21` fix: address code review issues for profile validation
- `d32c006` feat: expose robot skills as LLM tools
- `e40ca42` test: add edge case tests for robot tools
- `9ccc786` feat: allow robot-local LLM direct skill tools
- `6daa203` feat: pass robot skill tools to local agent
- `9695ad6` fix: load robot profile and constrain exposed skills
- `7f30341` feat: support profile-backed capability skill chains
- `f7b9010` feat: export robot registry from capability profile
- `d94a890` docs: add gazebo turtlebot3 robot profile
- `c796a34` docs: document profile-backed robot gateway workflow
