from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ROLE_SCOPES: dict[str, set[str]] = {
    "observer": {"state.read"},
    "operator": {"task.submit", "task.confirm", "task.cancel", "state.read"},
    "supervisor": {
        "task.submit",
        "task.confirm",
        "task.cancel",
        "safety.override",
        "state.read",
    },
    "admin": {
        "task.submit",
        "task.confirm",
        "task.cancel",
        "safety.override",
        "emergency.stop",
        "state.read",
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
