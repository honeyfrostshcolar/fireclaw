# P1 Fixed Runtime Root And Workspace Path Security

## Timestamp

- 2026-07-30 20:47:15 +08

## Task Goal

Eliminate FireClaw's dependence on the shell launch directory and prevent
Mission/Robot Agent computer workspaces from being configured as dangerous
host paths, repository source/plugin directories, credential directories, or
symlink escapes.

## OpenClaw Analogues Inspected

- `openclaw/src/agents/sandbox/host-paths.ts`
  - Canonicalizes a requested host path through the deepest existing ancestor.
- `openclaw/src/agents/sandbox/validate-sandbox-security.ts`
  - Rejects protected system paths, credential-bearing home directories, broad
    parent mounts, and paths outside explicit allowed roots.
- `openclaw/src/agents/sandbox/docker.ts`
  - Runs security validation before constructing Docker bind arguments.
- `openclaw/src/agents/sandbox/workspace-mounts.ts`
  - Separates the managed Agent workspace from plugin/Skill mounts.

FireClaw reuses those structural principles, while adding a fixed process
runtime root because the Python Gateway currently has many relative state,
TLS, model, ROS, and workspace paths.

## Implementation

### Stable process runtime root

- Added `src/fireclaw_core/infra/runtime_paths.py`.
- Runtime-root precedence:
  1. CLI/config `runtime.root_dir`;
  2. `FIRECLAW_HOME`;
  3. directory containing `fireclaw.toml`;
  4. `~/.fireclaw`.
- Mission and Robot Gateway CLIs enter the canonical runtime root before
  constructing long-lived services, and restore the prior cwd on exit.
- A new runtime root is created with mode `0700`.
- `/`, the account home itself, protected system directories, and credential
  directories cannot be used as runtime roots.
- The embedding API does not perform a process-global `chdir`; embedding hosts
  must pass absolute paths and own their process cwd explicitly.

### Canonical Agent workspace policy

- Added `src/fireclaw_core/infra/path_security.py`.
- `SandboxProfile` now records `allowed_workspace_roots` and validates the
  canonical workspace against them.
- Rejects:
  - `/`, Home, protected system paths, and broad parents covering them;
  - `.ssh`, `.aws`, `.docker`, `.gnupg`, `.config`, and related credentials;
  - the FireClaw repository root;
  - `.git`, `.codegraph`, `src`, `skills`, `extensions`, and `openclaw`;
  - paths outside explicit allowed roots;
  - symlink paths whose existing ancestor resolves outside the allowed root.
- Mission Agent is restricted to `data/mission/agent-workspace`.
- Robot Agent is restricted to `data/robot/agent-workspace`.
- Authoritative state directories are not valid computer-tool workspaces.

### Docker bind time-of-use protection

- `ComputerSandbox` validates and pins its canonical workspace when created.
- It validates the path again immediately before each Docker bind.
- If the workspace is replaced or retargeted through a symlink after profile
  validation, execution is rejected before Docker starts.
- Literal search found one FireClaw Docker host bind construction site:
  `src/fireclaw_core/agent/computer_tools.py`; it uses this validation path.

### Observability and configuration

- Added `[runtime].root_dir` to `fireclaw.example.toml`.
- Robot `/state` exposes the effective process working directory, Agent
  workspace, and allowed workspace roots.
- Mission/Robot startup output includes the effective runtime root.
- Added `docs/architecture/runtime-workspace-path-security.md` and updated the
  README and OpenClaw alignment/gap documents.

## Files Added

- `src/fireclaw_core/infra/path_security.py`
- `src/fireclaw_core/infra/runtime_paths.py`
- `tests/test_runtime_path_security.py`
- `docs/architecture/runtime-workspace-path-security.md`
- `memory/2026-07-30/p1-runtime-workspace-path-security.md`

## Files Modified For This Task

- `src/fireclaw_core/policy/deployment.py`
- `src/fireclaw_core/agent/computer_tools.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `tests/test_gateway.py`
- `tests/test_gateway_robot_agent_cli.py`
- `fireclaw.example.toml`
- `README.md`
- `docs/architecture/fireclaw-openclaw-alignment.md`
- `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`

The worktree also contains earlier P0 authentication, secure transport,
legacy executable tool sandbox, and ROS diagnostics changes. They were
preserved and not reverted.

## Verification So Far

- Python compile:
  - `/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q src tests/test_runtime_path_security.py`
- Patch whitespace:
  - `git diff --check`
- Focused path/deployment/sandbox tests:
  - `38 passed in 0.68s`
- Earlier broad related set:
  - `126 passed in 55.81s`
  - Required unsandboxed loopback socket access.

## Current Conclusion

The production Mission and Robot Gateway processes no longer inherit an
uncontrolled launch cwd. Computer-tool Docker binds are limited to dedicated,
canonical Agent workspaces and are revalidated at the point of use. This
closes the reported repository-root, credential-directory, system-directory,
and symlink-retargeting workspace risks.

## Remaining Step

Completed:

- Loopback Gateway integration set:
  - `127 passed in 55.74s`
- Complete FireClaw regression:
  - `1935 passed, 7 skipped in 163.77s`

No required implementation or verification work remains for this security
item. Commit and push only when explicitly requested.
