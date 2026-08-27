# FireClaw Deployment Security Audit

FireClaw provides a read-only deployment security audit:

```bash
fireclaw security-audit \
  --config /etc/fireclaw/fireclaw.real.toml \
  --plugin-dir /etc/fireclaw/plugins
```

The report follows the same stable boundary used by OpenClaw's security
audit:

- every finding has a stable `check_id`;
- severity is `critical`, `warn`, or `info`;
- findings contain evidence and an actionable remediation;
- one summary reports the number of findings at each severity;
- the auditor is independent from `doctor`, while `doctor` can consume the
  same report through `--security-config`.

The implementation was adapted from the structure of
`openclaw/src/security/audit.ts`,
`openclaw/src/security/audit.types.ts`, and
`openclaw/docs/gateway/security/audit-checks.md`. FireClaw checks different
deployment risks because its Gateways can authorize physical robot work.

## Current Checks

The initial audit covers:

- non-loopback Mission and Robot Gateway listeners without authentication;
- non-loopback listeners without TLS and remote listeners without mTLS;
- wildcard listeners without `network.allowed_hosts`;
- invalid Gateway request, connection, authentication, and SSE limits;
- missing TLS identity files and broadly accessible private keys;
- unsafe runtime roots and invalid sandbox workspace roots;
- invalid or unpinned Docker sandbox profiles;
- broad Tool selectors and simulation computer Tools without a ready sandbox;
- Docker bridge networking for exposed computer Tools;
- group/other-writable config, state, and Plugin descriptor files;
- executable artifacts placed inside descriptor-only Plugin directories;
- symbolic-link plugin descriptors and missing plugin install provenance.

The audit never starts a Gateway, imports executable plugins, invokes a Tool,
or probes ROS. It inspects only configuration and filesystem metadata.

## Exit Policy

By default the CLI exits with code `2` only when critical findings exist.
Use `--fail-on warn` for stricter CI enforcement or `--fail-on never` when
collecting a report without gating a deployment.

`doctor` remains a readiness and repair tool. Run:

```bash
python -m fireclaw_core.devtools.doctor \
  --security-config /etc/fireclaw/fireclaw.real.toml
```

Its `security_audit` check embeds the full audit report, but `doctor --fix`
does not automatically change security-sensitive configuration or file
permissions.
