# P0 Legacy Executable Tool Sandbox

## 2026-07-30 19:24 +08

### Task Goal

Close the P0 path where legacy `skills/**/*.skill.json` manifests could declare
arbitrary host argv, execute it through `subprocess.Popen`, inherit the
FireClaw environment, bypass `ComputerSandbox`, and still report
`dry_run=True`.

### OpenClaw Analogue Inspected

- `openclaw/src/agents/embedded-agent-runner/sandbox-skills.ts`
  - useful shape: materialize Skill content into a sandbox-visible workspace;
- `openclaw/src/agents/bash-tools.exec-run.ts`
  - useful shape: process execution is a policy-projected Tool path, not an
    executable capability implicitly granted to Skill content.

FireClaw adaptation: legacy executable manifests are hard-blocked for real
robot deployments and can exist only as simulation process Tools.

### Files Inspected

- `src/fireclaw_core/infra/workspace_skills.py`
- `src/fireclaw_core/infra/skill_manifest.py`
- `src/fireclaw_core/execution/runtime.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/agent/computer_tools.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/agent_cli.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/devtools/doctor.py`
- related Agent, Gateway, CLI, runtime, manifest, and policy tests

### Implementation

1. Removed all host process creation from `SubprocessSkillRunner`.
   - It now requires the trusted `SandboxedSkillExecutor` protocol.
   - Production execution mode is recorded as `sandboxed_subprocess`.
   - JSON stdin/stdout, timeout, cancellation, exit, and malformed response
     handling remain explicit.

2. Extended `ComputerSandbox` as the production executor.
   - `execute_process()` appends manifest argv only after the fixed reviewed
     Docker invocation.
   - Docker still uses `--pull=never`, no shell, `cap-drop ALL`,
     `no-new-privileges`, non-root identity, read-only root, bounded tmpfs,
     resource/timeout/output limits, and one `/workspace` mount.
   - No FireClaw environment variables are forwarded into the container.

3. Added bounded Skill materialization.
   - Source files are copied below
     `.fireclaw/legacy-skills/<name>-<digest>/`.
   - Limits: 256 regular files, 2 MiB per file, 16 MiB total.
   - Source/nested symlinks, special files, sandbox-inside-source recursion,
     and invalid staging destinations are rejected.
   - Digest includes relative path, mode, and file bytes.

4. Added registration-time deployment policy.
   - Every legacy manifest is classified as `effect=process` and
     `requires_sandbox=true`.
   - It must receive an explicit `DeploymentProfile`, an `allow` decision, and
     a trusted sandbox executor.
   - `real`, disabled sandbox, missing image, allowlist miss, explicit deny,
     and `group:computer` deny all block registration.
   - The policy decision is attached to Tool metadata for audit.

5. Hardened manifest authority.
   - `dry_run_only` must be true.
   - `allow_real_robot` must be false.
   - `{python}` resolves to container `python3`, never host `sys.executable`.
   - Commands remain argv arrays; shell strings are rejected.

6. Separated inspection from execution.
   - `doctor` uses `inspect_only=True`.
   - Inspection parses metadata without staging, registration, sandbox
     creation, or execution.

7. Integrated composition boundaries.
   - Gateway creates one `ComputerSandbox` and reuses it for computer Tools and
     legacy manifest Tools.
   - A trusted `workspace_skill_executor` injection seam exists for tests; it
     is not exposed through manifests, LLM calls, Gateway requests, or config.
   - The standalone Agent CLI requires explicit
     `--legacy-skill-sandbox-image` and sandbox root options. Without them,
     executable manifests fail closed.

### Files Modified For This P0

- `src/fireclaw_core/execution/runtime.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/infra/skill_manifest.py`
- `src/fireclaw_core/infra/workspace_skills.py`
- `src/fireclaw_core/agent/computer_tools.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/agent_cli.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/devtools/doctor.py`
- `tests/conftest.py`
- `tests/test_runtime.py`
- `tests/test_skill_manifest.py`
- `tests/test_workspace_skills.py`
- `tests/test_workspace_skill_example.py`
- `tests/test_skill_metadata.py`
- `tests/test_agent_tool_runtime.py`
- `tests/test_agent.py`
- `tests/test_gateway.py`
- `tests/test_subagent_client.py`
- `tests/test_mission_cli.py`
- `tests/test_cli.py`
- `README.md`
- `skills/README.md`
- `docs/architecture/legacy-executable-tool-sandbox.md`
- `docs/architecture/plugin-host-agent-harness.md`
- `docs/architecture/fireclaw-openclaw-alignment.md`
- `docs/architecture/plugin-skill-tool-terminology.md`
- `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`

### Commands And Results

- `python3 -m compileall -q src/fireclaw_core tests`
  - passed.
- `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q` for runtime,
  manifest, workspace loader, example, metadata, computer Tool, and Agent tests
  - `96 passed`.
- Gateway, subagent client, mission CLI, and doctor tests require local
  loopback sockets and were run outside the filesystem/network sandbox
  - `99 passed`.
- standalone CLI tests
  - `21 passed`.

The first Gateway run inside the restricted sandbox failed only because local
socket creation was denied. The same tests passed outside that sandbox.

### Current Conclusion

Disabling `computer_exec` no longer leaves a second manifest-controlled host
process path. Legacy executable Tool manifests are now one policy domain with
computer process Tools and have no production host fallback.

### Remaining Work

- Consider a future Docker process supervisor if cancellation must interrupt a
  running container immediately instead of relying on the bounded process
  timeout plus stopping subsequent Tool dispatch.
- Migrate users away from `*.skill.json` toward canonical Plugin Tool +
  trusted Adapter packages; keep the current loader compatibility-only.

## 2026-07-30 19:29 +08

Full repository regression completed outside the restricted socket sandbox:

```text
1904 passed, 7 skipped in 160.90s
```

The seven skipped tests are the existing opt-in ROS1 smoke tests.
