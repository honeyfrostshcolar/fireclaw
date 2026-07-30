# P0 host-safety gap closure

## Timestamp

- 2026-07-30 23:06-23:12 +08

## Task goal

Close every P0 gap identified in
`fireclaw-openclaw-host-safety-reaudit.md`, while preserving the invariant
that simulation grants broader Agent capabilities without granting direct,
unbounded access to the host machine.

## OpenClaw analogues reused

The implementation followed the OpenClaw structures already inspected during
the preceding audit:

- `openclaw/src/agents/sandbox/docker.ts`
- `openclaw/src/agents/sandbox/validate-sandbox-security.ts`
- `openclaw/src/agents/agent-tools.policy.ts`
- `openclaw/src/agents/agent-tools.execution-validation.ts`
- `openclaw/src/agents/agent-tools.before-tool-call.policy.ts`
- `openclaw/src/plugins/plugin-api.types.ts`
- `openclaw/src/plugins/registry.ts`
- `openclaw/src/plugins/plugin-registration-transaction.ts`
- `openclaw/src/security/audit.ts`
- `openclaw/docs/gateway/security/audit-checks.md`

FireClaw adaptations keep physical actions outside the generic computer Tool
runtime and add robotics-specific fail-closed deployment modes.

## Completed implementation

### 1. Sandbox workspace and process bounds

Files:

- `src/fireclaw_core/policy/deployment.py`
- `src/fireclaw_core/agent/computer_tools.py`
- `fireclaw.example.toml`

Changes:

- Docker now mounts the role workspace at `/workspace` as `readonly`.
- Persistent writes must use `computer_write_file`.
- Added `max_file_bytes`, `max_workspace_bytes`,
  `max_workspace_files`, and `max_concurrent_processes`.
- Added hard upper limits for timeout, memory, CPU, PID, file, workspace, and
  concurrency settings.
- `computer_read_file`, overwrite hashing, and legacy Tool staging now use
  bounded regular-file reads rather than unbounded `Path.read_bytes()`.
- Workspace quota is revalidated before process launch.
- Workspace symlinks and special files fail closed.
- `.fireclaw/` is reserved from LLM-facing file Tools.
- A bounded semaphore prevents aggregate container fan-out.
- Generic Docker `network="bridge"` is rejected; only `network="none"` is
  accepted.

### 2. Gateway and model-provider response bounds

Files:

- `src/fireclaw_core/gateway/transport.py`
- `src/fireclaw_core/subagent/subagent_client.py`
- `src/fireclaw_core/mission/mission_gateway_client.py`
- `src/fireclaw_core/provider/provider.py`

Changes:

- Gateway clients use an opener with environment proxies disabled.
- Automatic HTTP redirects are disabled, preventing authenticated request
  forwarding to a redirect target.
- Mission and Robot Gateway JSON responses use a streaming hard byte limit,
  including HTTP error bodies and memory-replication responses.
- SSE parsing has a per-event byte limit; list-returning cursor helpers cap
  accumulated events.
- `OpenAICompatProvider` streams response bytes with a hard limit, disables
  redirects, defaults to `trust_env=False`, rejects remote plaintext HTTP, and
  bounds malformed-response text.
- Provider tool calls are count-bounded and must decode arguments to JSON
  objects.

### 3. Executable plugin trust boundary

Files:

- `src/fireclaw_core/plugin/plugin_host.py`
- `src/fireclaw_core/plugin/plugin_runtime.py`
- `src/fireclaw_core/plugin/trusted_callback.py`
- `src/fireclaw_core/agent/tool_runtime.py`
- `src/fireclaw_core/infra/workspace_skills.py`

Changes:

- Plugin records now carry `builtin`, `trusted`, `sandboxed`, or
  `descriptor_only` trust.
- `descriptor_only` data is registered through `register_data_service()`,
  which accepts no plugin callback. Generic `activate()` rejects this trust
  level.
- Descriptor-only plugins cannot contribute executable objects or dispose
  callbacks.
- Legacy executable manifests are recorded as `sandboxed` and may contribute
  only Docker-bound Tool wrappers.
- Programmatic generic Agent Tools, Harnesses, and physical capability
  projections are explicitly `trusted`; built-in computer and ROS diagnostic
  plugins are `builtin`.
- Trusted Tool handlers and all provider/memory/approval/before-tool Hooks
  have deadlines and JSON result byte limits.
- Timeout threads are daemonized. This is an operational loop bound, not an
  in-process security sandbox; only explicitly trusted code may use it.

### 4. Security audit completion

Files:

- `src/fireclaw_core/security/audit.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `docs/architecture/deployment-tool-policy.md`
- `docs/architecture/docker-sandbox-lifecycle-security.md`
- `docs/architecture/plugin-host-agent-harness.md`

Changes:

- `security-audit --deep` is now exposed by both CLI entry paths.
- Deep plugin scans reject executable artifacts in descriptor-only
  directories and reject descriptors larger than 1 MiB before reading them.
- Effective sandbox findings include network, file, workspace, and
  concurrency budgets.
- Example config and legacy manifest permissions were tightened from `0664`
  to `0644`.
- Documentation now describes read-only workspace mounts, typed persistence,
  trust classes, response bounds, and the prohibition on generic bridge
  networking.

## Commands and results

Focused tests:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_plugin_host.py tests/test_plugin_runtime.py \
  tests/test_agent_tool_runtime.py \
  tests/test_deployment_agent_tool_integration.py \
  tests/test_planner_builder.py
```

Result: `65 passed`.

Final full regression, run with local socket permission for HTTP/HTTPS/mTLS
integration tests:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
```

Result: `1977 passed, 7 skipped in 169.79s`.

Deep deployment audit:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core \
  security-audit --config fireclaw.example.toml --deep --fail-on never
```

Result: `0 critical`, `0 warn`, `4 info`, status `ok`.

`git diff --check` passed. Ruff was not run because it is not installed in the
`py310` environment (`No module named ruff`).

## Current conclusion

All four P0 gaps from the 22:40 re-audit are closed in code and covered by
tests. An LLM-controlled simulation process no longer has a writable host bind
mount or generic network bridge, remote responses cannot allocate unbounded
host memory, redirects cannot forward Gateway credentials, and untrusted
descriptor files cannot register or execute in-process callbacks.

This is an engineering safety baseline, not a formal containment proof or a
research contribution by itself.

## Remaining non-P0 work and uncertainty

- Docker is unavailable on this machine, so containment is verified with a
  fake-Docker lifecycle suite rather than a live daemon adversarial test.
- Image signatures, SBOM verification, trusted build provenance, and registry
  admission remain deployment responsibilities.
- Trusted in-process callbacks cannot be forcibly killed after timeout; a
  malicious or defective trusted built-in can still consume resources. Truly
  third-party executable plugins need a dedicated out-of-process plugin
  service before they can be supported.
- Workspace quotas are enforced by the FireClaw adapter, not by a dedicated
  filesystem/project quota. Other trusted host processes could still fill the
  same partition.
- Host file operations do not yet use a directory-fd/openat2 no-symlink API,
  leaving a local privileged-process TOCTOU hardening opportunity.
- ROS diagnostic subprocesses still need an allowlisted environment instead
  of inheriting the complete FireClaw environment.
- A real deployment should make a clean security audit a mandatory startup
  admission gate and add continuous drift/retention monitoring.

## Next recommended step

Do not add more generic computer authority before simulation testing. Start
the TurtleBot3/navigation experiment with the current bounded Tool set, record
which diagnostics and parameter changes are actually needed, and add only
typed capabilities supported by observed failure cases.
