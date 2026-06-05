# Skill / Action Remap YAML v1 Design

## Goal

Expose FireClaw's skill/action to ROS1 endpoint mapping as a user-editable YAML/JSON file so future robot capabilities can be added without changing FireClaw core code.

## Design

The ROS1 config file keeps the current JSON shape, and adds a YAML-friendly `remap` alias for `endpoints`. Both names load into the same `Ros1AdapterConfig.endpoints` structure.

Remap entries can refer to built-in actions or future workspace skill names. The parser no longer rejects unknown action names. Doctor still distinguishes built-in actions from custom actions when reporting coverage.

The config supports standard profiles for common ROS1 types:

- `move_base`: expands to `interface=action`, `type=move_base_msgs/MoveBaseAction`, `cancel_supported=true`, `feedback_supported=true`
- `trigger_service`: expands to `interface=service`, `type=std_srvs/Trigger`
- `string_topic`: expands to `interface=topic`, `type=std_msgs/String`

Users may override any expanded field explicitly.

## YAML Shape

```yaml
robot_id: fireclaw-01
namespace: /fireclaw/fireclaw-01

remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
    goal_template:
      target_pose:
        header:
          frame_id: map
        pose:
          position:
            x: "{{ targets.floor_${floor}.x }}"
            y: "{{ targets.floor_${floor}.y }}"
            z: 0.0
          orientation:
            yaw: "{{ targets.floor_${floor}.yaw }}"

  emergency_stop:
    profile: trigger_service
    name: /fireclaw/emergency_stop

targets:
  floor_2:
    frame_id: map
    x: 12.4
    y: -3.8
    yaw: 1.57
```

## Parser Boundary

FireClaw remains dependency-free. `.json` files use `json.loads`. `.yaml` and `.yml` files use a small strict YAML subset parser supporting:

- nested mappings by indentation;
- string, integer, float, and boolean scalar values;
- comments and blank lines.

It does not support YAML lists, anchors, multiline strings, or arbitrary YAML tags. Those can be added later by switching to PyYAML behind the same loader API.

## Runtime Behavior

`Ros1RobotAdapter` records:

- endpoint interface/name/type;
- `goal_template`;
- `request_template`;
- target map;
- profile name.

It still returns `not_configured` for live action calls because transport is not implemented yet.

## Doctor Behavior

Doctor reports:

- configured built-in actions;
- configured custom actions;
- missing built-in actions;
- workspace skills missing remap bindings;
- unknown remap entries that are not built-in and not loaded workspace skills.

Unknown remap entries are `warn`, not `fail`, because a user may create the remap before adding the skill manifest.
