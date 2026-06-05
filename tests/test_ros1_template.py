import pytest

from fireclaw_core.ros1_template import render_ros1_template


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


def test_render_ros1_template_raises_for_missing_reference():
    with pytest.raises(ValueError, match="targets.floor_3.x"):
        render_ros1_template(
            {"x": "{{ targets.floor_${floor}.x }}"},
            inputs={"floor": 3},
            targets={"floor_2": {"x": 12.4}},
        )
