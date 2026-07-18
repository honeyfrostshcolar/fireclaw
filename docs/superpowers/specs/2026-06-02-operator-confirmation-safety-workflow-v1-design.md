# Operator Confirmation Safety Workflow v1 Design

## Goal

Add a deterministic operator confirmation workflow for safety-critical FireClaw actions.

FireClaw should be able to plan a task, decide that it is otherwise valid but requires explicit operator confirmation, persist the pending plan, and execute or cancel it in a later turn in the same session.

## Scope

- Add skill risk metadata.
- Add a `require_confirmation` safety decision.
- Keep hard safety violations as `block`.
- Add pending confirmation records to JSONL memory.
- Support `确认执行` and `取消` commands.
- Keep the workflow CLI-compatible across separate processes.

## Non-Goals

- No real ROS2 adapter.
- No authentication or human identity verification.
- No free-form LLM explanation generation.
- No modification of historical JSONL records.

## Design

### Skill Risk Metadata

Each skill gets:

```python
risk_level: str = "low"
```

Allowed values:

- `low`
- `medium`
- `high`
- `critical`

Skill manifests may declare `risk_level`.

### Safety Decision

`SafetyGate.evaluate(...)` keeps existing behavior for clarify and block cases.

If a plan passes all hard checks but has confirmation requirements, it returns:

```python
SafetyDecision(status="require_confirmation", reasons=[...])
```

Confirmation is required when:

- `dry_run=False` and a skill is otherwise allowed for real robot execution;
- any plan step uses a skill with `risk_level` of `high` or `critical`.

When `operator_confirmed=True`, those confirmation reasons no longer stop execution. Hard blocks still block.

### Agent Workflow

Normal command:

1. Plan.
2. Safety evaluate.
3. If `require_confirmation`, do not execute.
4. Append a memory record with:
   - `status="awaiting_confirmation"`
   - `safety.status="require_confirmation"`
   - `confirmation.status="pending"`

Confirm command:

1. Find latest unresolved pending confirmation in the current session.
2. Reconstruct the planned task from memory.
3. Re-evaluate safety with `operator_confirmed=True`.
4. Execute only if safety allows.
5. Append a new memory record with:
   - `confirmation.status="confirmed"`
   - `confirmation.pending_turn_index=<pending turn>`

Cancel command:

1. Find latest unresolved pending confirmation.
2. Append a new memory record with:
   - `status="cancelled"`
   - `confirmation.status="cancelled"`
   - no execution.

## Risks

- Memory is append-only, so unresolved pending detection must ignore already confirmed/cancelled pending turns.
- Pending plans are trusted from local memory. Later versions should include plan hashes and operator identity.
- Current confirmation commands are simple Chinese templates.

## Verification

- Safety tests for real-run and risk-level confirmation.
- Agent tests for pending, confirm, cancel, and no-pending behavior.
- CLI test for cross-process pending confirmation.
- Full pytest run.
