"""Phase 2 tests: local failure taxonomy."""
from __future__ import annotations

from fireclaw_core.safety.local_failure import (
    FailureCategory,
    LocalFailureReason,
    NON_RETRYABLE_CATEGORIES,
    RETRYABLE_CATEGORIES,
)


def test_failure_category_values() -> None:
    assert FailureCategory.TRANSPORT == "transport"
    assert FailureCategory.TIMEOUT == "timeout"
    assert FailureCategory.ROBOT_OFFLINE == "robot_offline"
    assert FailureCategory.LOW_BATTERY == "low_battery"
    assert FailureCategory.EMERGENCY_STOP == "emergency_stop"
    assert FailureCategory.SENSOR_UNAVAILABLE == "sensor_unavailable"
    assert FailureCategory.TARGET_UNREACHABLE == "target_unreachable"
    assert FailureCategory.ACTION_FAILED == "action_failed"
    assert FailureCategory.CANCELLED == "cancelled"
    assert FailureCategory.SAFETY_BLOCKED == "safety_blocked"
    assert FailureCategory.AUTHORIZATION_DENIED == "authorization_denied"


def test_retryable_categories() -> None:
    assert FailureCategory.TRANSPORT in RETRYABLE_CATEGORIES
    assert FailureCategory.TIMEOUT in RETRYABLE_CATEGORIES
    assert FailureCategory.SENSOR_UNAVAILABLE in RETRYABLE_CATEGORIES


def test_non_retryable_categories() -> None:
    assert FailureCategory.ROBOT_OFFLINE in NON_RETRYABLE_CATEGORIES
    assert FailureCategory.LOW_BATTERY in NON_RETRYABLE_CATEGORIES
    assert FailureCategory.EMERGENCY_STOP in NON_RETRYABLE_CATEGORIES
    assert FailureCategory.SAFETY_BLOCKED in NON_RETRYABLE_CATEGORIES
    assert FailureCategory.AUTHORIZATION_DENIED in NON_RETRYABLE_CATEGORIES


def test_no_category_in_both() -> None:
    overlap = RETRYABLE_CATEGORIES & NON_RETRYABLE_CATEGORIES
    assert overlap == set()


def test_local_failure_reason_to_dict() -> None:
    reason = LocalFailureReason(
        category=FailureCategory.TIMEOUT,
        message="navigation timed out",
        retryable=True,
        action="navigate_to_waypoint",
        robot_id="r1",
    )
    d = reason.to_dict()
    assert d["category"] == "timeout"
    assert d["message"] == "navigation timed out"
    assert d["retryable"] is True
    assert d["action"] == "navigate_to_waypoint"
    assert d["robot_id"] == "r1"


def test_local_failure_reason_from_robot_result_timeout() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="failed",
        error="Connection timed out after 30s",
        action="navigate_to_waypoint",
        robot_id="r1",
    )
    assert reason.category == FailureCategory.TIMEOUT
    assert reason.retryable is True
    assert reason.action == "navigate_to_waypoint"


def test_local_failure_reason_from_robot_result_offline() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="failed",
        error="Robot offline",
        action="victim_search",
        robot_id="r2",
    )
    assert reason.category == FailureCategory.ROBOT_OFFLINE
    assert reason.retryable is False


def test_local_failure_reason_from_robot_result_not_configured() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="not_configured",
        error="ROS1 endpoint for navigate_to_waypoint is not configured.",
        action="navigate_to_waypoint",
        robot_id="r1",
    )
    assert reason.category == FailureCategory.NOT_CONFIGURED
    assert reason.retryable is False


def test_local_failure_reason_from_robot_result_cancelled() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="cancelled",
        error=None,
        action="victim_search",
        robot_id="r1",
    )
    assert reason.category == FailureCategory.CANCELLED


def test_local_failure_reason_from_robot_result_transport() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="failed",
        error="Transport connection refused",
        action="navigate_to_waypoint",
        robot_id="r1",
    )
    assert reason.category == FailureCategory.TRANSPORT
    assert reason.retryable is True


def test_local_failure_reason_from_robot_result_unknown() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="failed",
        error=None,
        action="inspect_casualty",
        robot_id="r1",
    )
    assert reason.category == FailureCategory.ACTION_FAILED


def test_local_failure_reason_recognizes_navigation_no_path() -> None:
    reason = LocalFailureReason.from_robot_result(
        status="failed",
        error="Navigation failed: no path to target",
        action="navigate_to_waypoint",
        robot_id="r1",
    )

    assert reason.category == FailureCategory.TARGET_UNREACHABLE
    assert reason.retryable is False


def test_local_failure_reason_frozen() -> None:
    reason = LocalFailureReason(
        category=FailureCategory.TIMEOUT,
        message="test",
        retryable=True,
    )
    try:
        reason.message = "changed"  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass
