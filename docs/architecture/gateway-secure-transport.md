# Gateway Secure Transport

## Boundary

Mission Gateway and Robot Gateway use the shared
`fireclaw_core.gateway.transport` boundary. It protects operator commands,
robot tasks, bearer tokens, approvals, traces, and memory replication while
they cross a network.

The enforced rules are:

- a Gateway may use plaintext HTTP only on a loopback bind;
- clients reject remote `http://` URLs before sending a request;
- HTTPS always validates the certificate chain and hostname;
- private deployment CAs can be configured explicitly;
- clients and servers can require mutually authenticated TLS;
- TLS setup errors abort startup and never fall back to HTTP;
- TLS 1.3 is the minimum supported protocol version.

Bearer authentication remains required for a non-loopback listener. TLS proves
the encrypted peer/channel; the bearer principal supplies current
application-level scopes. When mTLS is enabled, both checks must pass.

## OpenClaw Analogue

The module follows the split used by:

- `openclaw/src/infra/tls/gateway.ts` for loading TLS material and failing
  closed;
- `openclaw/src/gateway/server/tls.ts` for the transport boundary;
- `openclaw/src/gateway/server-http.ts` for selecting HTTPS rather than
  changing endpoint handlers.

FireClaw requires deployment-provided certificates instead of silently
generating a self-signed identity. A multi-robot deployment needs stable,
operator-managed trust roots and revocable robot/client certificates. FireClaw
also adds optional client-certificate verification because central-to-robot
control is a physical safety boundary.

## Configuration

```toml
[server]
host = "0.0.0.0"
api_token = "use-an-environment-secret-in-production"

[server.tls]
enabled = true
cert_file = "certs/mission-gateway.crt"
key_file = "certs/mission-gateway.key"
ca_file = "certs/fireclaw-ca.crt"
require_client_cert = true

[mission.robot_gateway_tls]
ca_file = "certs/fireclaw-ca.crt"
cert_file = "certs/mission-gateway-client.crt"
key_file = "certs/mission-gateway-client.key"

[robot_gateway]
host = "0.0.0.0"
api_token = "use-a-distinct-environment-secret"

[robot_gateway.tls]
enabled = true
cert_file = "certs/robot-1.crt"
key_file = "certs/robot-1.key"
ca_file = "certs/fireclaw-ca.crt"
require_client_cert = true
```

The robot registry must use an HTTPS endpoint whose hostname appears in the
robot certificate:

```json
{"robot_id": "robot-1", "base_url": "https://robot-1.example:8765"}
```

Loopback HTTP remains available for unit tests and same-host development. It
is not a deployment transport for central-to-robot traffic.

## Non-Goals

This change does not implement certificate enrollment, rotation, revocation,
trusted reverse-proxy headers, network firewall policy, or per-human identity.
Those remain separate control-plane concerns.
