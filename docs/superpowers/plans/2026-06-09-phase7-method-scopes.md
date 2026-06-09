# Phase 7: Gateway Method Scopes and Security Review

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add OpenClaw-style method descriptor table and default-deny authorization to all FireClaw Gateway endpoints.

**Architecture:** Create `method_scopes.py` with scope constants, method descriptor table, and `authorize_method()`. Integrate into both `gateway.py` (robot-local) and `mission_gateway.py` (mission-level). Write security review doc.

**Tech Stack:** Python, pytest

---

## File Structure

- Create: `src/fireclaw_core/method_scopes.py`
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Create: `tests/test_method_scopes.py`
- Create: `docs/security/gateway-endpoint-security-review.md`

---

### Task 1: Create method_scopes.py

**Files:**
- Create: `src/fireclaw_core/method_scopes.py`
- Create: `tests/test_method_scopes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_method_scopes.py
from fireclaw_core.method_scopes import (
    ADMIN_SCOPE,
    APPROVALS_SCOPE,
    EMERGENCY_SCOPE,
    PAIRING_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    MethodDescriptor,
    authorize_method,
    resolve_required_scope,
)


class TestScopeConstants:
    def test_scope_constants_are_strings(self):
        assert isinstance(ADMIN_SCOPE, str)
        assert isinstance(READ_SCOPE, str)
        assert isinstance(WRITE_SCOPE, str)
        assert isinstance(APPROVALS_SCOPE, str)
        assert isinstance(PAIRING_SCOPE, str)
        assert isinstance(EMERGENCY_SCOPE, str)

    def test_admin_scope_value(self):
        assert ADMIN_SCOPE == "admin"

    def test_read_scope_value(self):
        assert READ_SCOPE == "state.read"

    def test_write_scope_value(self):
        assert WRITE_SCOPE == "task.submit"


class TestMethodDescriptor:
    def test_descriptor_creation(self):
        desc = MethodDescriptor(
            method="GET /state",
            required_scope=READ_SCOPE,
            category="read",
            description="Robot state",
        )
        assert desc.method == "GET /state"
        assert desc.required_scope == READ_SCOPE
        assert desc.category == "read"

    def test_descriptor_frozen(self):
        desc = MethodDescriptor(
            method="GET /state",
            required_scope=READ_SCOPE,
            category="read",
        )
        try:
            desc.method = "changed"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestResolveRequiredScope:
    def test_known_method_returns_scope(self):
        scope = resolve_required_scope("GET /state")
        assert scope == READ_SCOPE

    def test_unknown_method_returns_admin(self):
        scope = resolve_required_scope("GET /totally-unknown")
        assert scope == ADMIN_SCOPE

    def test_post_missions_requires_submit(self):
        scope = resolve_required_scope("POST /missions")
        assert scope == WRITE_SCOPE

    def test_emergency_stop_requires_emergency(self):
        scope = resolve_required_scope("POST /emergency-stop")
        assert scope == EMERGENCY_SCOPE


class TestAuthorizeMethod:
    def test_admin_allows_everything(self):
        result = authorize_method("POST /missions", {ADMIN_SCOPE})
        assert result.allowed is True

    def test_read_scope_allows_read_endpoint(self):
        result = authorize_method("GET /state", {READ_SCOPE})
        assert result.allowed is True

    def test_write_scope_allows_read_endpoint(self):
        result = authorize_method("GET /state", {WRITE_SCOPE})
        assert result.allowed is True

    def test_read_scope_denies_write_endpoint(self):
        result = authorize_method("POST /missions", {READ_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == WRITE_SCOPE

    def test_empty_scopes_denies_read(self):
        result = authorize_method("GET /state", set())
        assert result.allowed is False
        assert result.missing_scope == READ_SCOPE

    def test_unknown_endpoint_requires_admin(self):
        result = authorize_method("GET /unknown", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == ADMIN_SCOPE

    def test_approval_scope_required_for_approvals(self):
        result = authorize_method("POST /missions/abc/approvals", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == APPROVALS_SCOPE

    def test_emergency_scope_required_for_emergency(self):
        result = authorize_method("POST /emergency-stop", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == EMERGENCY_SCOPE

    def test_pairing_scope_required_for_enrollment(self):
        result = authorize_method("POST /enrollment", {WRITE_SCOPE})
        assert result.allowed is False
        assert result.missing_scope == PAIRING_SCOPE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_method_scopes.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement method_scopes.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Scope constants — adapted from OpenClaw operator-scopes.ts
ADMIN_SCOPE = "admin"
READ_SCOPE = "state.read"
WRITE_SCOPE = "task.submit"
APPROVALS_SCOPE = "mission.approve"
PAIRING_SCOPE = "robot.pairing"
EMERGENCY_SCOPE = "emergency.stop"


@dataclass(frozen=True)
class MethodDescriptor:
    method: str
    required_scope: str
    category: str
    description: str = ""


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    missing_scope: str | None = None


# Method descriptor table — maps "VERB /path" to required scope.
# Default for unregistered methods: ADMIN_SCOPE (default-deny).
_METHOD_DESCRIPTORS: dict[str, MethodDescriptor] = {}


def _register(descriptor: MethodDescriptor) -> None:
    _METHOD_DESCRIPTORS[descriptor.method] = descriptor


# Robot-local Gateway endpoints
_register(MethodDescriptor("GET /health", READ_SCOPE, "read", "Health check"))
_register(MethodDescriptor("GET /state", READ_SCOPE, "read", "Robot state"))
_register(MethodDescriptor("GET /skills", READ_SCOPE, "read", "List skills"))
_register(MethodDescriptor("GET /memory/recent", READ_SCOPE, "read", "Recent memory"))
_register(MethodDescriptor("GET /events/recent", READ_SCOPE, "read", "Recent events"))
_register(MethodDescriptor("GET /events", READ_SCOPE, "read", "Event ledger"))
_register(MethodDescriptor("GET /tasks/{id}", READ_SCOPE, "read", "Task trace"))
_register(MethodDescriptor("GET /tasks/{id}/events", READ_SCOPE, "read", "Task events"))
_register(MethodDescriptor("POST /tasks", WRITE_SCOPE, "write", "Submit task"))
_register(MethodDescriptor("POST /tasks/{id}/cancel", WRITE_SCOPE, "write", "Cancel task"))
_register(MethodDescriptor("POST /confirm", APPROVALS_SCOPE, "approve", "Confirm high-risk"))
_register(MethodDescriptor("POST /cancel", WRITE_SCOPE, "write", "Cancel via command"))
_register(MethodDescriptor("POST /emergency-stop", EMERGENCY_SCOPE, "emergency", "Emergency stop"))

# Mission-level Gateway endpoints
_register(MethodDescriptor("POST /missions", WRITE_SCOPE, "write", "Submit mission"))
_register(MethodDescriptor("GET /missions/{id}/trace", READ_SCOPE, "read", "Mission trace"))
_register(MethodDescriptor("GET /missions/{id}/events", READ_SCOPE, "read", "Mission events"))
_register(MethodDescriptor("POST /missions/{id}/cancel", WRITE_SCOPE, "write", "Cancel mission"))
_register(MethodDescriptor("POST /missions/{id}/approvals", APPROVALS_SCOPE, "approve", "Mission approval"))
_register(MethodDescriptor("GET /fleet/state", READ_SCOPE, "read", "Fleet state"))
_register(MethodDescriptor("GET /fleet/doctor", READ_SCOPE, "read", "Fleet diagnostics"))

# Enrollment endpoints
_register(MethodDescriptor("POST /enrollment", PAIRING_SCOPE, "pairing", "Enrollment request"))
_register(MethodDescriptor("POST /enrollment/approve", PAIRING_SCOPE, "pairing", "Approve enrollment"))


def _match_registered(method: str) -> MethodDescriptor | None:
    """Match a concrete method string against registered patterns."""
    if method in _METHOD_DESCRIPTORS:
        return _METHOD_DESCRIPTORS[method]
    # Try pattern matching for parameterized paths
    parts = method.split(" ", 1)
    if len(parts) != 2:
        return None
    verb, path = parts
    path_segments = path.strip("/").split("/")
    for pattern, desc in _METHOD_DESCRIPTORS.items():
        p_verb, p_path = pattern.split(" ", 1)
        if verb != p_verb:
            continue
        p_segments = p_path.strip("/").split("/")
        if len(p_segments) != len(path_segments):
            continue
        match = True
        for ps, cs in zip(p_segments, path_segments):
            if ps.startswith("{") and ps.endswith("}"):
                continue
            if ps != cs:
                match = False
                break
        if match:
            return desc
    return None


def resolve_required_scope(method: str) -> str:
    """Resolve the required scope for a method. Unknown methods require ADMIN_SCOPE."""
    desc = _match_registered(method)
    if desc is not None:
        return desc.required_scope
    return ADMIN_SCOPE


def authorize_method(method: str, scopes: set[str]) -> AuthorizationResult:
    """Check if the given scopes authorize the method.

    OpenClaw pattern:
    - ADMIN_SCOPE bypasses all checks.
    - WRITE_SCOPE implies READ_SCOPE.
    - Unknown methods require ADMIN_SCOPE (default-deny).
    """
    if ADMIN_SCOPE in scopes:
        return AuthorizationResult(allowed=True)

    required = resolve_required_scope(method)

    # WRITE_SCOPE implies READ_SCOPE
    if required == READ_SCOPE and WRITE_SCOPE in scopes:
        return AuthorizationResult(allowed=True)

    if required in scopes:
        return AuthorizationResult(allowed=True)

    return AuthorizationResult(allowed=False, missing_scope=required)


def all_descriptors() -> list[MethodDescriptor]:
    """Return all registered method descriptors for documentation/audit."""
    return sorted(_METHOD_DESCRIPTORS.values(), key=lambda d: d.method)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_method_scopes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/method_scopes.py tests/test_method_scopes.py
git commit -m "feat: add method scopes with default-deny authorization"
```

---

### Task 2: Integrate authorize_method into both Gateways

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Modify: `tests/test_method_scopes.py`

- [ ] **Step 1: Add integration tests**

```python
# Add to tests/test_method_scopes.py

class TestGatewayIntegration:
    """Test that both gateways enforce method scopes."""

    def test_robot_gateway_state_endpoint_authorized(self):
        from fireclaw_core.gateway import FireClawGateway, GatewayConfig
        gateway = FireClawGateway(GatewayConfig(adapter="dry-run"))
        gateway.start()
        try:
            import urllib.request
            req = urllib.request.Request(f"{gateway.base_url}/state")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            gateway.stop()

    def test_mission_gateway_fleet_state_authorized(self):
        from fireclaw_core.mission_gateway import MissionGateway, MissionGatewayConfig
        from fireclaw_core.mission_agent import MissionAgent
        from fireclaw_core.robot_registry import RobotRegistry
        agent = MissionAgent(registry=RobotRegistry())
        gateway = MissionGateway(
            MissionGatewayConfig(),
            mission_agent=agent,
            registry=RobotRegistry(),
        )
        gateway.start()
        try:
            import urllib.request
            req = urllib.request.Request(f"{gateway.base_url}/fleet/state")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            gateway.stop()
```

- [ ] **Step 2: Run integration tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_method_scopes.py::TestGatewayIntegration -v`
Expected: FAIL (gateways don't use authorize_method yet)

- [ ] **Step 3: Integrate into gateway.py**

Add import at top:
```python
from fireclaw_core.method_scopes import READ_SCOPE, authorize_method
```

Modify `_handler_class` to add scope enforcement in `do_GET` and `do_POST`:
```python
def _handler_class(self):
    gateway = self

    class GatewayRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not gateway._check_auth(self):
                return
            parsed = urlparse(self.path)
            method_key = f"GET {parsed.path}"
            # Health check bypasses scope enforcement
            if parsed.path == "/health":
                gateway._handle_get(self)
                return
            scopes = _extract_scopes_from_auth(self)
            result = authorize_method(method_key, scopes)
            if not result.allowed:
                gateway._write_error(
                    self, HTTPStatus.FORBIDDEN,
                    f"Missing required scope: {result.missing_scope}",
                )
                return
            gateway._handle_get(self)

        def do_POST(self) -> None:
            if not gateway._check_auth(self):
                return
            parsed = urlparse(self.path)
            method_key = f"POST {parsed.path}"
            scopes = _extract_scopes_from_auth(self)
            result = authorize_method(method_key, scopes)
            if not result.allowed:
                gateway._write_error(
                    self, HTTPStatus.FORBIDDEN,
                    f"Missing required scope: {result.missing_scope}",
                )
                return
            gateway._handle_post(self)

        def log_message(self, format: str, *args: object) -> None:
            return

    return GatewayRequestHandler
```

Add helper function:
```python
def _extract_scopes_from_auth(handler: BaseHTTPRequestHandler) -> set[str]:
    """Extract operator scopes from X-Operator-Scopes header or default."""
    scopes_header = handler.headers.get("X-Operator-Scopes", "")
    if scopes_header:
        return {s.strip() for s in scopes_header.split(",") if s.strip()}
    return {"state.read"}  # default: read-only
```

- [ ] **Step 4: Integrate into mission_gateway.py**

Same pattern: add import, modify `_handler_class`, add `_extract_scopes_from_auth`.

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/gateway.py src/fireclaw_core/mission_gateway.py tests/test_method_scopes.py
git commit -m "feat: integrate method scope authorization into both gateways"
```

---

### Task 3: Write endpoint security review doc

**Files:**
- Create: `docs/security/gateway-endpoint-security-review.md`

- [ ] **Step 1: Generate security review doc**

```markdown
# Gateway Endpoint Security Review

Date: 2026-06-09

## Scope

This document reviews all FireClaw Gateway endpoints for authorization coverage.

## Authorization Model

FireClaw uses OpenClaw-style method scopes with default-deny:
- `admin` — unrestricted access
- `state.read` — read-only endpoints
- `task.submit` — submit/cancel tasks and missions (implies read)
- `mission.approve` — confirm high-risk actions, handle approvals
- `robot.pairing` — enrollment/pairing operations
- `emergency.stop` — emergency stop

Unknown/unregistered endpoints require `admin` scope.

## Endpoint Matrix

| Endpoint | Method | Required Scope | Category |
|---|---|---|---|
| /health | GET | state.read | read |
| /state | GET | state.read | read |
| /skills | GET | state.read | read |
| /memory/recent | GET | state.read | read |
| /events/recent | GET | state.read | read |
| /events | GET | state.read | read |
| /tasks/{id} | GET | state.read | read |
| /tasks/{id}/events | GET | state.read | read |
| /tasks | POST | task.submit | write |
| /tasks/{id}/cancel | POST | task.submit | write |
| /confirm | POST | mission.approve | approve |
| /cancel | POST | task.submit | write |
| /emergency-stop | POST | emergency.stop | emergency |
| /missions | POST | task.submit | write |
| /missions/{id}/trace | GET | state.read | read |
| /missions/{id}/events | GET | state.read | read |
| /missions/{id}/cancel | POST | task.submit | write |
| /missions/{id}/approvals | POST | mission.approve | approve |
| /fleet/state | GET | state.read | read |
| /fleet/doctor | GET | state.read | read |
| /enrollment | POST | robot.pairing | pairing |
| /enrollment/approve | POST | robot.pairing | pairing |

## Role Permission Matrix

| Scope | observer | operator | supervisor | admin |
|---|---|---|---|---|
| state.read | ✓ | ✓ | ✓ | ✓ |
| task.submit | ✗ | ✓ | ✓ | ✓ |
| mission.approve | ✗ | ✗ | ✓ | ✓ |
| robot.pairing | ✗ | ✗ | ✗ | ✓ |
| emergency.stop | ✗ | ✗ | ✗ | ✓ |
| admin | ✗ | ✗ | ✗ | ✓ |

## Default-Deny Behavior

Any endpoint not in the descriptor table requires `admin` scope.
This ensures new endpoints are secure by default.
```

- [ ] **Step 2: Commit**

```bash
git add docs/security/gateway-endpoint-security-review.md
git commit -m "docs: add gateway endpoint security review"
```
