# Security audit and Agent Tool approval resumption

## Task goal

Implement only the two requested remaining security items:

1. an OpenClaw-like whole-deployment security audit;
2. closure of the generic Agent Tool approval chain for Robot Agent,
   including passback of approved authorization and one-time consumption.

## Timestamp

- 2026-07-30 22:15 Asia/Ulaanbaatar

## OpenClaw analogues inspected

- `openclaw/src/security/audit.ts`
- `openclaw/src/security/audit.types.ts`
- `openclaw/src/agents/sandbox/validate-sandbox-security.ts`
- `openclaw/docs/gateway/security/audit-checks.md`
- `openclaw/docs/gateway/security/index.md`

Reused structure:

- independent audit orchestrator;
- stable check IDs and severities;
- structured summary and remediation;
- CLI and doctor consume the same report.

FireClaw adaptation:

- Mission and Robot Gateway transport are checked separately;
- robot-local runtime roots, ROS-adjacent legacy manifests, exact Tool
  approval, and physical-state replay risks are included;
- audit is read-only and never imports plugins, invokes Tools, or probes ROS.

## Files added

- `src/fireclaw_core/security/__init__.py`
- `src/fireclaw_core/security/audit.py`
- `tests/test_security_audit.py`
- `tests/test_gateway_agent_tool_approval.py`
- `docs/architecture/security-audit.md`
- `docs/architecture/agent-tool-approval-resumption.md`

## Files modified

- `src/fireclaw_core/__main__.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/devtools/doctor.py`
- `src/fireclaw_core/agent/tool_runtime.py`
- `src/fireclaw_core/gateway/control.py`
- `src/fireclaw_core/gateway/gateway.py`
- `tests/test_agent_tool_runtime.py`
- `tests/test_doctor.py`
- `README.md`

## Security audit result

`fireclaw security-audit --config ...` now returns:

- stable `check_id`;
- `critical`, `warn`, or `info`;
- evidence and remediation;
- aggregate summary and CI-oriented exit policy.

Initial checks cover Gateway authentication/TLS/Host policy, network limits,
runtime and sandbox path safety, image pinning, Tool selector drift, Docker
network exposure, state/config/key permissions, legacy executable manifests,
and plugin descriptor provenance.

`doctor --security-config ...` embeds the same report. It does not repair
security-sensitive settings automatically.

## Agent Tool approval result

Previous behavior:

- Robot Agent could return `approval_required`;
- the task safely stopped;
- `/confirm` could issue an authorization, but the Robot Agent Tool closure
  never received it;
- no generic Agent Tool consumption occurred.

Current behavior:

- the exact host-produced Tool approval request is persisted with
  `authorization_kind=agent_tool`;
- operator approval issues and persists the existing signed
  `ExecutionAuthorization`;
- the same structured task is submitted again with the grant;
- Gateway verifies the embedded signed grant;
- `AgentToolRuntime` rebinds it to the current plugin contract, final
  hook-adjusted arguments, deployment profile, and task context;
- the existing SQLite `authorization_uses` table atomically consumes the
  authorization before the handler;
- replay fails closed with `agent_tool_authorization_already_used`.

Crash semantics are intentionally at-most-once: consumption before the side
effect prevents automatic replay when the outcome is uncertain.

## Commands executed

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q \
  src/fireclaw_core/security \
  src/fireclaw_core/agent/tool_runtime.py \
  src/fireclaw_core/gateway/control.py \
  src/fireclaw_core/gateway/gateway.py \
  src/fireclaw_core/mission/mission_cli.py \
  src/fireclaw_core/devtools/doctor.py

/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_security_audit.py \
  tests/test_agent_tool_runtime.py \
  tests/test_gateway_agent_tool_approval.py \
  tests/test_gateway_execution_authorization.py \
  tests/test_authoritative_runtime_state.py \
  tests/test_doctor.py
```

Observed result at the time of this record: all focused tests passed. Broader
non-network tests passed. Network tests require running outside the filesystem
sandbox because local socket creation is denied there.

Final full regression was run outside the socket-restricted sandbox:

```text
1963 passed, 7 skipped in 169.75s
```

## Research assessment

This is engineering correctness and deployment-safety infrastructure, not a
standalone research contribution. It strengthens experimental validity by
making security configurations auditable and by preventing an evaluation
artifact where an approved Tool can be replayed indefinitely. A publication
claim would still require a formal threat model, attack/fault injection,
measured false-positive/false-negative audit coverage, and comparison against
embodied-agent safety baselines.

## Next step

Run the full suite outside the socket-restricted sandbox, inspect the final
diff for unintended changes, and report any residual security limitations.
