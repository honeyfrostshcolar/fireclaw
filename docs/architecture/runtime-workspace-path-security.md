# Runtime And Workspace Path Security

## Boundary

FireClaw separates three host path roles:

- the **runtime root** is the stable process working directory used to resolve
  relative deployment paths;
- the **state/data directory** owns authoritative task, event, checkpoint, and
  memory stores;
- the **Agent workspace** is the only host directory exposed read-write to
  computer Tools inside Docker.

Mission Gateway and Robot Gateway change into the canonical runtime root before
assembling adapters, stores, plugins, sandboxes, or TLS material. The original
launch-shell directory is therefore not an implicit runtime input.

Runtime-root precedence is:

1. `--runtime-root`;
2. `[runtime].root_dir`;
3. absolute `FIRECLAW_HOME`;
4. the directory containing `--config`;
5. `~/.fireclaw`.

Relative `root_dir` values are resolved from the config directory. A relative
`FIRECLAW_HOME` is rejected because it would recreate launch-directory
dependence.

Robot capability Profiles are a separate configuration boundary. Their
`robot.ros1_config` and `robot.data_dir` values are canonicalized against the
Profile TOML directory when the Profile is loaded, before a Gateway changes to
its runtime root. Consequently, a generated Gateway can reuse the original
absolute Profile path without inheriting the deployment state directory as an
accidental path base.

## OpenClaw Analogue

The implementation follows these OpenClaw boundaries:

- `openclaw/src/agents/sandbox/host-paths.ts`
  canonicalizes through existing ancestors so parent symlinks are honored;
- `openclaw/src/agents/sandbox/validate-sandbox-security.ts`
  blocks sensitive host paths, paths that cover sensitive descendants, and
  sources outside approved roots;
- `openclaw/src/agents/sandbox/docker.ts`
  validates source roots before constructing Docker bind arguments;
- `openclaw/src/agents/sandbox/workspace-mounts.ts`
  keeps the managed workspace mount separate from source/Skill mounts.

FireClaw adds a stricter robotics adaptation: there is no dangerous override
for an LLM-writable workspace, and the writable workspace cannot be the
authoritative Mission/Robot state directory.

## Workspace Policy

`SandboxProfile` stores only canonical absolute paths. Its optional
`allowed_workspace_roots` are also canonical.

The policy rejects:

- `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/root`, `/boot`, `/run`, and
  `/var/run`, including descendants and broad parent mounts;
- the user's Home itself and credential directories such as `.ssh`, `.aws`,
  `.docker`, `.gnupg`, `.config`, and `.netrc`;
- the FireClaw repository root;
- FireClaw source, `.git`, `.codegraph`, `openclaw`, `extensions`, and `skills`
  directories;
- paths outside the role-specific allowed workspace root;
- files used where a directory is required;
- Docker mount delimiter/control characters.

Mission Agent defaults to:

```text
<runtime-root>/data/mission/agent-workspace
```

Robot Agent defaults to:

```text
<runtime-root>/data/robot/agent-workspace
```

Each role's allowed root is that dedicated workspace itself. A configured
workspace may be that directory or a descendant, but not the adjacent task
database, plugin directory, repository, or another robot's workspace.

## Symlink And Docker Handling

Existing symlinks are resolved before containment checks. A symlink below an
allowed root that points outside it is rejected, including when the final
workspace leaf does not exist yet.

`ComputerSandbox` validates again after creating the workspace and before every
Docker bind. If the path is replaced or retargeted after deployment validation,
execution stops instead of mounting the new target.

The Docker command receives the canonical path:

```text
--mount type=bind,src=<canonical-agent-workspace>,dst=/workspace,rw
```

Computer Tool file paths remain workspace-relative and are independently
checked after real-path resolution.

## Configuration

```toml
[runtime]
root_dir = "/var/lib/fireclaw/robot-1"

[deployment.sandbox.robot_agent]
enabled = true
workspace_root = "data/robot/agent-workspace"
image = "fireclaw-agent-sim:local"
```

The Robot Gateway startup JSON and Robot `/state` response report the resolved
runtime directory and workspace policy. Mission Gateway prints its resolved
runtime root during startup.

## Embedding API

The CLI owns process-wide `chdir` and restores it when the server exits. Code
that embeds `start_server()` or constructs `GatewayConfig` directly should
pass absolute paths; an embedding host may have its own working-directory
lifecycle.
