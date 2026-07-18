from __future__ import annotations

from dataclasses import dataclass


# Scope constants — adapted from OpenClaw operator-scopes.ts
ADMIN_SCOPE = "admin"
READ_SCOPE = "state.read"
WRITE_SCOPE = "task.submit"
APPROVALS_SCOPE = "mission.approve"
PAIRING_SCOPE = "robot.pairing"
EMERGENCY_SCOPE = "emergency.stop"


@dataclass(frozen=True)
class MethodDescriptor:
    """Describes a Gateway endpoint and its required authorization scope."""

    method: str
    required_scope: str
    category: str
    description: str = ""


@dataclass(frozen=True)
class AuthorizationResult:
    """Result of an authorization check against a method descriptor."""

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
_register(MethodDescriptor("GET /events/stream", READ_SCOPE, "read", "SSE event stream"))

# Mission-level Gateway endpoints
_register(MethodDescriptor("POST /missions", WRITE_SCOPE, "write", "Submit mission"))
_register(MethodDescriptor("GET /missions/{id}/trace", READ_SCOPE, "read", "Mission trace"))
_register(MethodDescriptor("GET /missions/{id}/events", READ_SCOPE, "read", "Mission events"))
_register(MethodDescriptor("POST /missions/{id}/cancel", WRITE_SCOPE, "write", "Cancel mission"))
_register(MethodDescriptor("POST /missions/{id}/approvals", APPROVALS_SCOPE, "approve", "Mission approval"))
_register(MethodDescriptor("GET /fleet/state", READ_SCOPE, "read", "Fleet state"))
_register(MethodDescriptor("GET /fleet/doctor", READ_SCOPE, "read", "Fleet diagnostics"))
_register(MethodDescriptor("GET /missions/{id}/events/stream", READ_SCOPE, "read", "SSE mission event stream"))

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
