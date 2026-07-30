from dataclasses import replace

from fireclaw_core.approval.execution_authorization import (
    HmacExecutionAuthorizationAuthority,
    authorized_action,
    execution_scope_hash,
)


ISSUED_AT = "2026-07-29T10:00:00+00:00"
EXPIRES_AT = "2026-07-29T10:05:00+00:00"
NOW = "2026-07-29T10:01:00+00:00"


def _authorization_fixture():
    authority = HmacExecutionAuthorizationAuthority(
        issuer_id="robot-gateway:robot-a",
        key_id="robot-local-v1",
        secret=b"test-execution-authorization-secret",
    )
    actions = [
        authorized_action(
            "navigate_to",
            {"target": {"x": 4.0, "y": 2.0}},
        )
    ]
    structured_task = {
        "mission_id": "mission-1",
        "task_id": "task-1",
        "robot_id": "robot-a",
    }
    scope_hash = execution_scope_hash(
        command="navigate to the east corridor",
        structured_task=structured_task,
        actions=actions,
    )
    authorization = authority.issue(
        request_id="approval-1",
        operator_id="operator-1",
        mission_id="mission-1",
        task_id="task-1",
        robot_id="robot-a",
        scope_hash=scope_hash,
        authorized_actions=actions,
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    return authority, authorization, actions, scope_hash


def test_execution_authorization_verifies_exact_scope_and_action():
    authority, authorization, actions, scope_hash = _authorization_fixture()

    verification = authority.verify(
        authorization,
        robot_id="robot-a",
        scope_hash=scope_hash,
        action_hashes={actions[0]["action_hash"]},
        now=NOW,
    )

    assert verification.verified is True
    assert verification.grant is not None
    assert verification.grant.authorizes(
        "navigate_to",
        {"target": {"x": 4.0, "y": 2.0}},
    )


def test_execution_authorization_rejects_modified_skill_inputs():
    authority, authorization, _, scope_hash = _authorization_fixture()
    changed = authorized_action(
        "navigate_to",
        {"target": {"x": 40.0, "y": 20.0}},
    )

    verification = authority.verify(
        authorization,
        robot_id="robot-a",
        scope_hash=scope_hash,
        action_hashes={changed["action_hash"]},
        now=NOW,
    )

    assert verification.verified is False
    assert verification.error_code == "authorization_action_mismatch"


def test_execution_authorization_rejects_modified_task_scope():
    authority, authorization, actions, _ = _authorization_fixture()
    changed_scope = execution_scope_hash(
        command="navigate to the west corridor",
        structured_task={
            "mission_id": "mission-1",
            "task_id": "task-1",
            "robot_id": "robot-a",
        },
        actions=actions,
    )

    verification = authority.verify(
        authorization,
        robot_id="robot-a",
        scope_hash=changed_scope,
        action_hashes={actions[0]["action_hash"]},
        now=NOW,
    )

    assert verification.verified is False
    assert verification.error_code == "authorization_scope_mismatch"


def test_execution_authorization_rejects_tampered_signature():
    authority, authorization, actions, scope_hash = _authorization_fixture()
    tampered = replace(authorization, operator_id="attacker")

    verification = authority.verify(
        tampered,
        robot_id="robot-a",
        scope_hash=scope_hash,
        action_hashes={actions[0]["action_hash"]},
        now=NOW,
    )

    assert verification.verified is False
    assert verification.error_code == "authorization_signature_invalid"


def test_execution_authorization_rejects_expiry_and_robot_mismatch():
    authority, authorization, actions, scope_hash = _authorization_fixture()

    expired = authority.verify(
        authorization,
        robot_id="robot-a",
        scope_hash=scope_hash,
        action_hashes={actions[0]["action_hash"]},
        now=EXPIRES_AT,
    )
    wrong_robot = authority.verify(
        authorization,
        robot_id="robot-b",
        scope_hash=scope_hash,
        action_hashes={actions[0]["action_hash"]},
        now=NOW,
    )

    assert expired.error_code == "authorization_expired"
    assert wrong_robot.error_code == "authorization_robot_mismatch"
