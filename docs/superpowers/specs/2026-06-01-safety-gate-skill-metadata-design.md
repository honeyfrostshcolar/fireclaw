# Safety Gate Skill Metadata Design

## Goal

Make FireClaw's safety gate enforce the skill metadata introduced in workspace manifests.

## Scope

This phase keeps FireClaw in dry-run-first mode. It does not enable real robot execution globally. It adds structural checks so future real-robot mode has explicit gates instead of implicit trust.

## Rules

The safety gate checks every planned skill after missing-skill validation:

- Required sensors: block if a skill declares `required_sensors` that are not available in the current evaluation context.
- Retry safety: block if `max_attempts > 1` and `idempotent` is not true.
- Dry-run mode: block skills where `dry_run_only=false` while the agent is in dry-run mode.
- Non-dry-run mode: block unless every skill has `allow_real_robot=true` and `dry_run_only=false`.

`available_sensors` is passed into `SafetyGate.evaluate()`. The default is an empty set. `FireClawAgent` accepts `available_sensors` and forwards it to the safety gate.

## Data Flow

`FireClawAgent.run()` passes `dry_run` and `available_sensors` into `SafetyGate.evaluate()`. The safety gate only inspects planner output and `SkillRegistry`; it does not execute skills or mutate state.

## Testing

Add tests for:

- missing required sensors are blocked;
- available sensors allow the plan;
- retryable non-idempotent skills are blocked;
- dry-run blocks skills that are not dry-run-only;
- non-dry-run blocks skills without real-robot allowance;
- agent-level sensor propagation blocks execution before robot actions run.

## Research Impact

This turns skill metadata into a real safety boundary. It also creates explicit variables for future experiments: sensor availability, idempotency, and real-robot eligibility can become ablation dimensions for embodied-agent safety evaluation.
