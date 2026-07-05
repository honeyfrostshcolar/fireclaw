# FireClaw Operator Authorization v2 Session

## 2026-06-05 13:05 CST

### Task Goal

Implement Operator Authorization v2 after committing the ROS1 diagnostics/remap work.

### Current Baseline

Before this task, the repository was clean at:

- `e52ef7d feat: add ros1 diagnostics and remap config`

The user asked to add Operator Authorization v2 and asked what it is for. The design target is to separate natural-language intent from physical-action authorization.

### Design Scope

Implemented Gateway-level authorization:

```text
operator identity -> role/scope -> risk-aware approval -> expiry -> audit event -> enforcement
```

Out of scope:

- passwords/tokens/signatures;
- external identity providers;
- UI approval workflows;
- ROS-side permission enforcement.

### Files Modified

- `src/fireclaw_core/control.py`
- `src/fireclaw_core/gateway.py`
- `tests/test_control.py`
- `tests/test_gateway.py`
- `README.md`
- `docs/superpowers/specs/2026-06-05-operator-authorization-v2-design.md`
- `docs/superpowers/plans/2026-06-05-operator-authorization-v2.md`
- `memory/2026-06-05/fireclaw-operator-authorization-v2.md`

### Implementation Details

- Added `AuthorizationRequest`.
- Added `ControlPolicy.evaluate_risk(...)`.
- High/critical risk `task.confirm` requires `safety.override`.
- Gateway records pending authorization requests when a task finishes with `status="awaiting_confirmation"` and the requesting operator lacks `safety.override`.
- Gateway stores pending authorizations by `session_id`.
- `/confirm` now:
  - checks pending authorization;
  - denies operator approval without `safety.override`;
  - expires stale authorization requests;
  - records `authorization.approved` before running the confirmed task.
- Added `GatewayConfig.authorization_expiry_seconds`, default 300.
- `POST /tasks/<task_id>/cancel` now checks `task.cancel`.
- Unauthorized cancel records `task.cancel_denied` and does not set the cancellation event.

### Commands Executed

- `.venv/bin/python -m pytest tests/test_control.py::test_operator_requires_approval_for_high_risk_action_without_override_scope tests/test_control.py::test_supervisor_can_approve_high_risk_action tests/test_control.py::test_authorization_request_expires_after_deadline -q`
  - RED: `AuthorizationRequest` and `evaluate_risk` did not exist.
- `.venv/bin/python -m pytest tests/test_control.py::test_operator_requires_approval_for_high_risk_action_without_override_scope tests/test_control.py::test_supervisor_can_approve_high_risk_action tests/test_control.py::test_authorization_request_expires_after_deadline -q`
  - GREEN: 3 passed.
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_confirms_pending_high_risk_skill tests/test_gateway.py::test_gateway_denies_high_risk_confirmation_from_operator_without_override tests/test_gateway.py::test_gateway_expires_pending_high_risk_authorization_before_confirm -q`
  - RED: missing `authorization.requested`, no `/confirm` authorization enforcement, no expiry config.
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_confirms_pending_high_risk_skill tests/test_gateway.py::test_gateway_denies_high_risk_confirmation_from_operator_without_override tests/test_gateway.py::test_gateway_expires_pending_high_risk_authorization_before_confirm -q`
  - GREEN: 3 passed.
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_observer_cannot_cancel_active_task -q`
  - RED: HTTP cancel ignored operator payload.
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_observer_cannot_cancel_active_task tests/test_gateway.py::test_gateway_cancels_active_task_between_skills -q`
  - GREEN: 2 passed.
- `.venv/bin/python -m pytest tests/test_control.py tests/test_gateway.py -q`
  - GREEN: 21 passed.

### Current Conclusion

FireClaw now has a first auditable authorization layer at the Gateway. High-risk task confirmation requires a supervisor/admin-style `safety.override` scope, approval requests expire, and unauthorized cancellation attempts are denied without stopping active tasks.

### Research Impact

This strengthens FireClaw's safety-critical argument. The framework no longer treats operator natural language as sufficient authorization for high-risk physical action. It records who requested, who approved or denied, when authorization expired, and whether cancellation was allowed.

### Remaining Gaps

- No cryptographic authentication or signed approval.
- No per-task ownership rule yet; any operator with `task.cancel` can cancel active tasks.
- No UI for pending approvals.
- No persistent authorization request store beyond process memory.
- No explicit approval endpoint separate from `/confirm`.
