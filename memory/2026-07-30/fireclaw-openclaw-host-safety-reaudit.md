# FireClaw / OpenClaw host-safety re-audit

## Timestamp

- 2026-07-30 22:40 Asia/Ulaanbaatar

## Task goal

Reassess FireClaw security against the local OpenClaw reference, with the
specific invariant that neither simulation nor real-mode LLM activity should
be able to damage the host machine. This turn is assessment-only; no runtime
code was changed.

## Current progress

Previously completed controls were confirmed:

- non-loopback Gateway startup requires authentication and TLS;
- Gateway request bodies, connections, SSE queues, Host, and Origin are
  bounded;
- simulation process execution has no host subprocess fallback and uses a
  pinned Docker image, no shell, a read-only container root, dropped
  capabilities, non-root UID/GID, resource limits, bounded output, named
  containers, and forced cleanup;
- Agent workspaces are canonical, role-specific, and cannot be configured as
  repository, credential, or protected system paths;
- legacy executable manifests use the same Docker boundary and are blocked in
  real mode;
- final Tool arguments are revalidated after hooks;
- real-mode mutations and physical actions use exact, short-lived,
  one-time-consumed authorizations;
- ROS diagnostic operations are typed, read-only, allowlisted, shell-free,
  output-bounded, and process-group terminated.

## OpenClaw analogues inspected

- `openclaw/docs/gateway/security/audit-checks.md`
- `openclaw/docs/gateway/security/index.md`
- `openclaw/src/security/audit.ts`
- `openclaw/src/security/audit.types.ts`
- `openclaw/src/agents/sandbox/validate-sandbox-security.ts`
- `openclaw/src/agents/agent-tools.policy.ts`
- `openclaw/src/agents/agent-tools.execution-validation.ts`
- `openclaw/src/agents/agent-tools.before-tool-call.policy.ts`

FireClaw source inspected:

- `src/fireclaw_core/agent/computer_tools.py`
- `src/fireclaw_core/agent/docker_sandbox.py`
- `src/fireclaw_core/agent/tool_runtime.py`
- `src/fireclaw_core/policy/deployment.py`
- `src/fireclaw_core/gateway/auth.py`
- `src/fireclaw_core/gateway/network_security.py`
- `src/fireclaw_core/gateway/transport.py`
- `src/fireclaw_core/subagent/subagent_client.py`
- `src/fireclaw_core/provider/provider.py`
- `src/fireclaw_core/ros/ros1_diagnostics.py`
- `src/fireclaw_core/ros/ros1_config.py`
- `src/fireclaw_core/plugin/plugin_host.py`
- `src/fireclaw_core/plugin/plugin_runtime.py`
- `src/fireclaw_core/security/audit.py`

## Commands and observations

The current example deployment audit was run with:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core \
  security-audit --config fireclaw.example.toml --fail-on never
```

Result:

- 2 critical findings;
- `fireclaw.example.toml` is mode `0664`;
- `skills/examples/echo_policy.skill.json` is mode `0664`;
- this is an audit of the repository example, not proof of a production
  deployment because no `fireclaw.toml` is present.

## Remaining P0 host-safety gaps

1. Simulation workspace storage is not quota-bounded.
   - Docker mounts the host workspace read/write.
   - CPU, memory, PID, time, and output are bounded, but writable bytes, file
     count, aggregate concurrent containers, and total workspace size are not.
   - An LLM can create a very large file inside `/workspace` and fill the host
     filesystem.
   - `computer_read_file` then uses `Path.read_bytes()` before truncating,
     allowing a large sandbox-created file to exhaust host memory.

2. Outbound response bodies and redirects are not bounded strongly enough.
   - `RobotSubagentClient` calls `response.read()` without a byte cap for task,
     state, trace, event, and replication responses.
   - `GatewayHttpTransport` validates only the initial URL; urllib redirects
     are not revalidated per hop and may forward authentication headers.
   - A compromised robot endpoint could cause memory exhaustion, SSRF, or
     credential forwarding.
   - `OpenAICompatProvider` also buffers the complete provider response and
     includes complete response text in malformed-response errors.

3. Generic Plugin Tool handlers and hooks are trusted in-process Python.
   - `AgentToolRuntime` enforces the declared effect and schema, then directly
     calls `tool.handler(...)`.
   - A plugin can declare `effect="read"` while its handler mutates arbitrary
     host state, blocks indefinitely, returns an unbounded object, or allocates
     unbounded memory.
   - Current built-ins are reviewed, but the boundary is not sufficient for
     third-party or dynamically installed plugins. Metadata is not a sandbox.

4. Simulation permits Docker `network="bridge"` with only an audit warning.
   - This does not expose host networking directly, but it permits probing
     reachable host/LAN services and broadens prompt-injection impact.
   - For the requested host-safety invariant, network access needs a reviewed
     egress proxy/capability rather than an unrestricted bridge mode.

## Remaining P1 gaps

- `security-audit` accepts a `deep` argument but does not perform deep checks;
  the CLI does not expose `--deep`. There is no live Gateway probe, plugin
  code scan, Skill scan, or runtime drift check comparable to OpenClaw.
- Plugin directories are audited only when passed explicitly, and provenance
  is warned about rather than verified with an immutable digest/signature.
- Sandbox timeout, memory, CPU, and PID settings have lower bounds but no hard
  upper bounds; aggregate execution concurrency is not globally budgeted.
- Host-side file reads/writes use path re-resolution and ordinary path APIs,
  not a directory-fd/openat2-style no-symlink boundary, leaving a local
  concurrency/TOCTOU hardening gap.
- ROS diagnostic subprocesses inherit the full FireClaw environment rather
  than a ROS-specific allowlisted environment.
- Gateway shared tokens have no minimum entropy check and map every holder to
  one admin principal; unauthenticated loopback is also admin by design.
- Audit findings are not a mandatory startup gate, and logs/state/workspaces
  have no complete aggregate retention or disk quota policy.
- Docker isolation has only fake-Docker integration tests on this machine;
  Docker is not installed, so no live daemon smoke test or adversarial
  containment test has been completed.

## Conclusion

Real mode now has a credible host-side policy boundary for the currently
registered built-in tools. Simulation mode is not yet strong enough to claim
that LLM activity cannot damage the host, mainly because of writable workspace
storage, unbounded inbound responses, redirect handling, and trusted
in-process plugin handlers.

## Recommended next step

Implement the remaining P0 items in this order:

1. workspace/disk quotas plus bounded streaming file I/O and aggregate sandbox
   concurrency;
2. bounded response readers, redirect denial/per-hop validation, and
   cross-origin credential stripping for Robot Gateway and model-provider
   clients;
3. explicit trusted-plugin policy, immutable provenance, handler/hook
   time/result bounds, and an out-of-process boundary for untrusted plugins;
4. remove unrestricted bridge networking from the default capability model
   and replace it with explicit reviewed egress capabilities.
