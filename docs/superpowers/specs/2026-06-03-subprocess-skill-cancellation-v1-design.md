# Subprocess Skill Cancellation v1 Design

## Goal

Extend FireClaw task cancellation from "stop before the next skill" to "request termination of an active subprocess skill" so long-running external policies do not continue after an operator cancels a Gateway task.

## OpenClaw Analogue

OpenClaw keeps active runs in a `runId` keyed abort registry. `abortChatRunById(...)` looks up the active run, calls `AbortController.abort()`, removes active buffers/state, broadcasts an aborted final state, and emits lifecycle telemetry.

FireClaw already has the matching control-plane shape:

```text
task_id -> TaskControl.cancel_event -> FireClawAgent -> PlanExecutor
```

This change pushes that existing cancellation signal one layer deeper:

```text
PlanExecutor cancellation_requested -> subprocess skill runner -> terminate child process -> cancelled result
```

## Architecture

`SubprocessSkillRunner.run(...)` will accept an optional `cancellation_requested` callback. It will switch from `subprocess.run(...)` to `subprocess.Popen(...)` so it can poll the child process while the skill is in progress.

The runner will write JSON input to stdin, poll for completion, and when cancellation is requested it will terminate the child process. If the child does not exit promptly, the runner will kill it. The returned `RobotActionResult` will use `status="cancelled"`, `ok=False`, `mode="subprocess"`, and an explicit cancellation error message.

`Skill.run(...)` will accept the same optional callback and pass it to subprocess skills only. In-process skills keep their existing one-argument handler behavior.

`PlanExecutor` will pass its existing `_cancellation_requested` callback into `skill.run(...)`. If a skill returns `status="cancelled"` or the cancellation callback is set after the attempt, the executor returns `ExecutionResult(status="cancelled")` and does not start later skills.

## Event And Memory Semantics

The existing Gateway final event remains `task.cancelled`. This version does not add a separate `skill.cancelled` event type; the skill attempt event may carry `status="cancelled"` and the final task event remains the operator-facing cancellation record.

Memory behavior remains unchanged: the agent stores the final cancelled result in JSONL memory through the existing append path.

## Error Handling

Subprocess timeout remains a failure when no cancellation was requested. Cancellation wins over timeout when the operator cancellation flag is set while the child is still running.

`OSError`, nonzero exit, invalid JSON, and malformed payload behavior remains unchanged.

## Testing

Add focused tests for:

- direct `SubprocessSkillRunner` cancellation terminates a sleeping child before the normal sleep duration;
- Gateway task cancellation stops an active slow subprocess and prevents later rescue skills from starting;
- existing runtime and Gateway tests remain green.

## Scope Exclusions

This version does not implement ROS1 action cancellation, CUDA process group cleanup, task priority, durable queues, operator authorization, or emergency-stop adapter integration.
