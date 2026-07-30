# P1 Unified Capability Policy Pipeline

## Task Goal

Implement one auditable policy path that connects actor identity, task
delegation, plugin exposure, robot capabilities, current state, physical
safety, and exact execution authorization. Apply it both when projecting tools
to the Robot Agent LLM and immediately before a physical side effect.

## Timestamp

- 2026-07-29: implementation, bypass inspection, focused verification, and
  full-suite verification completed.

## OpenClaw Analogues Inspected

- `openclaw/src/agents/tool-policy-pipeline.ts`
  - reused ordered named policy stages;
  - reused deterministic before/after tool filtering;
  - reused per-stage diagnostics.
- `openclaw/src/agents/conversation-tool-policy-pipeline.ts`
  - reused one canonical order for tool visibility decisions.

FireClaw-specific adaptation: physical skills also require delegated task
scope, active plugin ownership, robot profile support, fresh runtime state,
`SafetyGate` acceptance, and an exact action authorization.

## Files Added

- `src/fireclaw_core/policy/__init__.py`
- `src/fireclaw_core/policy/capability.py`
- `tests/test_capability_policy.py`
- `docs/architecture/capability-policy-pipeline.md`

## Files Modified for Integration

- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/execution/executor.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/gateway/gateway.py`
- `tests/test_agent.py`
- `tests/test_gateway.py`
- `tests/test_gateway_structured_task.py`
- `README.md`
- `docs/architecture/fireclaw-openclaw-alignment.md`
- `docs/architecture/fireclaw-openclaw-alignment.zh-CN.md`

## Implemented Behavior

- Added `CapabilityPolicyPipeline` with ordered identity, delegation, plugin
  ownership, robot profile, runtime state, safety, and authorization stages.
- Added planning-time capability projection. The Robot Agent LLM sees only
  skills allowed by the current task, Plugin Host, profile, and state.
- Added execution preflight and a second evaluation with fresh robot state
  immediately before the executor calls a skill.
- Bound high-risk approval to an unexpired exact skill-input hash.
- Added `capability.policy_preflight` and
  `capability.policy_decided` audit events.
- Made executor policy callback errors fail closed.
- Reused the same delegation helper in Robot Agent decision validation to
  avoid divergent task-contract policy.
- Routed the legacy interactive pending-confirmation execution path through
  `_execute_policy_checked_plan`; CodeGraph-assisted bypass inspection found
  this as the only direct `FireClawAgent` executor call outside the helper.

## Verification So Far

- `python -m compileall -q src/fireclaw_core`: passed.
- Capability policy, Robot Agent policy, and executor tests: `35 passed`.
- Agent and Robot Agent runtime/planner/deliberation paths: `72 passed`.
- Selected Gateway/profile/authorization paths: one old event-order assertion
  was updated, then the failing test passed.
- Planning projection integration assertions: `2 passed`.
- Combined policy, Gateway, authorization, resource, safety, Plugin Host,
  Harness, profile, and Robot Agent regression:
  `207 passed in 34.08s`.
- Full suite:
  `1837 passed, 6 skipped in 153.54s`.
- `git diff --check`: passed.
- `python -m compileall -q src/fireclaw_core`: passed.

## Current Conclusion

The Agent no longer treats "installed", "visible to the LLM", and "allowed to
execute now" as the same state. Policy evaluation is centralized and
auditable, while Safety Gate, authorization consumption, resource leasing,
and skill execution retain separate ownership.

## Research-Level Impact

This closes an engineering and safety-architecture gap and creates observable
data for policy-stage ablations. It is not independently a publication-level
planning contribution. Research value would require experiments showing that
staged projection and execution revalidation reduce unsafe or invalid actions
under state drift, authorization mismatch, and partial capability failure.

## Next Step

Add production authentication for actor identity and scope claims before
deploying the policy chain on a real robot. After that, add adversarial and
state-drift scenario tests that measure stage-specific invalid-action
reduction rather than only unit-level correctness.
