# Gateway Authentication

## Scope

This boundary authenticates HTTP callers before Mission Gateway or Robot
Gateway endpoint scopes, operator control policy, approval policy, or task
execution are evaluated. It closes the former path where a caller could grant
itself authority with `X-Operator-Scopes`, `X-Operator-Id`, or an `operator`
object in JSON.

It does not replace network policy, browser-origin validation, authentication
rate limiting, per-human identity, or robot enrollment. TLS and optional mTLS
are enforced by the separate Gateway transport boundary documented in
[`gateway-secure-transport.md`](gateway-secure-transport.md).

## OpenClaw Analogue

The implementation follows the relevant OpenClaw shape:

- `openclaw/src/gateway/http-auth-utils.ts`
  `checkGatewayHttpRequestAuth` authenticates before resolving endpoint scopes
  and does not trust declared operator scopes for shared-secret Bearer auth.
- `openclaw/src/gateway/auth-resolve.ts` resolves configured and environment
  credentials into a server-owned authentication mode.
- `openclaw/src/gateway/auth.ts` verifies shared secrets with constant-time
  comparison and validates startup authentication configuration.

FireClaw adapts that shape to two HTTP control planes and the current
single-operator robotics deployment. It uses one shared Python boundary in
`fireclaw_core.gateway.auth`; physical authorization remains in the existing
control, approval, capability, and safety policies.

## Trust Model

| Request path | Resulting principal | Authority source |
| --- | --- | --- |
| `GET /health` | `anonymous-health` | Server-owned read-only health policy |
| Tokenless request from loopback | `local-loopback-operator` | Local OS/process boundary |
| Request with valid configured Bearer token | `gateway-shared-token` | Possession of the server secret |
| Tokenless request from a non-loopback address | Rejected | None |
| Missing, malformed, or wrong token when configured | Rejected | None |

Both authenticated principals currently receive the server-owned `admin`
role. This is deliberate for the current shared-operator deployment: a shared
secret cannot prove which individual human supplied it. A future
trusted-proxy, device-certificate, or per-operator credential mode must create
distinct principals and server-owned role mappings.

The following values are untrusted metadata and never grant or remove
authority:

- `X-Operator-Id`
- `X-Operator-Scopes`
- JSON `operator.operator_id`
- JSON `operator.role`
- JSON `operator.control_scopes`

Clients retain some old constructor parameters for source compatibility, but
they no longer transmit those identity or scope declarations. Robot task and
cancel requests likewise do not forward a caller-declared operator object.

## Request Flow

```text
HTTP request
  -> authenticate Bearer credential or verify loopback source
  -> derive AuthenticatedGatewayPrincipal on the server
  -> check endpoint method scope from that principal
  -> derive OperatorContext from the same principal
  -> run control / approval / capability / safety policy
  -> execute or reject
  -> persist the authenticated principal in audit records
```

The request body is parsed only after the authentication and endpoint-scope
checks. Mission approval `requested_by` and `decided_by`, Robot Gateway
`operator.identified`, and control decisions now use the authenticated
principal.

## Configuration

Mission Gateway inbound authentication:

```toml
[server]
host = "127.0.0.1"
api_token = "replace-with-a-random-secret"
```

Prefer the environment instead of plaintext TOML:

```bash
export FIRECLAW_GATEWAY_TOKEN='replace-with-a-random-secret'
```

Robot Gateway inbound authentication and the Mission Agent credential used to
call it:

```toml
[robot_gateway]
host = "127.0.0.1"
api_token = "replace-with-a-random-secret"

[mission]
robot_gateway_api_token = "replace-with-a-random-secret"
```

The preferred outbound environment variable is:

```bash
export FIRECLAW_ROBOT_GATEWAY_TOKEN='replace-with-a-random-secret'
```

Changing either Gateway bind host to `0.0.0.0`, a robot LAN address, or a
hostname without both a token and TLS raises an error before the server starts.

## Current Boundary

Loopback without a token remains an explicit compatibility mode and treats the
local OS account as the identity boundary. Configure a token even on loopback
for real robots. A shared token is an all-admin deployment credential, not a
per-person approval signature.

Remaining network-security work includes browser Origin/Host enforcement,
authentication failure rate limiting, trusted-proxy identity, and secret
references/rotation.
