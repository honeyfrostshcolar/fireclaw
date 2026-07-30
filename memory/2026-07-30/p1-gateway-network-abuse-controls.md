# P1 Gateway network abuse controls

## 2026-07-30 18:40 Asia/Ulaanbaatar

### Task goal

Close the reviewed P1 gap where Robot/Mission Gateway listeners could be
exhausted through oversized bodies, slow or excessive connections,
authentication attempts, unbounded SSE subscriber queues, and unvalidated
Host/Origin headers.

### OpenClaw analogues inspected

- `openclaw/src/infra/http-body.ts`
- `openclaw/src/gateway/server/preauth-connection-budget.ts`
- `openclaw/src/gateway/auth-rate-limit.ts`
- `openclaw/src/gateway/http-auth-utils.ts`
- `openclaw/src/gateway/origin-check.ts`

Reused structure: central admission primitives, pre-auth connection budget,
bounded failed-auth state, and Host/Origin checks at the auth boundary.
FireClaw adaptation: Python `ThreadingHTTPServer`, two Gateway roles, robot
loopback aliases, SSE stream budgets, and fail-safe wildcard-bind validation.

### Files added

- `src/fireclaw_core/gateway/network_security.py`
- `tests/test_gateway_network_security.py`
- `docs/architecture/gateway-network-abuse-controls.md`

### Files modified for this task

- `src/fireclaw_core/gateway/transport.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `tests/test_mission_gateway.py`
- `fireclaw.example.toml`
- `README.md`
- `docs/architecture/fireclaw-openclaw-alignment.md`
- `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`

### Implemented behavior

- Shared immutable `GatewayNetworkPolicy` for Mission and Robot listeners.
- Unknown, ill-typed, non-finite, non-positive, or inconsistent security
  settings fail startup.
- `BoundedThreadingHTTPServer` acquires total/per-client connection capacity
  before spawning a handler thread and releases it at thread termination.
- Accepted sockets get a request-header deadline.
- Exactly one Host is required. Wildcard listeners require explicit
  `allowed_hosts`; loopback listeners accept only equivalent loopback aliases.
- Optional Origin must be same-origin or explicitly allowlisted.
- Transfer-Encoding and duplicate/malformed Content-Length are rejected.
- Declared bodies over 1 MiB return `413` before body read/allocation.
- Body read timeout returns `408`; partial, invalid UTF-8/JSON, and non-object
  JSON return `400`.
- Failed authentication is rate-limited per socket peer with a bounded
  in-memory client map; lockout returns `429` and `Retry-After`.
- SSE has total/per-client connection budgets, bounded per-subscriber queues,
  nonblocking publication, write deadlines, and overflow disconnect.
- Authenticated robot `/state` and mission `/fleet/doctor` expose the policy
  and live SSE/auth tracker snapshot.

### Commands and results

- Direct `conda run -n py310 ...` failed because old Conda tried to create a
  temp file inside read-only
  `/home/lpp/miniconda3/envs/py310`; switched to the environment interpreter
  directly.
- `/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q ...`
  - passed.
- `pytest -q tests/test_gateway_network_security.py`
  - sandbox run: 8 pure tests passed, 3 socket tests failed with
    `PermissionError` as expected from the filesystem/network sandbox.
  - approved loopback run: `11 passed`.
- Initial Gateway regression:
  - `175 passed, 4 failed`.
  - two failures were old `400`/message expectations for the intentional
    `413` early rejection.
  - two failures showed TLS clients using `localhost` against a
    `127.0.0.1` listener; fixed with loopback-only alias equivalence.
- Focused correction run: `15 passed`.
- Added Mission auth rate-limit and Robot SSE limit coverage:
  - focused run: `14 passed`.
- Final Gateway-related regression:
  - `181 passed in 82.20s`.

### Security conclusions

- The original unbounded request-body read and unbounded SSE queues are closed.
- Thread creation is bounded before request authentication and parsing.
- Slow header/body/SSE phases all have separate deadlines.
- FireClaw does not trust `X-Forwarded-For`; behind a proxy, per-client limits
  see the proxy address. The proxy must enforce its own trusted-client and
  distributed limits.
- Limits remain process-local and reset on restart. They are defense in depth,
  not a replacement for firewall, reverse proxy, TLS/mTLS, monitoring, or
  deployment capacity tests.

### Current conclusion

P1 item 6 is implemented for both Gateway roles and the focused plus
Gateway-wide regression suite passes.

## 2026-07-30 19:05 Asia/Ulaanbaatar

### Final verification update

- Added request-header timeout to the accepted socket before HTTP parsing.
- Added SSE per-client <= SSE total validation.
- Added connection-budget release when handler-thread creation itself fails.
- Added shared `[network]` TOML loader coverage.
- Final focused run: `15 passed in 3.71s`.
- Final full repository run:
  `1956 passed, 7 skipped in 168.82s`.
- `git diff --check` passed for all task-touched paths.
- Ruff/Black are not installed in the `py310` environment; syntax was instead
  verified with `compileall`, and the full test suite passed.

### Final conclusion

P1 item 6 is complete. Both Gateway roles use the shared policy and the final
repository state passes all available tests. No commit was requested or
created.

### Next recommended step

Proceed to the next reviewed P1 security item. For production deployment,
capacity-test the configured limits behind the actual reverse proxy and
network topology.
