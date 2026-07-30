# Plugin Host and Agent Harness

Terminology follows
[`plugin-skill-tool-terminology.md`](plugin-skill-tool-terminology.md):
Plugins own contributions, Tools are atomic executable operations, and Skills
are Agent-facing workflows that may coordinate multiple Tools. `SkillRegistry`
below is a legacy-named executable Tool projection.

## Purpose

FireClaw uses two host-level boundaries adapted from OpenClaw:

- `FireClawPluginHost` owns extension identity and lifecycle.
- `ProviderAgentHarness` owns one model/tool turn.

`BoundedAgentLoop` remains the owner of multi-turn limits, checkpointing,
physical-operation reconciliation, and terminal state. The Harness does not
execute robot Tools or algorithms and does not replace `SafetyGate`.

## OpenClaw Analogues

The implementation was based on these local OpenClaw structures:

- `src/plugins/plugin-api.types.ts`: one injected plugin API for heterogeneous
  contribution types;
- `src/plugins/registry.ts` and `src/plugins/registry-api.ts`: central
  ownership and registration;
- `src/plugins/plugin-registration-transaction.ts`: snapshot, commit, and
  rollback of plugin registration;
- `src/plugins/tools.ts`: tool conflict and projection validation;
- `src/agents/harness/types.ts`: a harness as the complete model/tool attempt
  boundary, rather than only a loop;
- `src/agents/harness/registry.ts` and `selection.ts`: harness ownership,
  lifecycle, compatibility, and selection.

FireClaw keeps the module shape but adds robotics constraints. Physical
capabilities remain unbound definitions until projected for one Robot
Adapter. Physical side effects still cross task-envelope policy,
`SafetyGate`, exact execution authorization, checkpoint-before-actuation, and
resource leases.

## Unified Plugin Contributions

One plugin-scoped `FireClawPluginApi` can contribute:

```text
tool
physical_capability
hook
service
context_engine
agent_harness
```

The host records the plugin owner for every contribution. Activation stages
all contributions first, validates API version and conflicts, then commits
the complete set under one lock. A failure exposes a content-bounded
diagnostic and publishes none of the staged contributions. Disposal removes
only contributions still owned by that plugin and invokes its cleanup
callbacks in reverse order.

Each record carries an explicit execution trust class:

- `builtin`: executable contribution shipped with FireClaw;
- `trusted`: in-process callable explicitly admitted by host code;
- `sandboxed`: typed Tool wrapper that executes across the Docker boundary;
- `descriptor_only`: non-callable metadata only.

`descriptor_only` plugins cannot register Tools, Hooks, Harnesses, context
engines, physical capabilities, or dispose callbacks. `sandboxed` plugins can
register only Tool wrappers marked with the Docker execution boundary.
Trusted in-process Tool handlers and Hooks have deadlines and JSON result byte
limits; these are operational bounds, not a substitute for sandboxing.

The old APIs are compatibility projections:

- `PluginRuntime` projects descriptors and callable hooks;
- `PhysicalSkillCatalog` projects unbound physical capabilities;
- `SkillRegistry` projects executable tools bound to the current Adapter;
- `load_workspace_skills(..., plugin_host=...)` contributes legacy process
  Tools only after deployment policy allows `effect=process` with
  `requires_sandbox=true`; execution uses the shared `ComputerSandbox`.

Passing the same host makes these views share one ownership and conflict
table. Existing callers can migrate incrementally without maintaining a
second authoritative registry.

## Shared Agent Turn

Every LLM-backed Mission or Robot path now constructs an
`AgentHarnessAttempt` and calls `ProviderAgentHarness.run_attempt()`:

```text
role-specific authoritative / continuity / advisory context
-> ModelAwareContextManager.fit
-> normalized visible tool schemas
-> cancellation check
-> ProviderRuntime.chat_completion
-> cancellation check
-> tool-call count, name, and argument validation
-> role-specific semantic parser
```

There is one production `chat_completion()` call site for these agent paths,
inside the Harness. The central planner and Robot Agent no longer implement
provider calls or basic tool-call validation independently.

Mission and Robot roles intentionally remain different above and below this
boundary:

- Mission builds snapshot/evidence/task-graph prompts and parses planning
  decisions.
- Robot builds task-envelope/local-state prompts and parses one local
  operation.
- The host validates tools before either parser.
- Physical tool execution still happens later through Robot policy,
  `SafetyGate`, `PlanExecutor`, resource leases, and the Adapter.

## Failure Semantics

The Harness classifies:

- context budget overflow;
- cancellation before or after provider egress;
- provider timeout/API/fallback errors;
- malformed or duplicate tool schemas;
- missing, excessive, unexposed, or malformed tool calls.

Role adapters map these stable codes into their existing public result types.
This preserves Mission and Robot API compatibility while removing divergent
model-call behavior.

## Current Boundaries

- Third-party package signatures and a general isolated plugin process are not
  implemented. Untrusted executable contributions therefore fail closed;
  legacy executable manifests are admitted only through Docker-backed Tool
  wrappers.
- Harness selection is explicit injection; provider/model compatibility
  probing is not yet a fleet-wide policy.
- Tool permission provenance is enforced through the unified capability
  policy and exact execution-authorization pipeline.
- Plugin Host and Agent Harness are engineering foundations, not standalone
  research contributions.
