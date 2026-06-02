# Execution Monitor and Failure Policy Design

## Goal

Add the first execution-monitor layer for FireClaw so skill execution records attempts, retries recoverable failures when configured, and stops with an explicit operator-escalation state when a step cannot be completed.

## Scope

This phase stays in the pure Python dry-run core. It does not add ROS2, real hardware execution, async execution, or LLM-based recovery. The goal is to make the current sequential executor safer and more auditable.

## Design

`Skill` gains a small retry policy through `max_attempts`, defaulting to `1` so existing behavior is unchanged. Built-in robot skills remain one-attempt skills. Research or workspace skills can later opt into retries when their wrapper is known to be idempotent and safe.

`FailurePolicy` decides what to do after a failed attempt:

- `retry` when the skill has remaining attempts;
- `stop_and_escalate` when no retry remains.

`PlanExecutor` records every attempt for each step. A successful retry produces a succeeded step with `attempt_count > 1` and a full attempt history. An exhausted failure produces `status="failed"`, `failure_category="recoverable_exhausted"`, and `operator_action="escalate"`.

## Safety Rationale

Retries are not global. They are opt-in per skill. This avoids accidentally retrying unsafe robot actions. For firefighting robots, retry policy must later be tied to skill metadata such as idempotency, emergency-stop semantics, and robot state validation.

## Testing

Add focused tests for:

- a flaky skill that fails once and succeeds on retry;
- a permanently failing retryable skill that stops after the configured attempts and asks for escalation;
- existing rescue execution remains one-attempt and backward-compatible.

## Remaining Future Work

- Per-failure categories such as timeout, perception uncertainty, blocked path, communication loss, and hardware fault.
- Backoff and timeout budgets.
- Operator confirmation workflows.
- Real robot acknowledgement tracking.
