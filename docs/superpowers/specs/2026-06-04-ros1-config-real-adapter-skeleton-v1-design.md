# ROS1 Config / Real Adapter Skeleton v1 Design

## Goal

Define FireClaw's first real ROS1 adapter configuration boundary without importing `rospy`, `actionlib`, or controlling hardware.

## OpenClaw Analogue

OpenClaw keeps gateway and relay settings as structured configuration objects and validates request/config payloads before use. FireClaw reuses that shape: load a typed config, validate it early, and make runtime behavior explicit when a backend is not yet connected.

## Scope

This version adds:

- a dependency-free JSON config loader for ROS1 endpoints;
- a `ros1` adapter choice that creates a real-adapter skeleton from config;
- explicit endpoint metadata for topic, service, and actionlib-style actions;
- adapter results that say `not_configured` until live ROS1 transport exists;
- doctor checks that report ROS1 config readiness.

This version does not add:

- `rospy` or `actionlib` imports;
- live ROS master checks;
- message serialization;
- real topic/service/action calls;
- hardware emergency-stop wiring.

## Config Shape

The config file is JSON:

```json
{
  "robot_id": "fireclaw-01",
  "namespace": "/fireclaw/fireclaw-01",
  "endpoints": {
    "navigate_to_floor": {
      "interface": "action",
      "name": "/fireclaw/fireclaw-01/navigation",
      "type": "fireclaw_msgs/NavigateFloorAction",
      "cancel_supported": true,
      "feedback_supported": true
    },
    "search_for_victims": {
      "interface": "service",
      "name": "/fireclaw/fireclaw-01/search_victims",
      "type": "fireclaw_msgs/SearchVictims"
    }
  },
  "emergency_stop": {
    "interface": "service",
    "name": "/fireclaw/fireclaw-01/emergency_stop",
    "type": "std_srvs/Trigger"
  },
  "timeouts": {
    "default_seconds": 30.0
  }
}
```

Known action names are the built-in robot action boundary:

- `navigate_to_floor`
- `search_for_victims`
- `assess_victim`
- `report_status`
- `return_to_safe_zone`

## Runtime Behavior

`create_robot_adapter("ros1", robot_id, config_path=...)` loads the config and returns `Ros1RobotAdapter`.

The adapter exposes the same `RobotAdapter` methods as mock and dry-run adapters. Each action records a `Ros1CommandSpec` derived from the endpoint config, then returns:

- `ok=False`
- `status="not_configured"`
- `mode="ros1"`
- `dry_run=False`
- `error` explaining that live ROS1 transport is not implemented yet

This prevents FireClaw from silently treating a configured ROS1 adapter as a working hardware driver.

## Doctor Behavior

`python -m fireclaw_core.doctor --adapter ros1 --ros1-config <path>` checks:

- config file can be loaded;
- built-in action endpoints are present or reported as warnings;
- endpoint interface/name/type fields are valid;
- emergency stop endpoint exists;
- feedback and cancellation support are declared for action endpoints.

Doctor may return `warn` for incomplete but parseable configs, and `fail` for missing/unloadable/invalid config files.

## Testing

Tests use only local JSON files and the existing Python test runner. They verify:

- valid config loads into typed objects;
- invalid endpoint definitions fail with clear errors;
- runtime config can create `Ros1RobotAdapter` without ROS packages installed;
- adapter action calls return explicit `not_configured` results;
- doctor reports ROS1 config readiness.
