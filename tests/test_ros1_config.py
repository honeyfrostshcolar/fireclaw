from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.ros.ros1_config import load_ros1_adapter_config


def test_load_ros1_adapter_config_parses_core_transport_and_emergency_stop(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "fireclaw-01",
                "namespace": "/fireclaw/fireclaw-01",
                "emergency_stop": {
                    "interface": "service",
                    "name": "/fireclaw/fireclaw-01/emergency_stop",
                    "type": "std_srvs/Trigger",
                },
                "transport": {
                    "enabled": True,
                    "wait_for_server_seconds": 2.5,
                    "wait_for_result_seconds": 12.5,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_ros1_adapter_config(config_path)

    assert config.robot_id == "fireclaw-01"
    assert config.namespace == "/fireclaw/fireclaw-01"
    assert config.emergency_stop is not None
    assert config.emergency_stop.interface == "service"
    assert config.emergency_stop.name == (
        "/fireclaw/fireclaw-01/emergency_stop"
    )
    assert config.transport.enabled is True
    assert config.transport.wait_for_server_seconds == 2.5
    assert config.transport.wait_for_result_seconds == 12.5
    assert not hasattr(config, "endpoints")


@pytest.mark.parametrize("field", ["endpoints", "remap", "targets"])
def test_ros1_adapter_config_rejects_plugin_owned_domain_fields(
    tmp_path: Path,
    field: str,
) -> None:
    config_path = tmp_path / f"ros1-{field}.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "fireclaw-01",
                field: {"navigate_to_point": {"name": "/move_base"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="configure runtime endpoints in the owning Plugin",
    ):
        load_ros1_adapter_config(config_path)


def test_load_ros1_adapter_config_rejects_invalid_emergency_interface(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "fireclaw-01",
                "emergency_stop": {
                    "interface": "socket",
                    "name": "/fireclaw/emergency_stop",
                    "type": "std_srvs/Trigger",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="emergency_stop.interface"):
        load_ros1_adapter_config(config_path)


def test_load_ros1_adapter_config_parses_yaml_diagnostics_policy(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: fireclaw-01
namespace: /fireclaw/fireclaw-01
diagnostics:
  enabled: true
  topic_allowlist: /scan, /odom
  frame_allowlist: map, base_link
  action_allowlist: /move_base
  max_topics: 25
  max_samples: 2
  max_timeout_seconds: 1.5
  max_output_bytes: 8192
""".lstrip(),
        encoding="utf-8",
    )

    config = load_ros1_adapter_config(config_path)

    assert config.diagnostics.enabled is True
    assert config.diagnostics.topic_allowlist == ("/scan", "/odom")
    assert config.diagnostics.frame_allowlist == ("map", "base_link")
    assert config.diagnostics.action_allowlist == ("/move_base",)
    assert config.diagnostics.max_topics == 25
    assert config.diagnostics.max_samples == 2
    assert config.diagnostics.max_timeout_seconds == 1.5
    assert config.diagnostics.max_output_bytes == 8192


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"transport": {"wait_for_server_seconds": -1}},
            "wait_for_server_seconds must be non-negative",
        ),
        (
            {"transport": {"wait_for_result_seconds": -1}},
            "wait_for_result_seconds must be non-negative",
        ),
        (
            {"diagnostics": {"max_topics": 0}},
            "max_topics must be between 1 and 1000",
        ),
        (
            {"diagnostics": {"max_samples": 11}},
            "max_samples must be between 1 and 10",
        ),
        (
            {"diagnostics": {"max_timeout_seconds": 31}},
            "max_timeout_seconds must be between 0.1 and 30",
        ),
        (
            {"diagnostics": {"max_output_bytes": 512}},
            "max_output_bytes must be between 1024 and 1000000",
        ),
    ],
)
def test_ros1_adapter_config_rejects_invalid_core_limits(
    tmp_path: Path,
    payload: dict,
    message: str,
) -> None:
    config_path = tmp_path / "invalid.json"
    config_path.write_text(
        json.dumps({"robot_id": "fireclaw-01", **payload}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_ros1_adapter_config(config_path)


def test_load_gazebo_turtlebot3_adapter_config_has_no_domain_routing() -> None:
    config_path = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "ros1_configs"
        / "gazebo_turtlebot3_move_base.yaml"
    )

    config = load_ros1_adapter_config(config_path)

    assert config.robot_id == "gazebo_turtlebot3"
    assert config.transport.enabled is True
    assert config.transport.wait_for_server_seconds == 10.0
    assert config.transport.wait_for_result_seconds == 120.0
    assert config.emergency_stop is not None
    assert config.emergency_stop.name == "/fireclaw/emergency_stop"
    assert not hasattr(config, "endpoints")
