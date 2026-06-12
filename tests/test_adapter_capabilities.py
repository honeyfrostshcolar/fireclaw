"""Phase 2 tests: adapter capability declarations and simulator/real-robot separation."""
from __future__ import annotations

from fireclaw_core.agent.robot import (
    ALL_ROBOT_ACTIONS,
    AdapterCapabilities,
    DryRunRobotAdapter,
    MockRos1RobotAdapter,
    MockRos2RobotAdapter,
    SimulatorRobotAdapter,
    validate_simulator_real_separation,
)


def test_dry_run_adapter_capabilities() -> None:
    adapter = DryRunRobotAdapter(robot_id="r1")
    caps = adapter.capabilities()

    assert isinstance(caps, AdapterCapabilities)
    assert caps.supported_actions == ALL_ROBOT_ACTIONS
    assert caps.supports_dry_run is True
    assert caps.supports_real_execution is False
    assert caps.is_simulator is False
    assert caps.supports_feedback is False


def test_mock_ros1_adapter_capabilities() -> None:
    adapter = MockRos1RobotAdapter(robot_id="r1")
    caps = adapter.capabilities()

    assert caps.supports_feedback is True
    assert caps.supports_cancellation is True
    assert caps.supports_real_execution is False
    assert caps.is_simulator is False


def test_mock_ros2_adapter_capabilities() -> None:
    adapter = MockRos2RobotAdapter(robot_id="r1")
    caps = adapter.capabilities()

    assert caps.supported_modes == {"mock_ros2"}
    assert caps.supports_real_execution is False
    assert caps.is_simulator is False


def test_simulator_adapter_capabilities() -> None:
    adapter = SimulatorRobotAdapter(robot_id="r1")
    caps = adapter.capabilities()

    assert caps.is_simulator is True
    assert caps.supports_real_execution is False
    assert caps.supported_modes == {"simulator"}


def test_simulator_must_not_execute_real() -> None:
    error = validate_simulator_real_separation("simulator", dry_run=False, action="navigate_to_floor")
    assert error is not None
    assert "Simulator" in error


def test_simulator_dry_run_is_valid() -> None:
    error = validate_simulator_real_separation("simulator", dry_run=True, action="navigate_to_floor")
    assert error is None


def test_real_adapter_requires_allow_real() -> None:
    error = validate_simulator_real_separation("ros1", dry_run=False, action="navigate_to_floor", allow_real=False)
    assert error is not None
    assert "allow_real_robot" in error


def test_real_adapter_with_allow_real_is_valid() -> None:
    error = validate_simulator_real_separation("ros1", dry_run=False, action="navigate_to_floor", allow_real=True)
    assert error is None


def test_real_adapter_dry_run_is_valid() -> None:
    error = validate_simulator_real_separation("ros1", dry_run=True, action="navigate_to_floor")
    assert error is None


def test_all_adapters_declare_all_actions() -> None:
    adapters = [
        DryRunRobotAdapter(robot_id="r1"),
        MockRos1RobotAdapter(robot_id="r1"),
        MockRos2RobotAdapter(robot_id="r1"),
        SimulatorRobotAdapter(robot_id="r1"),
    ]
    for adapter in adapters:
        caps = adapter.capabilities()
        assert caps.supported_actions == ALL_ROBOT_ACTIONS, f"{adapter.__class__.__name__} missing actions"
