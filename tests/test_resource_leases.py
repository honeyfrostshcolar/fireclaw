import pytest

from fireclaw_core.infra.runtime_state import (
    ResourceAdmissionRecoveryError,
    ResourceLeaseConflict,
    SqliteAuthoritativeRuntimeStore,
    SqliteResourceLeaseManager,
)


def _managers(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    first = SqliteResourceLeaseManager(SqliteAuthoritativeRuntimeStore(path))
    second = SqliteResourceLeaseManager(SqliteAuthoritativeRuntimeStore(path))
    return first, second


def test_resource_lease_is_atomic_across_runtime_instances(tmp_path):
    first, second = _managers(tmp_path)
    leases = first.acquire(
        robot_id="robot-a",
        resource_names=("robot_motion", "local_navigation"),
        owner_id="task-1",
        operation_id="task-1:step:1",
        ttl_seconds=30,
    )

    with pytest.raises(ResourceLeaseConflict) as caught:
        second.acquire(
            robot_id="robot-a",
            resource_names=("robot_motion",),
            owner_id="task-2",
            operation_id="task-2:step:1",
            ttl_seconds=30,
        )

    assert {lease.resource_name for lease in leases} == {
        "local_navigation",
        "robot_motion",
    }
    assert caught.value.owner_id == "task-1"
    assert len(second.active(robot_id="robot-a")) == 2


def test_resource_lease_reacquire_is_idempotent_and_owner_release_is_exact(tmp_path):
    first, second = _managers(tmp_path)
    initial = first.acquire(
        robot_id="robot-a",
        resource_names=("robot_motion",),
        owner_id="task-1",
        operation_id="task-1:step:1",
        ttl_seconds=30,
    )
    repeated = second.acquire(
        robot_id="robot-a",
        resource_names=("robot_motion",),
        owner_id="task-1",
        operation_id="task-1:step:1",
        ttl_seconds=30,
    )

    assert repeated[0].generation == initial[0].generation
    assert second.release(
        robot_id="robot-a",
        owner_id="task-2",
        operation_id="task-2:step:1",
    ) == 0
    assert first.release(
        robot_id="robot-a",
        owner_id="task-1",
        operation_id="task-1:step:1",
    ) == 1
    assert second.active(robot_id="robot-a") == []


def test_emergency_stop_closes_persistent_resource_admission(tmp_path):
    first, second = _managers(tmp_path)
    first.close_admission(
        reason="operator emergency stop",
        task_id="emergency-1",
        closed_at="2026-07-29T10:00:00+00:00",
    )

    with pytest.raises(ResourceLeaseConflict) as caught:
        second.acquire(
            robot_id="robot-a",
            resource_names=("robot_motion",),
            owner_id="task-1",
            operation_id="task-1:step:1",
            ttl_seconds=30,
        )

    assert caught.value.resource_name == "resource_admission"
    assert second.admission_state()["closed"] is True


def test_stale_generation_cannot_release_reassigned_resource(tmp_path):
    first, second = _managers(tmp_path)
    initial = first.acquire(
        robot_id="robot-a",
        resource_names=("robot_motion",),
        owner_id="task-1",
        operation_id="task-1:step:1",
        ttl_seconds=30,
    )
    with first.store.transaction() as connection:
        connection.execute(
            """
            UPDATE resource_leases
            SET expires_at = '2020-01-01T00:00:00+00:00'
            WHERE robot_id = 'robot-a'
              AND resource_name = 'robot_motion'
            """
        )
    replacement = second.acquire(
        robot_id="robot-a",
        resource_names=("robot_motion",),
        owner_id="task-2",
        operation_id="task-2:step:1",
        ttl_seconds=30,
    )

    released = first.release(
        robot_id="robot-a",
        owner_id="task-1",
        operation_id="task-1:step:1",
        generations={"robot_motion": initial[0].generation},
    )

    assert released == 0
    assert replacement[0].generation == initial[0].generation + 1
    assert second.active(robot_id="robot-a")[0].owner_id == "task-2"


def _recovery_request_payload(*, request_id="recovery-1"):
    return {
        "request_id": request_id,
        "robot_id": "robot-a",
        "requested_at": "2026-08-12T10:01:00+00:00",
        "expires_at": "2026-08-12T10:06:00+00:00",
        "confirmation_phrase": f"RECOVER robot-a {request_id}",
        "requested_by": {"operator_id": "admin-1"},
        "request_stop_evidence": {"status": "stopped"},
    }


def _fresh_stop_evidence():
    return {
        "status": "stopped",
        "robot_id": "robot-a",
        "observed_at": "2026-08-12T10:01:59+00:00",
        "expires_at": "2026-08-12T10:02:10+00:00",
        "providers": [{"provider_id": "test-stop-witness"}],
    }


def test_recovery_requires_exact_confirmation_and_atomically_reopens_admission(
    tmp_path,
):
    first, second = _managers(tmp_path)
    first.close_admission(
        reason="physical_runtime_stop_unconfirmed",
        task_id="task-timeout",
        closed_at="2026-08-12T10:00:00+00:00",
    )
    request = first.create_recovery_request(_recovery_request_payload())

    with pytest.raises(ResourceAdmissionRecoveryError) as mismatch:
        second.recover_admission(
            request_id=request["request_id"],
            confirmation_phrase="RECOVER robot-a wrong-request",
            confirmed_by={"operator_id": "admin-1"},
            confirmed_at="2026-08-12T10:02:00+00:00",
            stop_evidence=_fresh_stop_evidence(),
        )

    assert mismatch.value.code == "recovery_confirmation_mismatch"
    assert second.admission_snapshot()["closed"] is True

    recovered = second.recover_admission(
        request_id=request["request_id"],
        confirmation_phrase=request["confirmation_phrase"],
        confirmed_by={"operator_id": "admin-1"},
        confirmed_at="2026-08-12T10:02:00+00:00",
        stop_evidence=_fresh_stop_evidence(),
    )

    assert recovered["status"] == "confirmed"
    assert recovered["admission"]["closed"] is False
    assert first.admission_snapshot()["closed"] is False
    assert second.recovery_request(request["request_id"])["status"] == "confirmed"


def test_new_freeze_supersedes_pending_recovery_request(tmp_path):
    first, second = _managers(tmp_path)
    first.close_admission(
        reason="first freeze",
        task_id="task-1",
        closed_at="2026-08-12T10:00:00+00:00",
    )
    request = first.create_recovery_request(_recovery_request_payload())
    second.close_admission(
        reason="second freeze",
        task_id="task-2",
        closed_at="2026-08-12T10:01:30+00:00",
    )

    with pytest.raises(ResourceAdmissionRecoveryError) as stale:
        first.recover_admission(
            request_id=request["request_id"],
            confirmation_phrase=request["confirmation_phrase"],
            confirmed_by={"operator_id": "admin-1"},
            confirmed_at="2026-08-12T10:02:00+00:00",
            stop_evidence=_fresh_stop_evidence(),
        )

    assert stale.value.code == "recovery_request_not_pending"
    assert first.recovery_request(request["request_id"])["status"] == "superseded"
    assert first.admission_snapshot()["task_id"] == "task-2"


def test_active_resource_lease_blocks_recovery(tmp_path):
    first, second = _managers(tmp_path)
    first.acquire(
        robot_id="robot-a",
        resource_names=("robot_motion",),
        owner_id="task-1",
        operation_id="task-1:step:1",
        ttl_seconds=300,
    )
    with first.store.transaction() as connection:
        connection.execute(
            """
            UPDATE resource_leases
            SET expires_at = '2099-01-01T00:00:00+00:00'
            WHERE robot_id = 'robot-a' AND resource_name = 'robot_motion'
            """
        )
    first.close_admission(
        reason="operator stop",
        task_id="emergency-1",
        closed_at="2026-08-12T10:00:00+00:00",
    )
    request = first.create_recovery_request(_recovery_request_payload())

    with pytest.raises(ResourceAdmissionRecoveryError) as active:
        second.recover_admission(
            request_id=request["request_id"],
            confirmation_phrase=request["confirmation_phrase"],
            confirmed_by={"operator_id": "admin-1"},
            confirmed_at="2026-08-12T10:02:00+00:00",
            stop_evidence=_fresh_stop_evidence(),
        )

    assert active.value.code == "resource_lease_still_active"
    assert second.admission_snapshot()["closed"] is True


def test_expired_recovery_request_stays_persistently_closed(tmp_path):
    first, second = _managers(tmp_path)
    first.close_admission(
        reason="operator stop",
        task_id="emergency-1",
        closed_at="2026-08-12T10:00:00+00:00",
    )
    request = first.create_recovery_request(_recovery_request_payload())

    expired = second.expire_recovery_request(
        request_id=request["request_id"],
        expired_at="2026-08-12T10:06:00+00:00",
    )

    assert expired["status"] == "expired"
    assert first.recovery_request(request["request_id"])["status"] == "expired"
    assert first.admission_snapshot()["closed"] is True
    with pytest.raises(ResourceAdmissionRecoveryError) as no_longer_pending:
        first.recover_admission(
            request_id=request["request_id"],
            confirmation_phrase=request["confirmation_phrase"],
            confirmed_by={"operator_id": "admin-1"},
            confirmed_at="2026-08-12T10:06:01+00:00",
            stop_evidence=_fresh_stop_evidence(),
        )
    assert no_longer_pending.value.code == "recovery_request_not_pending"
