# P1 Plugin Host and Agent Harness

## 2026-07-29 19:45 +08

### Task goal

Implement the two P1 architecture findings after the P0 execution-authority
work:

1. replace four independent registration mechanisms with one plugin host
   contribution model;
2. replace role-specific provider/tool turn implementations with one shared
   Agent Harness beneath `BoundedAgentLoop`.

The user explicitly required OpenClaw-first design and rejected another
independent FireClaw-specific registry or loop.

### Resume state

- P0 exact execution authorization, SQLite WAL authority, and persistent
  resource leases were complete.
- The working tree already contained uncommitted P0 and physical skill plugin
  changes. None were reverted.
- Last verified full suite before this task:
  `1822 passed, 6 deselected`.

### OpenClaw analogues inspected with CodeGraph

Plugin host:

- `openclaw/src/plugins/plugin-api.types.ts`
  - `OpenClawPluginApi`
  - heterogeneous registration methods under one injected plugin identity.
- `openclaw/src/plugins/registry.ts`
  - `createPluginRegistry`.
- `openclaw/src/plugins/registry-api.ts`
  - `createPluginApiFactory`.
- `openclaw/src/plugins/plugin-registration-transaction.ts`
  - snapshot, atomic commit, rollback of registry and process-global
    contributions.
- `openclaw/src/plugins/tools.ts`
  - projection, conflict, and malformed tool handling.

Agent Harness:

- `openclaw/src/agents/harness/types.ts`
  - `AgentHarness`, `supports`, `runAttempt`, result classification,
    finalization, compaction, and lifecycle.
- `openclaw/src/agents/harness/registry.ts`
  - harness ownership, reset, and disposal.
- `openclaw/src/agents/harness/selection.ts`
  - provider/model/runtime compatibility selection and no unsafe implicit
    fallback.

### FireClaw adaptation

#### Unified plugin host

Added `src/fireclaw_core/plugin/plugin_host.py`:

- `FireClawPluginHost`
- `FireClawPluginApi`
- `PluginActivationTransaction`
- `PluginRecord`
- `PluginContribution`
- `PluginDiagnostic`
- `PluginRegistrationError`

Contribution types:

- `tool`
- `physical_capability`
- `hook`
- `service`
- `context_engine`
- `agent_harness`

Host behavior:

- plugin identity is injected into the API and becomes the contribution owner;
- API version is checked before publication;
- all staged contributions are conflict-checked before any are committed;
- activation failure publishes no staged contribution;
- failed activation records a bounded diagnostic with phase/code/identity;
- active plugins can add contributions in later compatibility migration calls;
- disposal removes only contributions still owned by that plugin;
- dispose callbacks run in reverse order and one failure does not stop cleanup;
- inventory exposes plugin status and ownership but not executable callback
  internals.

Compatibility migrations:

- `PluginRuntime` descriptors are host `service` contributions and callables
  are host `hook` contributions.
- `PhysicalSkillCatalog` is a host-backed
  `physical_capability` projection.
- `SkillRegistry` is a host-backed executable `tool` projection.
- `load_workspace_skills(..., plugin_host=...)` contributes workspace
  subprocess tools with manifest source metadata.
- `FireClawAgent` creates one host for builtin physical definitions, their
  bound executable tools, and workspace tools.
- Old public registry methods remain for incremental compatibility, but when
  supplied the same host they share one owner/conflict table.

#### Shared Agent Harness

Added `src/fireclaw_core/agent/harness.py`:

- `AgentHarness` protocol
- `AgentHarnessAttempt`
- `AgentHarnessAttemptResult`
- `AgentHarnessError`
- `ProviderAgentHarness`
- host registration helper

One Harness attempt owns:

1. mandatory `ModelAwareContextManager.fit`;
2. tool schema normalization and duplicate rejection;
3. allowed tool projection validation;
4. cancellation before provider egress;
5. the only production `ProviderRuntime.chat_completion` call for the
   migrated agent paths;
6. cancellation after provider return;
7. tool-call count, visible name, and object-argument validation;
8. stable error classification and optional turn trace.

Migrated LLM paths:

- `LLMMissionPlanner.plan`
- `LLMMissionPlanner.decide`
- `LLMRobotAgentPlanner.plan`, including the advisory memory-query second
  round
- `LLMRobotAgentDecisionPolicy.decide`

After migration, an `rg` check found `chat_completion(` in these paths only at
`src/fireclaw_core/agent/harness.py`. Role code still owns its prompt,
authoritative/continuity/advisory split, and semantic result parser.

`BoundedAgentLoop` remains separate:

- Loop: iterations, total timeout, checkpoints, pending physical operation,
  reconciliation, progress requirement, terminal state.
- Harness: one tokenizer-aware model/tool turn.

The Harness does not execute skills and cannot bypass Robot task envelope,
`SafetyGate`, exact authorization, resource leases, or Adapter boundaries.

### Files added

- `src/fireclaw_core/plugin/plugin_host.py`
- `src/fireclaw_core/agent/harness.py`
- `tests/test_plugin_host.py`
- `tests/test_agent_harness.py`
- `docs/architecture/plugin-host-agent-harness.md`
- this memory record

### Files modified

- `src/fireclaw_core/plugin/plugin_runtime.py`
- `src/fireclaw_core/plugin/__init__.py`
- `src/fireclaw_core/execution/skill_plugin.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/infra/workspace_skills.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/__init__.py`
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/agent/robot_deliberation.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `README.md`
- FireClaw/OpenClaw alignment documents
- physical skill plugin architecture document

### Validation

- Plugin/skill/agent compatibility set:
  `113 passed`.
- Central/Robot LLM planner and deliberation set:
  `55 passed`.
- Expanded plugin host/Harness and compatibility set:
  `123 passed`.
- New architecture tests:
  `9 passed`.
- Cross-module plugin, Harness, planner, skill, and Gateway integration set:
  `209 passed in 9.85s`.
- Full Gateway set:
  `30 passed in 23.00s`.
- Full non-ROS suite:
  `1831 passed, 6 deselected in 150.47s`.
- `python -m compileall -q src/fireclaw_core tests`: passed.
- `git diff --check`: passed.

### 2026-07-29 20:03 +08 regression finding

The first full-suite run stopped at
`test_gateway_events_endpoint_filters_by_task_id`. A standalone run with
`faulthandler_timeout=20` showed a lock-order inversion between two concurrent
task workers:

- a terminal-result path held the SQLite runtime transaction and then waited
  for the legacy Gateway `_event_lock`;
- a live skill-event path held `_event_lock` and then waited for the SQLite
  runtime transaction.

The main test thread then waited for `_task_lock`, which the terminal-result
path also held.

The P0 SQLite event authority already serializes database writes through its
transaction visibility lock and serializes JSONL audit mirrors through its
audit lock. The old Gateway `_event_lock` was therefore redundant and unsafe.
It was removed from `FireClawGateway`; `_append_event` now enters only the
authoritative SQLite event write path. The exact formerly hanging test passed
in `1.81s`, followed by the full Gateway and full repository results above.

### Engineering conclusion

This closes the two named P1 architecture defects:

- the old registration APIs no longer require independent ownership/conflict
  semantics;
- Mission and Robot roles now share the complete model/tool turn boundary,
  not only the outer bounded loop.

### Research conclusion

This is engineering infrastructure, not a publication contribution by
itself. It reduces implementation confounds for future experiments:

- one can compare Mission versus Robot policies while holding provider,
  context, tool validation, cancellation, and error behavior constant;
- plugin/harness fault injection can measure activation rollback,
  cross-plugin collision isolation, context overflow, malformed tool output,
  cancellation races, and provider failures.

A research claim still needs a robotics-specific protocol and experiments,
for example evidence-bound capability projection and fault-tolerant embodied
execution compared with direct unrestricted ReAct tool access.

### Next recommended step

Implement the next P1 item as a unified capability policy pipeline with
provenance:

```text
identity/scope
-> mission delegation envelope
-> plugin/tool exposure
-> robot capability profile
-> current state and SafetyGate
-> exact execution authorization
```
