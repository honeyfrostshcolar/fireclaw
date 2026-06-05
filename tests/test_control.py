from fireclaw_core.control import (
    AuthorizationRequest,
    DEFAULT_LOCAL_OPERATOR,
    ControlPolicy,
    operator_from_payload,
    scopes_for_role,
)


def test_default_local_operator_has_development_control_scopes():
    assert DEFAULT_LOCAL_OPERATOR.operator_id == "local-operator"
    assert DEFAULT_LOCAL_OPERATOR.role == "operator"
    assert "task.submit" in DEFAULT_LOCAL_OPERATOR.control_scopes
    assert "task.confirm" in DEFAULT_LOCAL_OPERATOR.control_scopes
    assert "task.cancel" in DEFAULT_LOCAL_OPERATOR.control_scopes
    assert "state.read" in DEFAULT_LOCAL_OPERATOR.control_scopes


def test_observer_role_cannot_submit_tasks():
    operator = operator_from_payload({"operator_id": "observer-1", "role": "observer"})

    decision = ControlPolicy().evaluate(operator, "task.submit")

    assert decision.status == "deny"
    assert "task.submit" in decision.reasons[0]
    assert decision.operator.operator_id == "observer-1"


def test_operator_role_can_submit_and_cancel_but_not_emergency_stop():
    operator = operator_from_payload({"operator_id": "operator-1", "role": "operator"})
    policy = ControlPolicy()

    submit = policy.evaluate(operator, "task.submit")
    cancel = policy.evaluate(operator, "task.cancel")
    emergency = policy.evaluate(operator, "emergency.stop")

    assert submit.status == "allow"
    assert cancel.status == "allow"
    assert emergency.status == "deny"


def test_admin_role_can_trigger_emergency_stop():
    operator = operator_from_payload({"operator_id": "admin-1", "role": "admin"})

    decision = ControlPolicy().evaluate(operator, "emergency.stop")

    assert decision.status == "allow"
    assert "emergency.stop" in scopes_for_role("admin")


def test_operator_payload_defaults_to_local_operator_when_missing():
    operator = operator_from_payload(None)

    assert operator == DEFAULT_LOCAL_OPERATOR


def test_operator_requires_approval_for_high_risk_action_without_override_scope():
    operator = operator_from_payload({"operator_id": "operator-1", "role": "operator"})

    decision = ControlPolicy().evaluate_risk(operator, action="task.confirm", risk_level="high")

    assert decision.status == "approval_required"
    assert decision.action == "task.confirm"
    assert "safety.override" in decision.reasons[0]


def test_supervisor_can_approve_high_risk_action():
    supervisor = operator_from_payload({"operator_id": "supervisor-1", "role": "supervisor"})

    decision = ControlPolicy().evaluate_risk(supervisor, action="task.confirm", risk_level="high")

    assert decision.status == "allow"


def test_authorization_request_expires_after_deadline():
    request = AuthorizationRequest(
        request_id="auth-1",
        task_id="task-1",
        session_id="session-1",
        command="运行 smoke_entry",
        requested_by=operator_from_payload({"operator_id": "operator-1", "role": "operator"}),
        required_scope="safety.override",
        risk_level="high",
        requested_at="2026-06-05T00:00:00+00:00",
        expires_at="2026-06-05T00:05:00+00:00",
    )

    assert request.is_expired("2026-06-05T00:06:00+00:00") is True
    assert request.to_dict()["required_scope"] == "safety.override"
