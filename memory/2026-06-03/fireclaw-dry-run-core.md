# FireClaw Dry-Run Core Session

## 2026-06-03 10:12 CST

### Task Goal

Implement Subprocess Skill Cancellation v1 so Gateway task cancellation can stop an active subprocess-backed skill instead of only waiting for the current skill to return naturally.

### OpenClaw Analogue

Checked OpenClaw abort flow in `openclaw-main/src/gateway/chat-abort.ts`.

OpenClaw pattern:

```text
runId -> active AbortController registry -> abort signal -> cleanup -> aborted final event
```

FireClaw adaptation:

```text
task_id -> TaskControl.cancel_event -> PlanExecutor -> SubprocessSkillRunner -> terminate child process -> task.cancelled
```

CodeGraph was checked, but the available index was for the FireClaw Python files only, not the TypeScript OpenClaw reference subtree. The OpenClaw reference was therefore read directly from the local `openclaw-main` source.

### Files Modified

- `src/fireclaw_core/runtime.py`
- `src/fireclaw_core/skills.py`
- `src/fireclaw_core/executor.py`
- `tests/test_runtime.py`
- `tests/test_gateway.py`
- `README.md`
- `docs/superpowers/specs/2026-06-03-subprocess-skill-cancellation-v1-design.md`
- `docs/superpowers/plans/2026-06-03-subprocess-skill-cancellation-v1.md`
- `memory/2026-06-03/fireclaw-dry-run-core.md`

### Commands Executed

- `.venv/bin/python -m pytest tests/test_runtime.py::test_subprocess_skill_runner_terminates_process_when_cancelled -q`
  - RED: failed because `SubprocessSkillRunner.run()` did not accept `cancellation_requested`.
- `.venv/bin/python -m pytest tests/test_runtime.py -q`
  - GREEN: 6 passed.
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_cancels_active_task_between_skills -q`
  - RED: failed because cancellation took about 1.01 seconds, proving the subprocess was allowed to finish naturally.
- `.venv/bin/python -m pytest tests/test_runtime.py tests/test_execution.py tests/test_gateway.py -q`
  - GREEN: 30 passed.
- `.venv/bin/python -m pytest -q`
  - FULL: 150 passed in 4.53s.

### Implementation Details

- `SubprocessSkillRunner.run(...)` now accepts optional `cancellation_requested`.
- Runtime uses `subprocess.Popen(...)` instead of `subprocess.run(...)` so it can poll while the child process is active.
- On cancellation, the runner calls `terminate()`, then `kill()` after a short grace if the child does not exit.
- Cancelled subprocess results return `RobotActionResult(status="cancelled", ok=False, mode="subprocess")`.
- `Skill.run(...)` accepts optional `cancellation_requested` and passes it only to subprocess handlers.
- `PlanExecutor` passes its existing cancellation callback into skill execution.
- Executor treats `result.status == "cancelled"` as cancelled execution and does not emit terminal `skill.failed` or start later skills.
- Gateway cancellation regression now uses a one-second slow subprocess and asserts the cancelled task returns before the subprocess would naturally finish.

### Current Conclusion

Full verification shows subprocess cancellation now propagates from Gateway task cancellation into the active subprocess runner.

### User Clarification

The user clarified that FireClaw should target ROS1, not ROS2. Subprocess cancellation is unaffected, but the next robot-adapter cancellation/feedback phase should be designed around ROS1 actionlib or the user's actual ROS1 control interfaces.

### Remaining Gaps

- No process-group or child-process-tree cleanup yet.
- No ROS1 robot action cancellation mapping yet.
- No CUDA/runtime-specific cleanup protocol yet.
- No operator authorization for cancellation.
- No emergency-stop adapter integration yet.
