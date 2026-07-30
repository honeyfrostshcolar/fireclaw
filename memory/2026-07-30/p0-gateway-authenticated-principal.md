# P0 Gateway Authenticated Principal

## 2026-07-30 18:49:59 +08

### Task goal

Resolve the first security P0 from the FireClaw/OpenClaw comparison: Mission
Gateway and Robot Gateway must not authorize a request from caller-controlled
`X-Operator-Id`, `X-Operator-Scopes`, or JSON `operator` values. Establish one
server-derived principal and use it consistently for endpoint authorization,
control policy, approvals, task execution, and audit attribution.

The user requested that P0 items be handled one at a time and that FireClaw
reuse OpenClaw's proven shape rather than inventing an unrelated API.

### OpenClaw analogue inspected

- `openclaw/src/gateway/http-auth-utils.ts`
  - `checkGatewayHttpRequestAuth`
  - `resolveTrustedHttpOperatorScopes`
  - `resolveSharedSecretHttpOperatorScopes`
  - `resolveHttpBrowserOriginPolicy`
- `openclaw/src/gateway/auth-resolve.ts`
  - `resolveGatewayAuth`
- `openclaw/src/gateway/auth.ts`
  - `assertGatewayAuthConfigured`
  - shared-secret verification and rate-limit/origin integration

Reused structure:

- authenticate before endpoint scope checks;
- shared-secret auth produces server-owned authority;
- caller-declared scopes are not trusted for Bearer authentication;
- compare the secret with a constant-time primitive;
- reject unsafe listener configuration before binding.

FireClaw adaptation:

- one shared authentication boundary serves both Mission Gateway and Robot
  Gateway;
- current shared-token and loopback modes map to fixed deployment principals;
- existing robotics control, approval, capability, execution authorization,
  and safety policies remain downstream and receive the authenticated
  `OperatorContext`.

### Files introduced

- `src/fireclaw_core/gateway/auth.py`
  - `AuthenticatedGatewayPrincipal`
  - `GatewayAuthenticationResult`
  - `authenticate_gateway_request`
  - `validate_gateway_bind`
  - `resolve_gateway_api_token`
- `tests/test_gateway_auth.py`
- `docs/architecture/gateway-authentication.md`

### Files modified for this P0

- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_gateway_client.py`
- `src/fireclaw_core/subagent/subagent_client.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/mission/interactive.py`
- `src/fireclaw_core/devtools/embodied_eval.py`
- `fireclaw.example.toml`
- `README.md`
- focused Gateway, Mission Gateway, client, CLI, configuration, and embodied
  end-to-end tests

ROS diagnostic files and their README/pyproject/test changes were already
present and remain intentionally unmodified except where shared Gateway code
had to coexist with them.

### Implemented behavior

1. `GET /health` on Robot Gateway is the only public route and receives the
   fixed read-only `anonymous-health` principal.
2. Tokenless loopback requests receive the fixed server-owned
   `local-loopback-operator` admin principal. The local OS account is the
   compatibility trust boundary.
3. When an API token is configured, every non-health request requires a valid
   Bearer credential, verified with `secrets.compare_digest`. A valid token
   becomes `gateway-shared-token` with server-owned admin authority.
4. Tokenless non-loopback listeners are rejected before `ThreadingHTTPServer`
   binds. Explicit blank tokens are also rejected.
5. Both Gateway request handlers now authenticate first, check method scopes
   from `principal.gateway_scopes`, and only then parse/dispatch request data.
6. Request-body operator identity/role/scopes and old `X-Operator-*` headers
   no longer affect authorization.
7. Task submit, cancel, confirmation, emergency stop, mission submit, mission
   cancel, memory lifecycle actions, reusable-knowledge actions, and memory
   tool access use the same authenticated principal.
8. Mission approval `requested_by` and `decided_by` now use the request's
   authenticated principal instead of the MissionAgent default operator.
9. Mission Gateway and Robot Agent HTTP clients no longer send self-declared
   identity/scope headers. `RobotSubagentClient` retains old `operator`
   parameters for source compatibility but does not transmit them.
10. Inbound Mission Gateway and outbound Robot Gateway credentials are wired
    through TOML, CLI, and environment variables:
    - `FIRECLAW_GATEWAY_TOKEN`
    - `FIRECLAW_ROBOT_GATEWAY_TOKEN`

### Commands and results

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q src tests
```

Passed.

Initial focused run after changing semantics:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_method_scopes.py tests/test_gateway.py \
  tests/test_gateway_structured_task.py tests/test_mission_gateway.py \
  tests/test_mission_gateway_client.py tests/test_subagent_client.py \
  tests/test_gateway_robot_agent_cli.py tests/test_serve.py
```

Result: 11 failures, all caused by old tests expecting caller-provided scopes
or payload roles to authorize/deny requests. No implementation crash was
observed. Tests were rewritten to assert that forged declarations are ignored
and missing/wrong credentials remain unauthorized.

Final focused run:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_gateway_auth.py tests/test_method_scopes.py \
  tests/test_gateway.py tests/test_gateway_structured_task.py \
  tests/test_mission_gateway.py tests/test_mission_gateway_client.py \
  tests/test_mission_agent.py tests/test_subagent_client.py \
  tests/test_gateway_robot_agent_cli.py tests/test_serve.py \
  tests/test_embodied_gateway_e2e.py tests/test_embodied_mission_e2e.py
```

Result: `279 passed in 80.39s`.

Full repository regression:

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
```

Result: `1895 passed, 7 skipped in 159.62s`.

`git diff --check` passed.

### Current conclusion

The original privilege-escalation path is closed: network callers cannot
become admin, supervisor, emergency-stop operator, approval actor, or memory
auditor by declaring headers or JSON fields. Endpoint authority and downstream
operator identity now originate from one authenticated server principal, and
audit records use that same identity.

### Known boundary and remaining security work

- A shared token identifies possession of one deployment credential, not an
  individual human. It currently maps to an all-admin principal.
- Tokenless loopback is retained as an explicit compatibility mode. Real robot
  deployments should configure a token even on loopback.
- OpenClaw additionally provides browser Origin/Host enforcement,
  authentication failure rate limiting, trusted-proxy identity, secret
  references/rotation, and stricter default token bootstrap. These were not
  silently claimed as part of this P0 and should be handled as separate
  security work.
- TLS or mutually authenticated robot-LAN transport is still required before
  exposing Mission-to-Robot traffic outside a trusted development network.

### Next recommended step

Do not start another P0 implicitly. Explain this P0 to the user with a concrete
before/after attack example, then continue with the next item from the
previously agreed P0 list only after the user confirms.
