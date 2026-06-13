"""Regression tests: gateway must propagate dry_run to the ROS1 adapter.

Task 1 from the profile-driven runtime plan.  These tests prove that a
GatewayConfig loaded from a robot profile correctly sets ``robot.dry_run``
on the underlying adapter — both for dry-run mode and live mode.
"""
from __future__ import annotations

from pathlib import Path

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig

ROS1_CONFIG_YAML = "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml"


def _write_ros1_profile(tmp_path: Path, *, dry_run: bool) -> Path:
    """Write a minimal ROS1 robot profile TOML to *tmp_path* and return its path."""
    profile = tmp_path / "robot.toml"
    profile.write_text(
        f"""
[robot]
id = "test-ros1-robot"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "{ROS1_CONFIG_YAML}"
data_dir = "{tmp_path / 'data'}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )
    return profile


def test_profile_gateway_applies_dry_run_to_ros1_adapter(tmp_path: Path) -> None:
    """Gateway with dry_run=True must set robot.dry_run on the ROS1 adapter."""
    profile_path = _write_ros1_profile(tmp_path, dry_run=True)

    gateway = FireClawGateway(
        GatewayConfig(
            port=0,
            dry_run=True,
            robot_profile_path=str(profile_path),
            memory_path=str(tmp_path / "mem.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        ),
    )

    assert gateway.robot.dry_run is True, (
        f"Expected robot.dry_run=True, got {gateway.robot.dry_run}"
    )


def test_profile_gateway_real_run_keeps_ros1_adapter_live(tmp_path: Path) -> None:
    """Gateway with dry_run=False must NOT force the adapter into dry-run."""
    profile_path = _write_ros1_profile(tmp_path, dry_run=False)

    gateway = FireClawGateway(
        GatewayConfig(
            port=0,
            dry_run=False,
            robot_profile_path=str(profile_path),
            memory_path=str(tmp_path / "mem.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        ),
    )

    assert gateway.robot.dry_run is False, (
        f"Expected robot.dry_run=False, got {gateway.robot.dry_run}"
    )
