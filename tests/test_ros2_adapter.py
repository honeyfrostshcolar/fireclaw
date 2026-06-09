"""Tests for ROS2 adapter protocol boundary."""
from __future__ import annotations

from typing import Any

from fireclaw_core.ros2_adapter import Ros2AdapterProtocol


class MinimalRos2Adapter:
    """Minimal implementation to verify protocol is satisfiable."""

    robot_id: str = "test_robot"
    mode: str = "ros2"
    dry_run: bool = True

    def navigate_to_floor(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True, "floor": floor}

    def search_for_victims(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True, "victims": 0}

    def assess_victim(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True}

    def report_status(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True}

    def return_to_safe_zone(self, **kwargs: Any) -> Any:
        return {"ok": True}

    def emergency_stop(self, reason: str | None = None, **kwargs: Any) -> Any:
        return {"ok": True, "reason": reason}

    def get_robot_state(self) -> Any:
        return {"robot_id": self.robot_id, "mode": self.mode}

    def get_environment_state(self) -> Any:
        return {}

    def capabilities(self) -> Any:
        return {"supported_modes": {"ros2"}}

    def init_node(self, node_name: str) -> None:
        pass

    def shutdown_node(self) -> None:
        pass


def _check_protocol(adapter: Ros2AdapterProtocol) -> Ros2AdapterProtocol:
    """Type-check: adapter satisfies protocol at runtime."""
    return adapter


def test_ros2_protocol_is_implementable():
    adapter = MinimalRos2Adapter()
    checked = _check_protocol(adapter)
    assert checked.mode == "ros2"
    assert checked.robot_id == "test_robot"


def test_ros2_protocol_has_required_methods():
    adapter = MinimalRos2Adapter()
    assert adapter.init_node("test") is None
    assert adapter.shutdown_node() is None
    assert adapter.navigate_to_floor(2) == {"ok": True, "floor": 2}
    assert adapter.emergency_stop("test") == {"ok": True, "reason": "test"}


def test_ros2_protocol_docstring_exists():
    assert Ros2AdapterProtocol.__doc__ is not None
    assert "ROS2" in Ros2AdapterProtocol.__doc__
