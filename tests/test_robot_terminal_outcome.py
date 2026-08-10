import pytest

from fireclaw_core.task.terminal_outcome import (
    build_robot_task_terminal_outcome,
    normalize_robot_task_terminal_status,
    robot_task_status_from_trace,
    robot_task_terminal_status_from_trace,
)


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("succeeded", "completed"),
        ("block", "blocked"),
        ("denied", "blocked"),
        ("clarify", "escalated"),
        ("failed", "failed"),
        ("timeout", "timed_out"),
        ("cancelled", "cancelled"),
        ("orphaned", "lost"),
    ],
)
def test_normalize_robot_task_terminal_status(raw_status, expected):
    assert normalize_robot_task_terminal_status(raw_status) == expected


def test_awaiting_confirmation_is_active_instead_of_terminal():
    trace = {
        "status": "awaiting_confirmation",
        "queue_record": {"status": "awaiting_confirmation"},
        "result": {"status": "awaiting_confirmation"},
        "events": [{"type": "authorization.requested"}],
    }

    assert normalize_robot_task_terminal_status("awaiting_confirmation") is None
    assert robot_task_terminal_status_from_trace(trace) is None
    assert robot_task_status_from_trace(trace) == "awaiting_confirmation"


def test_unacknowledged_cancellation_loss_overrides_sticky_cancel_request():
    outcome = build_robot_task_terminal_outcome(
        "lost",
        cancellation_requested=True,
    )

    assert outcome.status == "lost"
    assert outcome.event_type == "task.lost"
    assert outcome.requires_intervention is False
    assert outcome.successful is False


def test_trace_result_semantics_override_stale_completed_wrapper():
    trace = {
        "status": "completed",
        "queue_record": {"status": "completed"},
        "result": {"status": "escalated"},
        "events": [
            {
                "type": "task.completed",
                "payload": {
                    "status": "escalated",
                    "result": {"status": "escalated"},
                },
            }
        ],
    }

    assert robot_task_terminal_status_from_trace(trace) == "escalated"


def test_cancellation_event_is_sticky_over_stale_result():
    trace = {
        "result": {"status": "completed"},
        "events": [{"type": "task.cancelled"}],
    }

    assert robot_task_terminal_status_from_trace(trace) == "cancelled"


def test_runtime_status_preserves_running_without_making_it_terminal():
    trace = {
        "status": "running",
        "queue_record": {"status": "running"},
    }

    assert robot_task_terminal_status_from_trace(trace) is None
    assert robot_task_status_from_trace(trace) == "running"


def test_non_terminal_success_event_does_not_complete_task():
    trace = {
        "status": "running",
        "events": [
            {
                "type": "skill.succeeded",
                "payload": {"status": "succeeded"},
            }
        ],
    }

    assert robot_task_terminal_status_from_trace(trace) is None
    assert robot_task_status_from_trace(trace) == "running"
