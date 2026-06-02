# FireClaw Dry-Run Core Session

## 2026-06-01 14:50 CST

### Task Goal

Start the FireClaw project by designing the first runnable Python dry-run core inspired by OpenClaw. The target system is a firefighting robot embodied agent that can receive natural-language commands such as `去二楼救人`, decompose them, run skills, and remember task history.

### User Decisions

- Primary language for first version: Python.
- First version should be pure dry-run core, not ROS or real hardware.
- Planner type: deterministic rule/template planner.
- Initial skill set: minimal rescue flow.

### Files Inspected

- `AGENTS.md`
- `AGENTS.zh-CN.md`
- OpenClaw structure through CodeGraph, especially `openclaw-main/src/agents/skills/*`
- OpenClaw memory structure through CodeGraph, especially `openclaw-main/extensions/memory-core/src/memory/*`

### Current Design

The first FireClaw core should create a Python package named `fireclaw_core` with:

- `agent`
- `planner`
- `skills`
- `robot`
- `safety`
- `executor`
- `memory`

The first demo command is `去二楼救人`, which should produce:

1. `navigate_to_floor`
2. `search_for_victims`
3. `assess_victim`
4. `report_status`
5. `return_to_safe_zone`

### Files Modified

- Added `AGENTS.zh-CN.md`
- Added `docs/superpowers/specs/2026-06-01-fireclaw-dry-run-core-design.md`
- Added this memory record

### Next Recommended Step

Ask the user to review the written design spec. After approval, create an implementation plan and then scaffold the Python package and tests.

### Remaining Uncertainty

The first version intentionally does not decide the future ROS2 adapter shape in detail. That should be designed after the dry-run core interfaces are validated.

## 2026-06-01 15:14 CST

### Implemented Files

- `pyproject.toml`
- `.gitignore`
- `src/fireclaw_core/__init__.py`
- `src/fireclaw_core/__main__.py`
- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/executor.py`
- `src/fireclaw_core/memory.py`
- `src/fireclaw_core/planner.py`
- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/skills.py`
- `tests/test_agent.py`
- `tests/test_execution.py`
- `tests/test_memory.py`
- `tests/test_planner.py`
- `tests/test_safety.py`

### Environment

- Created `.venv/` with `uv venv --python /home/nankai/.local/bin/python3.11 .venv`.
- Virtual environment Python: 3.11.15.
- Installed dev dependency with `uv pip install --python .venv/bin/python -e '.[dev]'`.
- System `python3` is 3.8.10 and should not be used for this project.

### Verification

- `.venv/bin/python -m pytest tests/test_planner.py -v`: 3 passed.
- `.venv/bin/python -m pytest tests/test_execution.py -v`: 2 passed.
- `.venv/bin/python -m pytest tests/test_safety.py -v`: 4 passed.
- `.venv/bin/python -m pytest tests/test_memory.py -v`: 2 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 3 passed.
- `.venv/bin/python -m pytest -v`: 14 passed.
- `.venv/bin/python -m fireclaw_core '去二楼救人' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned status `succeeded`.

### Current Conclusion

The first Python dry-run FireClaw core is implemented. It can parse `去二楼救人`, generate the five-step rescue plan, pass the safety gate, execute built-in dry-run skills, and append structured JSONL memory.

### Remaining Gaps

- No ROS2 adapter yet.
- No LLM planner yet.
- No external CUDA/RL skill runtime yet.
- No vector or semantic memory yet.
- Current planner only supports a small rescue-command template.

## 2026-06-01 15:26 CST

### Continued Work

After the user asked to continue, added small but important project hardening:

- Added `README.md` with quickstart, test command, dry-run demo command, and skill runtime direction.
- Added `.codex/` to `.gitignore` because `.codex/config.toml` is local tool configuration.
- Added `tests/test_cli.py` to verify `python -m fireclaw_core` runs in a subprocess and writes memory.
- Added runtime metadata to `Skill`, defaulting to `runtime="in_process"`.
- Added a test that all default dry-run skills declare `runtime="in_process"` and `dry_run_only=True`.

### Why Runtime Metadata Matters

The user clarified that future research algorithms may require CUDA, reinforcement learning, or deep-learning stacks. The current design keeps FireClaw core lightweight and records skill runtime as metadata so later skills can use isolated runtimes such as:

- `in_process`
- `subprocess`
- `external_conda`
- `ros2`
- `http`

### Verification

- `.venv/bin/python -m pytest -v`: 16 passed.
- `.venv/bin/python -m fireclaw_core '去二楼救人' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned status `succeeded`.

## 2026-06-01 15:35 CST

### Continued Work

Added the first external skill runtime adapter for future CUDA/RL algorithms:

- `src/fireclaw_core/runtime.py`
- `SubprocessSkillRunner`
- `create_subprocess_skill(...)`
- `tests/test_runtime.py`

The subprocess runner sends JSON inputs to a command through stdin and expects JSON on stdout in the shape `{ "ok": true, "data": {...} }`.

### Why This Matters

The user clarified that future research algorithms may require CUDA, reinforcement learning, or deep-learning stacks. FireClaw core should stay lightweight, while heavy algorithms can run in separate Python, conda, ROS2, or service runtimes.

## 2026-06-01 15:43 CST

### Continued Work

Added a JSON manifest loader for subprocess skills:

- `src/fireclaw_core/skill_manifest.py`
- `tests/test_skill_manifest.py`

The loader supports manifest files with:

- `name`
- `description`
- `runtime`
- `command`
- `timeout_seconds`
- `dry_run_only`

It currently only accepts `runtime="subprocess"` and explicitly rejects shell string commands. `command` must be a list of strings. This is intentional because future CUDA/RL skills should be launched explicitly, for example with a specific conda Python path, rather than through shell parsing.

### Verification

- `.venv/bin/python -m pytest tests/test_runtime.py -v`: 5 passed.
- `.venv/bin/python -m pytest tests/test_skill_manifest.py -v`: 3 passed.
- `.venv/bin/python -m pytest -v`: 24 passed.
- `.venv/bin/python -m fireclaw_core '去二楼救人' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned status `succeeded`.

## 2026-06-01 15:52 CST

### Continued Work

Added the first recall path for the memory system:

- `JsonlMemoryStore.latest_records(limit=5)`
- Natural-language recall detection in `FireClawAgent.run(...)`
- Recall command examples such as `之前做过什么` and `回忆之前任务`
- Structured memory-read failure handling

Recall commands return `status="recalled"`, include recent records under `memory.records`, do not execute skills, and do not append another memory entry. This keeps task history from being polluted by recall queries.

### Tests Added

- `test_jsonl_memory_store_returns_latest_records`
- `test_agent_recalls_recent_tasks_without_executing_or_rewriting_memory`
- `test_agent_reports_empty_memory_for_recall_command`
- `test_agent_reports_memory_read_errors_for_recall_command`

### Verification

- `.venv/bin/python -m pytest -v`: 29 passed.
- `.venv/bin/python -m fireclaw_core '去二楼救人' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned status `succeeded`.
- `.venv/bin/python -m fireclaw_core '之前做过什么' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned status `recalled` with exit code 0 after fixing CLI success statuses.

## 2026-06-01 16:00 CST

### Continued Work

Added skill visibility/status support:

- `SkillRegistry.list_metadata()`
- Natural-language skill listing detection in `FireClawAgent`
- CLI success handling for `status="skills"`
- README example for `你有哪些技能`

Skill listing commands return metadata for registered skills, including `name`, `description`, `runtime`, and `dry_run_only`. They do not execute skills and do not append memory records.

### Tests Added

- `test_agent_lists_available_skills_without_executing_or_writing_memory`
- `test_module_cli_treats_skill_listing_as_successful_command`

### Verification

- `.venv/bin/python -m pytest -v`: 31 passed.
- `.venv/bin/python -m fireclaw_core '你有哪些技能' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned status `skills` with five registered dry-run skills.

## 2026-06-01 16:18 CST

### Continued Work

Implemented workspace skill loading plan:

- `src/fireclaw_core/workspace_skills.py`
- `tests/test_workspace_skills.py`
- `skills/examples/echo_policy.skill.json`
- `skills/examples/echo_policy.py`
- `tests/test_workspace_skill_example.py`

Also added registry merge support:

- `SkillRegistry.register(...)`
- `SkillRegistry.extend(...)`

Integrated workspace skills into:

- `FireClawAgent(..., workspace_skills_dir=...)`
- CLI `--skills-dir`
- CLI `--no-workspace-skills`
- skill listing output field `skill_load_errors`

### Important Path Note

Subprocess skills execute with the manifest directory as `cwd`. The example manifest uses `../../.venv/bin/python` because it lives under `skills/examples/`.

### Verification

- `.venv/bin/python -m pytest tests/test_workspace_skills.py -v`: 3 passed.
- `.venv/bin/python -m pytest tests/test_execution.py -v`: 7 passed.
- `.venv/bin/python -m pytest tests/test_agent.py tests/test_cli.py -v`: 14 passed.
- `.venv/bin/python -m pytest tests/test_workspace_skill_example.py -v`: 1 passed.
- `.venv/bin/python -m pytest -v`: 43 passed.
- `.venv/bin/python -m fireclaw_core '你有哪些技能' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="skills"` and included `echo_policy`.
- `.venv/bin/python -m fireclaw_core '去二楼救人' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"`.

### Current Conclusion

The workspace skill loading plan is complete. FireClaw can now discover `skills/**/*.skill.json`, load subprocess skills, surface load errors non-fatally, list workspace skills alongside built-ins, and keep built-in rescue execution working.

## 2026-06-01 16:32 CST

### Continued Work

Implemented direct skill invocation:

- `RuleBasedPlanner` now parses `运行 <skill>`, `调用 <skill>`, and `执行 <skill>`.
- Optional payload after `处理`, `输入`, or `参数` is passed as `{"text": ...}`.
- Direct skill invocation generates `intent="direct_skill_invocation"` with a one-step plan.
- Safety gate no longer requires `target_floor` for non-rescue plans.
- Missing direct skills are blocked by the existing missing-skill check before execution.
- CLI direct invocation works for workspace skills.

### Tests Added

- Direct skill planner tests in `tests/test_planner.py`.
- Agent direct invocation and missing-skill tests in `tests/test_agent.py`.
- Non-dry-run direct invocation safety test in `tests/test_safety.py`.
- CLI direct invocation and missing-skill tests in `tests/test_cli.py`.

### Verification

- `.venv/bin/python -m pytest tests/test_planner.py -v`: 7 passed.
- `.venv/bin/python -m pytest tests/test_agent.py tests/test_safety.py -v`: 16 passed.
- `.venv/bin/python -m pytest tests/test_cli.py -v`: 7 passed.
- `.venv/bin/python -m pytest -v`: 52 passed.
- `.venv/bin/python -m fireclaw_core '运行 echo_policy 处理 二楼' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"`.
- `.venv/bin/python -m fireclaw_core '运行 missing_skill' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="block"` with nonzero exit as expected.

### Current Conclusion

The direct skill invocation plan is complete. FireClaw can now route natural-language direct skill calls through planner, safety, executor, and memory. This makes workspace skills usable through the agent loop rather than only visible through skill listing.

## 2026-06-01 18:55 CST

### Continued Work

Implemented rescue plans with optional policy skills:

- `RuleBasedPlanner` now parses policy references in rescue commands:
  - `去二楼救人 使用 echo_policy`
  - `去二楼救人 导航策略用 echo_policy`
  - `去2楼救人 用 echo_policy`
- The policy skill is inserted as the first step before navigation.
- Policy skill inputs include `floor` and original `command`.
- Missing policy skills are blocked by the existing safety gate before execution.
- CLI mixed rescue plans work with workspace skills.

### Tests Added

- Planner tests for policy skill insertion.
- Agent tests for mixed rescue plan success and missing policy block.
- CLI tests for mixed rescue plan success and missing policy nonzero exit.

### Verification

- `.venv/bin/python -m pytest tests/test_planner.py -v`: 10 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 13 passed.
- `.venv/bin/python -m pytest tests/test_cli.py -v`: 9 passed.
- `.venv/bin/python -m pytest -v`: 59 passed.
- `.venv/bin/python -m fireclaw_core '去二楼救人 使用 echo_policy' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` with six execution steps.
- `.venv/bin/python -m fireclaw_core '去二楼救人 使用 missing_policy' --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="block"` with nonzero exit as expected.

### Current Conclusion

The rescue plan policy skill plan is complete. FireClaw can now take a natural-language rescue task and insert a named workspace algorithm skill into the generated multi-step plan before navigation.

## 2026-06-01 19:08 CST

### Continued Work

Implemented the robot adapter boundary:

- Added `RobotAdapter` protocol.
- Expanded `RobotActionResult` with structured fields:
  - `status`
  - `robot_id`
  - `mode`
  - `action`
  - `dry_run`
  - `timestamp`
- Updated `DryRunRobotAdapter` to populate structured results.
- Added `MockRos2RobotAdapter` as a ROS2-shaped test double without ROS2 imports.
- Updated built-in skill registry typing to depend on `RobotAdapter`.
- Updated executor outputs to include structured robot result fields.
- Updated `FireClawAgent` type hints to accept `RobotAdapter`.

### Tests Added

- `tests/test_robot.py`
- Fake adapter protocol compatibility test in `tests/test_execution.py`
- Mock ROS2 agent integration test in `tests/test_agent.py`

### Verification

- `.venv/bin/python -m pytest tests/test_robot.py -v`: 4 passed.
- `.venv/bin/python -m pytest tests/test_execution.py -v`: 8 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 14 passed.
- `.venv/bin/python -m pytest -v`: 65 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` and each execution output included structured robot fields such as `robot_id`, `mode`, `action`, `status`, `dry_run`, and `timestamp`.

### Current Conclusion

The robot adapter boundary plan is complete. FireClaw now has an explicit protocol boundary for robot implementations, keeps the existing dry-run adapter as the default, and includes a ROS2-shaped mock adapter for future integration tests without adding ROS2 as a core dependency.

### Remaining Gaps

- No real ROS2 adapter yet.
- No simulator adapter yet.
- No real hardware execution mode; current safety policy still blocks non-dry-run execution.
- Future real adapters will need emergency-stop handling, timeout semantics, robot state validation, and auditable command acknowledgements.

## 2026-06-01 19:02 CST

### Continued Work

Implemented the first execution monitor and failure policy layer:

- Added execution attempt history for every skill step.
- Added `Skill.max_attempts`, defaulting to `1` so existing robot actions are not retried automatically.
- Added opt-in retry behavior for retryable skills.
- Added `FailurePolicy` in `src/fireclaw_core/monitor.py`.
- Failed retry exhaustion now stops the plan and marks the failed step with:
  - `failure_category="recoverable_exhausted"`
  - `operator_action="escalate"`
- Agent and CLI output now serialize:
  - `attempt_count`
  - `attempts`
  - `failure_category`
  - `operator_action`

### Files Modified

- `src/fireclaw_core/executor.py`
- `src/fireclaw_core/monitor.py`
- `src/fireclaw_core/skills.py`
- `tests/test_execution.py`
- `tests/test_agent.py`
- `README.md`
- `docs/superpowers/specs/2026-06-01-execution-monitor-failure-policy-design.md`
- `docs/superpowers/plans/2026-06-01-execution-monitor-failure-policy.md`

### Verification

- `.venv/bin/python -m pytest tests/test_execution.py -v`: 11 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 15 passed.
- `.venv/bin/python -m pytest -v`: 69 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` and each execution step included attempt history.

### Current Conclusion

The execution monitor and failure policy plan is complete. FireClaw now has auditable per-step attempts and a conservative retry mechanism that must be explicitly enabled per skill.

### Remaining Gaps

- No timeout budget per plan or per mission yet.
- No backoff strategy yet.
- Failure categories are still minimal.
- Operator escalation is represented structurally but has no interactive confirmation workflow yet.
- Real robot retries still require stronger preconditions, idempotency metadata, and emergency-stop integration before non-dry-run execution should be allowed.

## 2026-06-01 19:06 CST

### Continued Work

Implemented skill manifest execution and safety metadata:

- Added metadata fields to `Skill`:
  - `max_attempts`
  - `idempotent`
  - `required_sensors`
  - `failure_categories`
  - `allow_real_robot`
  - `timeout_seconds`
- Updated `SkillRegistry.list_metadata()` so skill listing exposes those fields.
- Extended subprocess skill manifests with optional metadata fields.
- Added manifest validation:
  - `max_attempts` must be a positive integer.
  - `max_attempts > 1` requires `idempotent=true`.
  - `required_sensors` and `failure_categories` must be lists of non-empty strings.
  - `allow_real_robot=true` cannot be combined with `dry_run_only=true`.
- Updated `skills/examples/echo_policy.skill.json` with conservative metadata.
- Updated README manifest examples and safety rules.

### Files Modified

- `src/fireclaw_core/skills.py`
- `src/fireclaw_core/skill_manifest.py`
- `tests/test_execution.py`
- `tests/test_skill_manifest.py`
- `tests/test_agent.py`
- `skills/examples/echo_policy.skill.json`
- `README.md`
- `docs/superpowers/specs/2026-06-01-skill-manifest-metadata-design.md`
- `docs/superpowers/plans/2026-06-01-skill-manifest-metadata.md`

### Verification

- `.venv/bin/python -m pytest tests/test_execution.py -v`: 12 passed.
- `.venv/bin/python -m pytest tests/test_skill_manifest.py -v`: 7 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 16 passed.
- `.venv/bin/python -m pytest -v`: 75 passed.
- `.venv/bin/python -m fireclaw_core "你有哪些技能" --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="skills"` with six skills and metadata fields including `max_attempts`, `idempotent`, `required_sensors`, `failure_categories`, `allow_real_robot`, and `timeout_seconds`.

### Current Conclusion

The skill manifest metadata plan is complete. Workspace skill manifests now act as explicit skill contracts rather than only subprocess launch configs.

### Remaining Gaps

- Safety gate does not yet enforce required sensors or real-robot eligibility beyond current dry-run blocking.
- Planner does not yet select skills by metadata.
- Failure categories are declared but not yet used for branch planning or recovery strategy.
- No schema file exists yet for editor validation of `*.skill.json`.

## 2026-06-01 19:11 CST

### Continued Work

Implemented safety gate enforcement for skill metadata:

- `SafetyGate.evaluate()` now accepts `available_sensors`.
- Blocks skills when declared `required_sensors` are unavailable.
- Blocks skills with `max_attempts > 1` unless `idempotent=true`.
- In dry-run mode, blocks skills marked `dry_run_only=false`.
- In non-dry-run mode, blocks every skill unless `allow_real_robot=true` and `dry_run_only=false`.
- `FireClawAgent` now accepts `available_sensors` and forwards them to the safety gate.

### Files Modified

- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/agent.py`
- `tests/test_safety.py`
- `tests/test_agent.py`
- `README.md`
- `docs/superpowers/specs/2026-06-01-safety-gate-skill-metadata-design.md`
- `docs/superpowers/plans/2026-06-01-safety-gate-skill-metadata.md`

### Verification

- `.venv/bin/python -m pytest tests/test_safety.py -v`: 11 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 17 passed.
- `.venv/bin/python -m pytest -v`: 82 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"`.

### Current Conclusion

The safety gate skill metadata plan is complete. Skill metadata now affects pre-execution safety decisions instead of only appearing in skill listings.

### Remaining Gaps

- CLI does not yet accept `--available-sensor` arguments.
- Planner does not yet select or reject skills based on metadata before safety evaluation.
- There is still no operator confirmation workflow for real robot execution.
- Real robot mode remains structurally possible only for explicitly allowed direct skills, but no real adapter is implemented yet.

## 2026-06-01 19:16 CST

### Continued Work

Implemented CLI runtime context:

- Added `--robot-id` to configure the dry-run robot adapter id.
- Added repeatable `--available-sensor` and passed the values into `FireClawAgent(available_sensors=...)`.
- Added `--real-run` to evaluate safety with `dry_run=False`.
- Confirmed `--real-run` does not create a real robot adapter; it only exercises safety-gate real-robot eligibility checks.
- Updated README with CLI runtime context examples.

### Files Modified

- `src/fireclaw_core/__main__.py`
- `tests/test_cli.py`
- `README.md`
- `docs/superpowers/specs/2026-06-01-cli-runtime-context-design.md`
- `docs/superpowers/plans/2026-06-01-cli-runtime-context.md`

### Verification

- `.venv/bin/python -m pytest tests/test_cli.py -v`: 13 passed.
- `.venv/bin/python -m pytest -v`: 86 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --robot-id robot-cli --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` and execution output used `robot_id="robot-cli"`.

### Current Conclusion

The CLI runtime context plan is complete. FireClaw command-line runs can now configure robot identity, available sensors, and dry-run/real-run safety evaluation mode.

### Remaining Gaps

- No config file loader yet.
- No environment-variable based defaults yet.
- No CLI option for selecting adapter type.
- `--real-run` is only a safety evaluation mode; it must not be treated as real hardware support.

## 2026-06-01 19:30 CST

### Continued Work

Implemented the first session and conversation state layer:

- Added `session_id` to `FireClawAgent`.
- Added session metadata to task results:
  - `session_id`
  - `turn_index`
  - `resolved_command`
  - `context_used`
- Added session-aware `JsonlMemoryStore.latest_records(..., session_id=...)`.
- Recall commands now prefer records from the current session.
- Preserved backward compatibility for old memory records without a `session` field.
- Added a minimal deterministic clarification flow:
  - first turn: `救人` returns `clarify`;
  - second turn in same session: `二楼` resolves to `去二楼救人` and succeeds.
- Added CLI `--session-id`.
- Updated README with session state usage.

### Files Modified

- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/memory.py`
- `src/fireclaw_core/__main__.py`
- `tests/test_agent.py`
- `tests/test_memory.py`
- `tests/test_cli.py`
- `README.md`
- `docs/superpowers/specs/2026-06-01-session-conversation-state-design.md`
- `docs/superpowers/plans/2026-06-01-session-conversation-state.md`

### Verification

- `.venv/bin/python -m pytest tests/test_agent.py tests/test_memory.py -v`: 22 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 20 passed.
- `.venv/bin/python -m pytest tests/test_cli.py -v`: 14 passed.
- `.venv/bin/python -m pytest -v`: 91 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --session-id demo-session --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` and included `session.session_id="demo-session"`.

### Current Conclusion

The session conversation state plan is complete. FireClaw now has a durable local session boundary, session-scoped recall, and a minimal multi-turn clarification mechanism.

### Remaining Gaps

- Session state is still derived from JSONL memory rather than a dedicated session store.
- Clarification resolution is intentionally narrow and deterministic.
- No full dialogue state machine yet.
- No LLM planner has access to session history yet.
- No operator correction/preference memory model yet.

## 2026-06-01 19:19 CST

### Project Progress Summary

The user asked for a summary of completed FireClaw work and how much of OpenClaw's source-level capability has been approximated.

### Completed FireClaw Capabilities

Completed nine implementation phases:

1. Python dry-run core.
2. Workspace skill loading.
3. Direct skill invocation.
4. Rescue plan policy skill insertion.
5. Robot adapter boundary.
6. Execution monitor and failure policy.
7. Skill manifest execution and safety metadata.
8. Safety gate enforcement of skill metadata.
9. CLI runtime context.

Current source package:

- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/planner.py`
- `src/fireclaw_core/skills.py`
- `src/fireclaw_core/runtime.py`
- `src/fireclaw_core/skill_manifest.py`
- `src/fireclaw_core/workspace_skills.py`
- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/executor.py`
- `src/fireclaw_core/monitor.py`
- `src/fireclaw_core/memory.py`
- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/__main__.py`

Current verified test suite:

- `.venv/bin/python -m pytest -v`: 86 passed.

Current runnable examples:

- `.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl`
- `.venv/bin/python -m fireclaw_core "你有哪些技能" --memory-path /tmp/fireclaw-demo-memory.jsonl`
- `.venv/bin/python -m fireclaw_core "运行 echo_policy 处理 二楼" --memory-path /tmp/fireclaw-demo-memory.jsonl`
- `.venv/bin/python -m fireclaw_core "去二楼救人 使用 echo_policy" --memory-path /tmp/fireclaw-demo-memory.jsonl`

### OpenClaw Alignment Assessment

Current FireClaw approximates OpenClaw's architecture at the skeleton level but is not a full OpenClaw feature migration.

Estimated progress:

- Architecture skeleton level: roughly 45%-55%.
- Runnable product capability level: roughly 25%-35%.
- Full OpenClaw-like capability: below half.

Implemented OpenClaw-like concepts:

- Agent main loop.
- Planner abstraction, currently deterministic rule/template planner.
- Skill/tool abstraction.
- Workspace skill loading through manifests.
- Subprocess external skill runtime.
- Basic persistent memory through JSONL.
- Safety gate.
- Execution logs and attempt monitoring.
- CLI entry point.
- Local-first state.

Not yet implemented or shallow:

- Full session/thread/conversation state.
- LLM planner and tool-calling planner.
- Multi-turn clarification with persistent dialogue context.
- Gateway/control plane.
- Full plugin/extension system.
- Advanced memory retrieval such as vector memory or semantic recall.
- Multi-agent or multi-robot coordination.
- Real ROS2 adapter.
- Simulator adapter.
- UI/dashboard.
- Full operator confirmation, authorization, and audit workflow.

### Recommended Next Direction

The next most important OpenClaw-alignment step is `Session / Conversation State`.

Reasoning:

- Multi-turn natural-language control requires session state.
- Clarification questions need a place to store pending intent.
- Operator corrections and preferences should attach to sessions.
- Memory retrieval needs session context to avoid returning unrelated past records.
- Later LLM planner and tool-calling planner will need session history.

After session state, suggested sequence:

1. LLM planner / tool-calling planner.
2. Stronger memory retrieval.
3. Operator confirmation / safety workflow.
4. ROS2 or simulator adapter.
