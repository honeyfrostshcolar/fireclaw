# OpenClaw Parity v1 Completion Record

Date: 2026-06-10

## Status

This roadmap has been implemented as **FireClaw OpenClaw parity v1**. It should
not be re-executed as an unchecked implementation plan.

Current verification target:

```bash
.venv/bin/python -m pytest -q
```

Latest audited result after the final integration pass:

```text
794 passed, 6 skipped
```

## Completed v1 Work

- OpenClaw-style `TaskRecord`, `TaskDeliveryState`, snapshots, JSONL store, and
  queue-to-registry conversion.
- Durable `SubagentRunRecord` / `JsonlSubagentRegistry`, with
  `RobotSubagentClient` submit/cancel/terminal-trace wiring.
- `PluginRuntime` descriptor loading, known hook validation, and hook name
  aggregation.
- `MemoryRetriever` lexical plus optional embedding rank fusion, transcript
  ingestion, and `MissionAgent` planner-context wiring.
- `MissionGatewayClient` typed synchronous client for JSON endpoints.
- SSE cursor replay via `StreamEvent.sequence`, `Last-Event-ID`, and
  `after_sequence`.
- `ApprovalRuntime` token creation, expiry, pending projection, and
  `MissionGateway` approval endpoint wiring.
- `doctor --fix` stale task queue repair flow, memory index/plugin checks,
  ROS1 hardware smoke template, and ROS2 adapter implementation plan.

## Remaining Gaps

These are not regressions in the v1 implementation; they are the next layer of
platform maturity needed before claiming full OpenClaw parity.

1. **Task/session runtime reconciliation**
   - `TaskRegistry` is implemented, but it is not yet the central source of
     truth for all `MissionAgent` / `MissionScheduler` lifecycle transitions.
   - Missing: session store, runtime reconciliation, child session cleanup.

2. **Subagent completion routing**
   - Subagent run lineage is durable and terminal trace observation now updates
     the registry.
   - Missing: event-driven completion routing from robot events into parent
     mission/subtask state, plus orphan recovery.

3. **Executable plugin SDK hooks**
   - `PluginRuntime` validates and aggregates descriptor hook names.
   - Missing: safe loading/execution of provider, memory, and tool approval hook
     callables with permission boundaries.

4. **Memory provider lifecycle and evaluation**
   - Ranked retrieval is wired into planner context when configured.
   - Missing: embedding provider lifecycle/config, session transcript indexing
     policy, retrieval quality evaluation set.

5. **Control-plane streaming client**
   - Server-side SSE cursor replay exists and the JSON typed client exists.
   - Missing: typed client-side SSE iterator/reconnect abstraction and
     WebSocket/control-channel parity.

6. **Approval durability and external relay**
   - Mission Gateway can create approval runtime tokens and expose pending
     projections.
   - Missing: persistent token storage across process restart and relay to
     external operator channels.

7. **Deployment and robotics proof**
   - ROS1 local smoke proof exists; ROS2 remains a protocol boundary and plan.
   - Missing: real robot hardware smoke proof, native `rclpy` ROS2 adapter,
     fleet onboarding wizard, and full sandbox/process/network policy.

## Next Recommended Plan

Start a new plan for one focused maturity slice instead of continuing this
completed v1 roadmap. Recommended order:

1. Make `TaskRegistry` / `SubagentRegistry` the lifecycle projection used by
   mission trace and recovery.
2. Add client-side SSE iterator and reconnect handling to `MissionGatewayClient`.
3. Design executable plugin hook loading with explicit permission boundaries.
4. Add persistent approval token storage and external operator relay.
5. Implement native ROS2 transport or run a real ROS1 hardware smoke proof,
   depending on available robotics hardware.
