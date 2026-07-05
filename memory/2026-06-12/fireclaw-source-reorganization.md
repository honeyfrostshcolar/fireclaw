# FireClaw Source Code Reorganization — 2026-06-12

## Task Goal

Reorganize 80+ flat Python modules in `src/fireclaw_core/` into domain subpackages, mirroring OpenClaw's `src/` directory structure.

## What Was Done

- Created 16 subpackages: agent, gateway, mission, planner, memory, task, approval, plugin, ros, safety, provider, execution, subagent, monitoring, infra, devtools
- Moved all 80+ files via `git mv` into appropriate subpackages
- Updated 313 import lines across 96 test files and all source files
- Merged `lifecycle_maintenance.py` + `lifecycle_reconciler.py` into single `lifecycle.py`
- Added `__init__.py` for each subpackage
- Fixed mock.patch paths in tests (test_provider.py, test_mission_cli.py, etc.)
- Added robot-agent CLI flags to `serve` subcommand in mission_cli.py
- All 1111 tests pass, 6 skipped

## Package Structure

```
src/fireclaw_core/
├── __init__.py, __main__.py, lifecycle.py
├── agent/          # agent, robot_agent, robot, robot_registry, robot_enrollment, agent_cli
├── gateway/        # gateway, serve, control, method_scopes, config.py (NEW)
├── mission/        # mission_agent, mission_planner, mission_registry, mission_cli, interactive, ...
├── planner/        # planner, planner_builder, llm_planner, llm_trace
├── memory/         # memory, memory_index, memory_retrieval, memory_eval, memory_cli
├── task/           # task_contract, task_registry, task_flow_registry, task_queue, task_state
├── approval/       # approval_store, approval_runtime, approval_relay
├── plugin/         # plugin_control_plane, plugin_descriptor, plugin_policy, plugin_runtime
├── ros/            # ros1_*, ros2_adapter, gazebo_smoke
├── safety/         # safety, validation_sidecar, local_failure
├── provider/       # provider, provider_runtime, model_catalog
├── execution/      # executor, action_runtime, runtime, runtime_config, skills
├── subagent/       # subagent_client, subagent_registry
├── monitoring/     # monitor, stream_events, event_ledger, incident_replay
├── infra/          # log_redaction, session_lineage, operator_*, skill_manifest, workspace_skills
└── devtools/       # demo, doctor, fleet_doctor, embodied_eval, embodied_proof_bundle, tool_schema
```

## Key Lessons

- Python 3.8 system `python` command doesn't support `int | float` in isinstance() — always use project venv
- Mock.patch paths in tests must match actual module locations — bulk sed replacement needed for both `from X import` and `@patch("X...")` patterns
- `lifecycle_maintenance.py` + `lifecycle_reconciler.py` concatenation has `from __future__` conflict — must remove duplicate imports when merging
- CLI entry points (`-m fireclaw_core.xxx`) break when modules become subpackages — update `__main__.py` routing

## Files Changed

- 203 files changed, 8058 insertions, 659 deletions
- Commit: `e904b61`

## Why

As the codebase grew to 80+ files, flat structure made navigation and maintenance difficult. Domain subpackages improve discoverability and match OpenClaw's architecture.

## How to Apply

When adding new modules, place them in the appropriate subpackage. When importing, use full paths like `from fireclaw_core.agent.robot_agent import RobotAgentRuntime`.
