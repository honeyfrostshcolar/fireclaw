import pytest

from fireclaw_core.ros.ros1_template import render_ros1_template


def test_render_ros1_template_resolves_inputs_and_targets():
    template = {
        "target_pose": {
            "header": {"frame_id": "{{ targets.floor_${floor}.frame_id }}"},
            "pose": {
                "position": {
                    "x": "{{ targets.floor_${floor}.x }}",
                    "y": "{{ targets.floor_${floor}.y }}",
                    "z": 0.0,
                },
                "orientation": {"yaw": "{{ targets.floor_${floor}.yaw }}"},
            },
        }
    }
    inputs = {"floor": 2}
    targets = {
        "floor_2": {
            "frame_id": "map",
            "x": 12.4,
            "y": -3.8,
            "yaw": 1.57,
        }
    }

    rendered = render_ros1_template(template, inputs=inputs, targets=targets)

    assert rendered["target_pose"]["header"]["frame_id"] == "map"
    assert rendered["target_pose"]["pose"]["position"]["x"] == 12.4
    assert rendered["target_pose"]["pose"]["position"]["y"] == -3.8
    assert rendered["target_pose"]["pose"]["orientation"]["yaw"] == 1.57


def test_render_ros1_template_resolves_dollar_expression_to_non_string_value():
    rendered = render_ros1_template(
        {
            "target_pose": {
                "pose": {
                    "position": "${targets.floor_${floor}.position}",
                    "orientation": "${targets.floor_${floor}.orientation}",
                }
            },
            "report": "status_report_floor_${floor}",
        },
        inputs={"floor": 2},
        targets={
            "floor_2": {
                "position": {"x": 2.0, "y": 0.0, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            }
        },
    )

    assert rendered["target_pose"]["pose"]["position"] == {"x": 2.0, "y": 0.0, "z": 0.0}
    assert rendered["target_pose"]["pose"]["orientation"] == {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
    assert rendered["report"] == "status_report_floor_2"


def test_render_ros1_template_raises_for_missing_reference():
    with pytest.raises(ValueError, match="targets.floor_3.x"):
        render_ros1_template(
            {"x": "{{ targets.floor_${floor}.x }}"},
            inputs={"floor": 3},
            targets={"floor_2": {"x": 12.4}},
        )
