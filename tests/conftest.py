from __future__ import annotations

import os
import shutil

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_ros1 = os.environ.get("FIRECLAW_RUN_ROS1_SMOKE") == "1"
    ros_available = shutil.which("roscore") is not None and shutil.which("rosrun") is not None
    skip_ros1 = pytest.mark.skip(
        reason=(
            "ROS1 smoke tests require FIRECLAW_RUN_ROS1_SMOKE=1 "
            "and ROS1 commands (roscore, rosrun) on PATH."
        )
    )
    for item in items:
        if "ros1_smoke" in item.keywords and not (run_ros1 and ros_available):
            item.add_marker(skip_ros1)
