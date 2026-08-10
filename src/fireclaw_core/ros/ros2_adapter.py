"""ROS2 adapter protocol boundary.

ROS2 is not yet supported. This module defines the protocol that future
ROS2 adapter implementations must satisfy. When a ROS2 environment becomes
available, implement this protocol with rclpy.
"""
from __future__ import annotations

from typing import Any, Protocol


class Ros2AdapterProtocol(Protocol):
    """Contract for ROS2 robot adapters.

    Mirrors RobotAdapter but adds ROS2-specific concerns:
    - Node lifecycle (init/shutdown)
    - QoS profile awareness
    - Lifecycle node support (managed nodes)

    Implementations should use rclpy for ROS2 communication.
    """

    robot_id: str
    mode: str  # "ros2"
    dry_run: bool

    def emergency_stop(self, reason: str | None = None, **kwargs: Any) -> Any:
        """Emergency stop robot."""
        ...

    def get_robot_state(self) -> Any:
        """Get current robot state snapshot."""
        ...

    def get_environment_state(self) -> Any:
        """Get current environment state snapshot."""
        ...

    def init_node(self, node_name: str) -> None:
        """Initialize rclpy node."""
        ...

    def shutdown_node(self) -> None:
        """Shutdown rclpy node."""
        ...
