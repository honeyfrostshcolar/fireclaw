from __future__ import annotations

from fireclaw_core.ros.gazebo_smoke import build_parser, build_robot_registry_payload


def test_gazebo_smoke_parser_accepts_robot_agent_args():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--robot-id",
            "gazebo_turtlebot3",
            "--robot-gateway-url",
            "http://127.0.0.1:8765",
            "--mission-gateway-url",
            "http://127.0.0.1:8766",
            "--command",
            "去坐标 (2.0, 1.5) 救人",
            "--output-dir",
            "results/gazebo-smoke/local",
        ]
    )

    assert args.robot_id == "gazebo_turtlebot3"
    assert args.command == "去坐标 (2.0, 1.5) 救人"


def test_build_robot_registry_payload_points_to_robot_gateway():
    payload = build_robot_registry_payload(
        robot_id="gazebo_turtlebot3",
        base_url="http://127.0.0.1:8765",
    )

    assert payload["robots"][0]["robot_id"] == "gazebo_turtlebot3"
    assert payload["robots"][0]["base_url"] == "http://127.0.0.1:8765"
    assert "navigate_to_point" in payload["robots"][0]["capabilities"]
    assert "search_for_victims" in payload["robots"][0]["capabilities"]
