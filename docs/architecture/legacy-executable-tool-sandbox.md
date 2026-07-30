# Legacy Executable Tool Sandbox

## Status

`*.skill.json` is a compatibility format for executable Tools. It is not an
OpenClaw-style Skill package and it grants no host command authority.

This boundary follows the relevant OpenClaw structure:

- `openclaw/src/agents/embedded-agent-runner/sandbox-skills.ts` materializes
  Skill content into the sandbox-visible workspace;
- `openclaw/src/agents/bash-tools.exec-run.ts` routes process execution through
  a policy-checked execution Tool instead of treating Skill content as an
  executable capability.

FireClaw adapts that model for robotics by hard-blocking this legacy process
format in real deployments.

## Registration Pipeline

```text
discover *.skill.json
-> parse and validate without execution
-> classify as effect=process, requires_sandbox=true
-> evaluate DeploymentProfile allow/deny, sandbox, and effect stages
-> reject unless the result is allow
-> stage bounded regular files into ComputerSandbox
-> register the Tool contribution with its policy decision
```

The loader fails closed when any of the following is true:

- no explicit deployment profile was provided;
- deployment mode is `real`;
- the process Tool is outside the allowlist or matches a deny selector;
- the Docker sandbox is disabled or has no image;
- no trusted sandbox executor was injected;
- `dry_run_only` is false or `allow_real_robot` is true.

`group:computer` selectors include these process Tools. Disabling
`computer_exec` by effect therefore also disables the legacy manifest route.

## File Materialization

The trusted host copies the manifest directory to:

```text
<sandbox-root>/.fireclaw/legacy-skills/<safe-name>-<content-digest>/
```

Staging accepts only regular files and enforces file-count, per-file, and
total-size bounds. It rejects source or nested symlinks, special files, and a
source tree that contains the sandbox workspace. The content digest includes
relative paths, modes, and bytes.

The container sees the configured sandbox root only at `/workspace`. It does
not receive arbitrary host paths, the FireClaw process environment, ROS
sockets, devices, or Docker control sockets.

## Process Execution

`SubprocessSkillRunner` serializes Tool arguments as JSON and calls the
`SandboxedSkillExecutor` protocol. Production uses `ComputerSandbox`, whose
trusted host code assembles a fixed `docker run` command with:

- no shell;
- `--pull=never`;
- `--cap-drop ALL` and `no-new-privileges`;
- PID, memory, CPU, timeout, and output bounds;
- read-only container root plus a bounded `/tmp`;
- non-root UID/GID;
- only the sandbox workspace mounted at `/workspace`;
- network `none` by default.

The legacy manifest controls only the argv appended after the reviewed image.
`{python}` maps to container `python3`; host interpreter paths are not
substituted. Output metadata uses `runtime=sandboxed_subprocess`.

## Inspection

`fireclaw doctor` uses `inspect_only=True`. It validates manifest structure and
reports names for ROS remap diagnostics, but it does not stage files, register
Tools, create a process sandbox, or execute a command.

## Trusted Injection Boundary

`SandboxedSkillExecutor` can be injected only by trusted Python composition
code. It is not constructible from a manifest, LLM Tool call, Gateway request,
or FireClaw configuration object. Production Gateway composition reuses the
same `ComputerSandbox` instance for computer Tools and legacy manifest Tools.
Tests inject a host-backed fake explicitly; that fake is defined under
`tests/` and is not shipped as a runtime fallback.
