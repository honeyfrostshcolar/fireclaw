# P0 Operational Correctness Repair Planning

## Timestamp

- 2026-07-24 19:41+08 review completed.
- 2026-07-24 19:59:01 +0800 repair plan self-review completed.

## Task goal

Create a Superpowers `writing-plans` implementation plan for the six issue
groups found in `memory-operational-correctness-p0-review.md`. This turn is
planning-only; no production code is modified.

## User decisions

- Use the Superpowers process.
- Produce a repair plan before implementation.
- Continue using the existing Python 3.10 dependency-free workflow.
- Do not require a Python 3.11 environment or formal pytest installation.
- Do not commit unless explicitly requested.

## Skills and workflow used

- Read the installed Superpowers `using-superpowers`, `writing-plans`, and
  `test-driven-development` instructions.
- Applied the required RED-GREEN-REFACTOR task structure, exact file paths,
  commands, expected failures, minimal implementation contracts, compatibility
  checks, and optional commit points.
- Kept one P0 plan because replication, hydration, consolidation ranges, and
  terminal triggers jointly define the already approved operational-correctness
  acceptance boundary. The plan still separates them into six independently
  testable tasks.

## CodeGraph inspection

Inspected current source and blast radius for:

- `RobotSubagentClient.get_memory_replication`
- robot Gateway `/memory/replication`
- `MissionGateway.sync_robot_memory`
- `ReplicationPeerPolicy`, request/batch signing, and replay verification
- `ReplicationBatch`, `ReplicationEnvelope`, exporter, and reconciler
- `EmbodiedWorkingMemory.hydrate_recent`
- `build_mission_agent_from_paths`
- `ConsolidationStateStore`
- `MemoryConsolidationCoordinator`
- `MissionAgent._record_terminal_outcome`, submit, trace, and cancel paths
- `FireClawConsolidationEngine.consolidate_events`
- existing P0 and compatibility tests

The local `openclaw/` reference remains absent. The indexed `emem-main/`
analogue confirms episode-end and time-window consolidation behavior, but its
memory facade does not provide FireClaw's authenticated robot replication or
safety-critical terminal journaling. The plan therefore preserves eMEM's
episode/gist behavior while keeping FireClaw-specific authority, safety,
runtime, and replication controls.

## Important plan decisions

- Verify raw signed batch dictionaries before envelope parsing.
- Keep record envelopes schema v1 and make the batch protocol v2.
- Use explicit server/client replication security configuration with no
  default credentials.
- Use a bounded replay cache and consume nonces only after valid signatures.
- Hydrate newest evidence with deterministic per-mission rounds.
- Atomically reserve boundary ranges using completed and already reserved
  tails.
- Requeue failed/running boundaries on startup.
- Add explicit mission/subtask scope kind and exact non-wildcard selection.
- Use deterministic terminal IDs as actual authority event IDs.
- Cover immediate submit, trace, cancellation, and final mission terminal
  transitions.
- Require a test runner to report a nonzero executed count; direct no-op test
  file execution is not accepted as evidence.

## Plan created

- `docs/superpowers/plans/2026-07-24-memory-operational-correctness-p0-review-fixes.md`

It contains six serial tasks:

1. replication primitives and fail-closed reconciliation;
2. real client/Gateway authenticated transport;
3. newest-first startup hydration;
4. durable boundary reservation and failure recovery;
5. terminal trigger and exact scope coverage;
6. worker lifecycle, documentation, and final proof.

## Plan self-review

- Spec coverage: all six review finding groups map to tasks and all original
  reproductions map to final verification.
- Placeholder scan: no `TBD`, `TODO`, deferred implementation, generic error
  handling, or unnamed test instructions remain.
- Type consistency: replication identity, v1/v2 batch compatibility, raw
  payload signing, policy IDs, scope kinds, watermarks, and terminal IDs are
  defined before downstream use.
- Test helper consistency: helper functions used by the planned RED tests are
  defined in the plan or already exist in the named test module.
- Markdown verification:
  - 6 tasks.
  - 42 checklist steps.
  - 76 code fences, correctly paired.
  - 14 final acceptance criteria.
  - 2280 plan lines.
- `git diff --check` passed for the plan and planning record.

## Current conclusion

Task 1 must be implemented first because the current real-runtime reconciler
can import a forged batch when auth metadata is present but verification
dependencies are absent. Task 2 must then replace fake-only security behavior
with production client/Gateway wiring before any replication acceptance claim.

## Next recommended step

Execute Task 1 with Superpowers TDD. Watch every new test fail for the expected
reason before changing production code, then stop at the Task 1 checkpoint for
review.
