# Gateway Endpoint Security Review

Date: 2026-06-09
Phase: 7 — Gateway Method Scopes and Security Review

## Authorization Model

FireClaw uses OpenClaw-style method scopes with **default-deny**:

- Any endpoint not in the descriptor table requires `admin` scope.
- `admin` scope bypasses all checks.
- `task.submit` (write) implies `state.read` (read).
- Scopes are provided via `X-Operator-Scopes` request header (comma-separated).
- Default scopes when no header: `state.read` (read-only).
- Bearer token auth (`Authorization: Bearer <token>`) is checked before scope enforcement.
- Health check (`GET /health` on robot-local gateway) bypasses scope enforcement.

## Scope Constants

| Constant | Value | Purpose |
|---|---|---|
| `ADMIN_SCOPE` | `admin` | Unrestricted access, bypasses all checks |
| `READ_SCOPE` | `state.read` | Read-only endpoints (state, events, traces) |
| `WRITE_SCOPE` | `task.submit` | Submit/cancel tasks and missions (implies read) |
| `APPROVALS_SCOPE` | `mission.approve` | Confirm high-risk actions, handle approvals |
| `PAIRING_SCOPE` | `robot.pairing` | Enrollment/pairing operations |
| `EMERGENCY_SCOPE` | `emergency.stop` | Emergency stop |

## Endpoint Matrix

### Robot-Local Gateway (port 8765)

| Endpoint | Method | Required Scope | Category | Description |
|---|---|---|---|---|
| `/health` | GET | *bypassed* | health | Health check (liveness probe) |
| `/state` | GET | state.read | read | Robot state |
| `/skills` | GET | state.read | read | List available skills |
| `/memory/recent` | GET | state.read | read | Recent memory records |
| `/events/recent` | GET | state.read | read | Recent events |
| `/events` | GET | state.read | read | Event ledger query |
| `/tasks/{id}` | GET | state.read | read | Task trace |
| `/tasks/{id}/events` | GET | state.read | read | Task-specific events |
| `/tasks` | POST | task.submit | write | Submit new task |
| `/tasks/{id}/cancel` | POST | task.submit | write | Cancel running task |
| `/confirm` | POST | mission.approve | approve | Confirm high-risk action |
| `/cancel` | POST | task.submit | write | Cancel via command |
| `/emergency-stop` | POST | emergency.stop | emergency | Emergency stop |

### Mission-Level Gateway (port 8766)

| Endpoint | Method | Required Scope | Category | Description |
|---|---|---|---|---|
| `/missions` | POST | task.submit | write | Submit mission |
| `/missions/{id}/trace` | GET | state.read | read | Mission trace |
| `/missions/{id}/events` | GET | state.read | read | Mission events |
| `/missions/{id}/cancel` | POST | task.submit | write | Cancel mission |
| `/missions/{id}/approvals` | POST | mission.approve | approve | Handle approval request |
| `/fleet/state` | GET | state.read | read | Fleet state overview |
| `/fleet/doctor` | GET | state.read | read | Fleet diagnostics |

### Enrollment Endpoints

| Endpoint | Method | Required Scope | Category | Description |
|---|---|---|---|---|
| `/enrollment` | POST | robot.pairing | pairing | Submit enrollment request |
| `/enrollment/approve` | POST | robot.pairing | pairing | Approve enrollment |

## Role Permission Matrix

| Scope | observer | operator | supervisor | admin |
|---|---|---|---|---|
| `state.read` | ✓ | ✓ | ✓ | ✓ |
| `task.submit` | ✗ | ✓ | ✓ | ✓ |
| `mission.approve` | ✗ | ✗ | ✓ | ✓ |
| `robot.pairing` | ✗ | ✗ | ✗ | ✓ |
| `emergency.stop` | ✗ | ✗ | ✗ | ✓ |
| `admin` | ✗ | ✗ | ✗ | ✓ |

## Default-Deny Behavior

Any endpoint **not** in the descriptor table requires `admin` scope. This ensures:

1. New endpoints are secure by default.
2. Forgotten scope assignments fail closed, not open.
3. Security audit can enumerate all permitted endpoints via `all_descriptors()`.

## Scope Enforcement Flow

```
Request arrives
  → Bearer token auth (_check_auth)
    → 401 if token invalid
  → Parse path, build "VERB /path" method key
  → Extract scopes from X-Operator-Scopes header
    → Default: {"state.read"} if header absent
  → authorize_method(method_key, scopes)
    → admin scope? → allow
    → Required scope in scopes? → allow
    → Required is state.read and has task.submit? → allow (write implies read)
    → Otherwise → 403 with missing scope message
  → Handle request
```

## Security Considerations

### System-to-System Communication

`RobotSubagentClient` sends `X-Operator-Scopes: admin` by default. This is appropriate because:
- It's a system-to-system call (mission agent → robot gateway)
- The client is already authenticated via API token
- The mission agent has already performed its own authorization checks

### Health Check Bypass

`GET /health` on the robot-local gateway bypasses scope enforcement. This is intentional:
- Health checks are used for liveness probes
- They return no sensitive data
- They must work even when the operator's scope is unknown

### Scope Header vs. Operator Context

The `X-Operator-Scopes` header provides scopes at the HTTP layer. The existing `ControlPolicy.evaluate()` in `gateway.py` provides additional operator-level authorization at the business logic layer. Both layers must pass for a request to proceed.

## Files Involved

| File | Role |
|---|---|
| `src/fireclaw_core/method_scopes.py` | Scope constants, descriptor table, `authorize_method()` |
| `src/fireclaw_core/gateway.py` | Robot-local gateway with scope enforcement |
| `src/fireclaw_core/mission_gateway.py` | Mission-level gateway with scope enforcement |
| `src/fireclaw_core/subagent_client.py` | System-to-system client (sends admin scope) |
| `src/fireclaw_core/control.py` | Role scopes and `ControlPolicy` (existing) |
| `tests/test_method_scopes.py` | 40 tests covering scopes, authorization, and gateway enforcement |
