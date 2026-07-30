# P0 Gateway Secure Transport

## 2026-07-30 20:22:57 +08

### Task goal

Close the third security P0: Mission Gateway and Robot Gateway previously used
plain `ThreadingHTTPServer`, returned `http://` URLs, and allowed Robot clients
to send task payloads and bearer tokens over plaintext network links.

The user requested an implementation that follows OpenClaw's architecture
rather than introducing an unrelated FireClaw-only pattern.

### OpenClaw analogue inspected

- `openclaw/src/infra/tls/gateway.ts`
  - `loadGatewayTlsRuntime`
  - explicit TLS material loading;
  - minimum TLS version;
  - startup failure instead of silent plaintext fallback.
- `openclaw/src/gateway/server/tls.ts`
  - keeps TLS setup behind a transport boundary.
- `openclaw/src/gateway/server-http.ts`
  - selects HTTP or HTTPS at server construction without coupling endpoint
    handlers to TLS.

Reused structure:

- one transport module owns server/client TLS policy;
- Gateway handlers remain transport-independent;
- TLS errors fail closed;
- clients verify peer identity instead of using an insecure skip-verify mode.

FireClaw adaptation:

- remote plaintext is prohibited, while loopback HTTP remains for local tests;
- deployment-managed certificates are required instead of auto-generating an
  unverifiable multi-robot identity;
- optional mTLS verifies the central client at each Robot Gateway and can also
  protect Mission Gateway;
- bearer authentication remains the application principal and scope source.

### Implemented behavior

1. Added `src/fireclaw_core/gateway/transport.py` with:
   - `GatewayTlsServerConfig`;
   - `GatewayTlsClientConfig`;
   - `GatewayHttpTransport`;
   - verified TLS 1.3 server/client contexts;
   - remote plaintext URL rejection before network I/O;
   - optional client-certificate enforcement;
   - no fallback after certificate/key/context failure.
2. Both `FireClawGateway` and `MissionGateway` construct their listeners through
   `create_gateway_http_server` and report `https://` when TLS is enabled.
3. `RobotSubagentClient` and `MissionGatewayClient` share the same outbound
   transport. CA chain and hostname checks cannot be disabled.
4. Mission server assembly now passes one configured `RobotSubagentClient` to
   both `MissionGateway` and its internal `MissionAgent`. This prevents memory
   sync from using the TLS client while actual task dispatch accidentally uses
   an unconfigured client.
5. Added TOML and CLI configuration for:
   - Mission Gateway server certificate/key/CA/client-cert requirement;
   - Robot Gateway server certificate/key/CA/client-cert requirement;
   - Mission-to-Robot CA and client certificate/key;
   - interactive Mission Gateway client CA and client certificate/key.
6. A non-loopback listener now requires both application authentication and
   TLS. A client rejects `http://192.0.2.10/...` before its Authorization
   header can leave the process.

### Files introduced

- `src/fireclaw_core/gateway/transport.py`
- `tests/test_gateway_transport.py`
- `docs/architecture/gateway-secure-transport.md`
- this memory record

### Main files modified

- `src/fireclaw_core/gateway/auth.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_gateway_client.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/mission/interactive.py`
- `src/fireclaw_core/subagent/subagent_client.py`
- `fireclaw.example.toml`
- `README.md`
- Gateway authentication/alignment documentation
- focused Gateway, CLI, replication, and server assembly tests

### Tests completed

- `tests/test_gateway_transport.py`: 10 tests covering:
  - no network call for remote plaintext;
  - non-loopback server TLS requirement;
  - missing server key failure;
  - partial client mTLS failure;
  - shared HTTPS use by Mission and Robot clients;
  - actual Robot Gateway HTTPS listener;
  - actual Mission Gateway HTTPS listener;
  - unknown CA rejection;
  - hostname mismatch rejection;
  - mTLS rejection/acceptance.
- `tests/test_replication_gateway.py tests/test_gateway_transport.py`:
  `18 passed`.
- `tests/test_serve.py tests/test_gateway_robot_agent_cli.py
  tests/test_mission_cli.py`: `58 passed`.
- broader Gateway/client/replication/end-to-end set initially found two mocked
  HTTPS calls whose test side effect did not accept urllib's `context`
  argument. The test adapter was corrected; production behavior was not
  weakened.

### Current conclusion

The plaintext-network P0 is closed for the two production Gateway servers and
their typed clients. FireClaw now has direct HTTPS/mTLS rather than relying on
LAN isolation. This does not yet implement certificate enrollment, rotation,
revocation, trusted reverse-proxy identity, rate limiting, firewall policy, or
per-human certificate-to-role mapping.

### Next recommended step

Continue the remaining security review in priority order. Do not commit until
the user asks.

## 2026-07-30 20:29:00 +08

### Final verification

Command:

```bash
timeout 420s /home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
```

Result:

```text
1915 passed, 7 skipped in 164.64s
```

`git diff --check` and Python `compileall` also completed successfully.
