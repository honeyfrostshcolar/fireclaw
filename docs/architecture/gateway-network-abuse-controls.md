# Gateway Network Abuse Controls

## Scope

`MissionGateway` and robot-local `FireClawGateway` share one network-admission
policy. The policy protects the Python control plane before a request reaches
mission planning, robot task execution, memory, approval, or ROS adapters.

This implementation follows OpenClaw's separation of concerns:

- `openclaw/src/infra/http-body.ts`: reject oversized bodies while streaming
  under a deadline;
- `openclaw/src/gateway/server/preauth-connection-budget.ts`: bound
  unauthenticated connections before expensive work;
- `openclaw/src/gateway/auth-rate-limit.ts`: bounded per-client failed-auth
  tracking and lockout;
- `openclaw/src/gateway/origin-check.ts` and
  `openclaw/src/gateway/http-auth-utils.ts`: make Host/Origin admission part of
  the authentication boundary.

FireClaw adapts those boundaries to `ThreadingHTTPServer`, long-lived SSE
telemetry, robot-local deployment, and strict fail-safe startup.

## Admission Order

Every supported HTTP request follows this order:

1. The server accepts the socket only if total and per-client connection
   budgets are available. Rejected sockets do not spawn handler threads.
2. A socket deadline bounds request-header parsing.
3. Host, Origin, Transfer-Encoding, duplicate Content-Length, method/body
   compatibility, and declared body size are checked.
4. Failed-authentication lockout is checked before token verification.
5. Method scopes are checked against the server-owned authenticated principal.
6. Only then may the Gateway parse a bounded JSON object and dispatch business
   logic.

The two Gateway implementations use the same `GatewayNetworkPolicy`,
`GatewayRequestGuard`, `GatewayAuthRateLimiter`, and
`BoundedThreadingHTTPServer`.

## Request Bodies

The default body limit is 1 MiB. A larger declared `Content-Length` returns
HTTP `413` immediately, without reading or allocating the declared body.
Unsupported `Transfer-Encoding`, duplicate or malformed Content-Length values,
partial bodies, invalid UTF-8/JSON, and non-object JSON are rejected.

Body reads have an independent deadline. A timeout returns HTTP `408`; partial
or malformed bodies return HTTP `400`. Rejected framing closes the connection
so unread bytes cannot be interpreted as another request.

## Host And Origin

Exactly one valid Host header is required. It must match the configured
listener, the actual listener address, or `network.allowed_hosts`. A wildcard
bind such as `0.0.0.0` is rejected at startup unless `allowed_hosts` is
explicitly configured. Loopback aliases `localhost`, `127.0.0.1`, and `::1`
are equivalent only for a loopback listener.

Origin is optional for non-browser clients. When present, it must be exact
same-origin or appear in `network.allowed_origins`; malformed, `null`, multiple,
or cross-origin values are rejected. This blocks browser-origin confusion but
does not replace bearer authentication or CSRF-safe frontend design.

## Authentication Rate Limits

Only failed authentication attempts consume the per-client budget. Defaults
are 10 failures in 60 seconds followed by a 300-second lockout. Locked clients
receive HTTP `429` and `Retry-After`. Successful authenticated requests clear
that client's failures.

The tracker has a fixed maximum number of client entries. Loopback is exempt
by default for development and can be included with
`auth_exempt_loopback = false`. The public robot health endpoint neither adds
nor clears failed-auth state.

## SSE Limits

SSE has its own total and per-client connection budgets. Each subscriber has a
fixed-size queue. Event publication never blocks on a slow subscriber: queue
overflow terminates that stream. Socket writes have a deadline, recent replay
is bounded by the EventBus ring, and mission history replay remains capped at
200 records.

Authenticated robot `/state` and mission `/fleet/doctor` responses report the
configured policy, tracked authentication clients, and current SSE usage.

## Configuration

```toml
[network]
max_body_bytes = 1048576
request_header_timeout_seconds = 10
body_read_timeout_seconds = 10
max_connections = 128
max_connections_per_ip = 32
auth_max_failures = 10
auth_window_seconds = 60
auth_lockout_seconds = 300
auth_exempt_loopback = true
auth_max_tracked_clients = 10000
max_sse_connections = 16
max_sse_connections_per_ip = 4
sse_queue_size = 256
sse_write_timeout_seconds = 10
# allowed_hosts = ["mission.example.internal"]
# allowed_origins = ["https://operator-console.example.internal"]
```

Unknown or ill-typed settings fail startup. Per-client limits cannot exceed
their corresponding total limits, and SSE limits cannot exceed general
connection limits.

## Deployment Boundary

These controls are process-local defense in depth, not an Internet edge.
Counters reset after a process restart and are not shared across replicas.
FireClaw deliberately uses the socket peer address and does not trust
`X-Forwarded-For`; behind a proxy, the proxy must enforce trusted-client IP,
connection, request-size, timeout, and rate-limit policies itself.

Production still requires a firewall or reviewed reverse proxy, TLS/mTLS,
secret rotation, monitoring, and deployment-specific capacity testing.

## Verification

Focused tests are in `tests/test_gateway_network_security.py`, with Mission
Gateway compatibility and rate-limit coverage in
`tests/test_mission_gateway.py`. They cover early `413` rejection without a
body upload, Host/Origin decisions, body framing, authentication lockout,
connection budgets, concurrent SSE limits, typed configuration, and local TLS
hostname compatibility.
