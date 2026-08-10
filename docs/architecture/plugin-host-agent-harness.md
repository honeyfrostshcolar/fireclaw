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

## Manifest-First Extension Loading

The OpenClaw-shaped discovery path is implemented by
`src/fireclaw_core/plugin/extension_loader.py`:

```text
configured extension roots
  -> read fireclaw.plugin.json (data only)
  -> validate id, API version, relative entrypoint, trust class, and bounds
  -> skip disabled extensions
  -> import the declared provider entrypoint in a private package namespace
  -> call provider.register(plugin_scoped_api)
  -> atomically commit all contributions to FireClawPluginHost
```

The manifest is not a Tool definition and it does not grant host command
execution. The provider owns its Tool schemas, Runtime/ROS adapter, Skill
workflow, and plugin-specific configuration. Core code passes opaque
manifest-keyed configuration and generic services; it does not add a new
Gateway branch for each Plugin. `extensions/navigation-move-base` is the first
package using this contract. The former core navigation registration module
has been removed.

Native Python extensions should use the public `fireclaw_plugin_sdk.ToolSpec`
contract. The extension entrypoint contributes a host-neutral Tool description;
the Plugin Host converts it to the internal `AgentTool` only at the registration
boundary. This keeps third-party extensions from importing
`fireclaw_core.agent.tool_runtime`, `fireclaw_core.plugin.plugin_host`, or
deployment-policy implementation modules. Existing first-party callers that
already construct `AgentTool` remain supported during migration.

This mirrors OpenClaw's separation of discovery, manifest validation, registry
ownership, and runtime activation. It is still an in-process trusted boundary;
third-party package signatures and a general isolated Plugin process remain
future hardening work.

Some Python class names remain compatibility projections over the one Plugin
Host ownership table:

- `PhysicalSkillPlugin` is the legacy-named internal form of a physical Tool
  contribution;
- `SkillRegistry` is the legacy-named executable Tool projection for one
  Robot Agent;
- `PluginRuntime` projects descriptor and hook contributions represented by
  the unified Host.

The former workspace executable-Tool loader and its separate manifest path
have been removed. Process Tools must now be contributed by a Plugin and pass
the normal deployment policy and sandbox boundary.

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

- Third-party package signatures and a general isolated Plugin process are not
  implemented. Untrusted executable contributions therefore fail closed.
- Harness selection is explicit injection; provider/model compatibility
  probing is not yet a fleet-wide policy.
- Tool permission provenance is enforced through the unified capability
  policy and exact execution-authorization pipeline.
- Plugin Host and Agent Harness are engineering foundations, not standalone
  research contributions.
