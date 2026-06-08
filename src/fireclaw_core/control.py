from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


ROLE_SCOPES: dict[str, set[str]] = {
    "observer": {"state.read", "mission.read"},
    "operator": {
        "task.submit", "task.confirm", "task.cancel", "state.read",
        "mission.submit", "mission.cancel", "mission.plan", "mission.read",
        "mission.correct",
    },
    "supervisor": {
        "task.submit",
        "task.confirm",
        "task.cancel",
        "safety.override",
        "state.read",
        "mission.submit",
        "mission.cancel",
        "mission.plan",
        "mission.read",
        "mission.correct",
    },
    "admin": {
        "task.submit",
        "task.confirm",
        "task.cancel",
        "safety.override",
        "emergency.stop",
        "state.read",
        "mission.submit",
        "mission.cancel",
        "mission.plan",
        "mission.read",
        "mission.correct",
    },
}


@dataclass(frozen=True)
class OperatorContext:
    operator_id: str
    display_name: str | None = None
    role: str = "operator"
    control_scopes: set[str] = field(default_factory=set)
    source: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_id": self.operator_id,
            "display_name": self.display_name,
            "role": self.role,
            "control_scopes": sorted(self.control_scopes),
            "source": self.source,
        }


@dataclass(frozen=True)
class ControlDecision:
    status: str
    action: str
    reasons: list[str]
    operator: OperatorContext

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "reasons": list(self.reasons),
            "operator": self.operator.to_dict(),
        }


@dataclass(frozen=True)
class AuthorizationRequest:
    request_id: str
    task_id: str
    session_id: str
    command: str
    requested_by: OperatorContext
    required_scope: str
    risk_level: str
    requested_at: str
    expires_at: str
    status: str = "pending"

    def is_expired(self, now: str) -> bool:
        return datetime.fromisoformat(now) >= datetime.fromisoformat(self.expires_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "command": self.command,
            "requested_by": self.requested_by.to_dict(),
            "required_scope": self.required_scope,
            "risk_level": self.risk_level,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "status": self.status,
        }


def scopes_for_role(role: str) -> set[str]:
    return set(ROLE_SCOPES.get(role, ROLE_SCOPES["observer"]))


DEFAULT_LOCAL_OPERATOR = OperatorContext(
    operator_id="local-operator",
    display_name="Local Operator",
    role="operator",
    control_scopes=scopes_for_role("operator"),
    source="local",
)


def operator_from_payload(payload: Any) -> OperatorContext:
    if not isinstance(payload, dict):
        return DEFAULT_LOCAL_OPERATOR
    role = _string_or_default(payload.get("role"), "operator")
    provided_scopes = payload.get("control_scopes")
    scopes = _string_set(provided_scopes) if provided_scopes is not None else scopes_for_role(role)
    return OperatorContext(
        operator_id=_string_or_default(payload.get("operator_id"), DEFAULT_LOCAL_OPERATOR.operator_id),
        display_name=_optional_string(payload.get("display_name")),
        role=role,
        control_scopes=scopes,
        source=_string_or_default(payload.get("source"), "gateway"),
    )


class ControlPolicy:
    def evaluate(self, operator: OperatorContext, action: str) -> ControlDecision:
        if action in operator.control_scopes:
            return ControlDecision(
                status="allow",
                action=action,
                reasons=[],
                operator=operator,
            )
        return ControlDecision(
            status="deny",
            action=action,
            reasons=[f"Operator {operator.operator_id} lacks required scope: {action}"],
            operator=operator,
        )

    def evaluate_risk(self, operator: OperatorContext, *, action: str, risk_level: str) -> ControlDecision:
        base_decision = self.evaluate(operator, action)
        if base_decision.status != "allow":
            return base_decision
        if risk_level in {"high", "critical"} and "safety.override" not in operator.control_scopes:
            return ControlDecision(
                status="approval_required",
                action=action,
                reasons=[f"Action {action} with risk_level={risk_level} requires scope: safety.override"],
                operator=operator,
            )
        return base_decision


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}
