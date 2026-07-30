import pytest

from fireclaw_core.infra.runtime_state import (
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
