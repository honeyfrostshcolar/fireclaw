# Config / Doctor v1 Design

## Goal

Add a local diagnostic command that tells the user whether FireClaw is still running as a mock framework or is configured for a safer next integration step.

## Scope

Doctor v1 does not connect to ROS1, inspect a live ROS master, install dependencies, or fix configuration automatically. It produces a structured report that catches obvious readiness problems before real ROS1 adapter work.

## Checks

Doctor v1 checks:

- adapter mode and whether it is mock/dry-run/simulator;
- memory and event ledger parent paths are writable;
- workspace skill manifests load without errors;
- robot adapter exposes `emergency_stop(...)`;
- action feedback boundary is present through `RobotAdapterActionBackend`;
- mock ROS1 adapter is not mistaken for a real robot adapter.

## Output

The report is JSON-ready:

```json
{
  "status": "warn",
  "checks": [
    {
      "name": "adapter.mock_ros1",
      "status": "warn",
      "message": "mock-ros1 is a test double and does not import rospy",
      "details": {}
    }
  ]
}
```

Top-level status is the worst check status in `pass < warn < fail`.

