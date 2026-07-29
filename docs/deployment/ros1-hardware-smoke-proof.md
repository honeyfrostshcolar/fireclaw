# ROS1 Hardware Smoke Proof Runbook

Repeatable, auditable procedure for proving FireClaw's ROS1 transport works
against a real robot. Follow every step in order. Do not skip the preflight.

This runbook is the authoritative companion to
[ros1-hardware-smoke-template.md](ros1-hardware-smoke-template.md) (copy-paste
checklist) and
[fireclaw-deployment-checklist.md](fireclaw-deployment-checklist.md) (full
deployment gates).

---

## 1. Prerequisites

### 1.1 ROS Master and Network

| Variable | Purpose | Example |
|----------|---------|---------|
| `ROS_MASTER_URI` | Points to the robot's roscore | `http://192.168.1.50:11311` |
| `ROS_IP` | Workstation IP reachable by the robot | `192.168.1.100` |
| `ROS_HOSTNAME` | Alternative to `ROS_IP` (DNS-resolvable) | `operator-laptop.local` |

Set before every session:

```bash
export ROS_MASTER_URI=http://<ROBOT_IP>:11311
export ROS_IP=<OPERATOR_WORKSTATION_IP>
```

Verify connectivity:

```bash
rostopic list          # should list robot topics
rosservice list        # should list robot services
rosnode list           # should list robot nodes
```

### 1.2 FireClaw Environment

```bash
export FIRECLAW_RUN_ROS1_SMOKE=1       # gates pytest smoke tests
export FIRECLAW_ROBOT_ID=<robot_id>    # must match ros1 config robot_id
```

### 1.3 Robot Namespace and Endpoint Mapping

FireClaw identifies endpoints by logical action name. The ROS1 adapter config
maps each logical name to a ROS topic, service, or action.

Built-in action names (must be present in config for a complete deployment):

| Logical Name | Typical Interface | Typical ROS Type |
|-------------|-------------------|------------------|
| `navigate_to_point` | action | `move_base_msgs/MoveBaseAction` |
| `search_for_victims` | action | custom action type |
| `assess_victim` | action | custom action type |
| `report_status` | topic or service | `std_msgs/String` or `std_srvs/Trigger` |
| `return_to_safe_zone` | action | `move_base_msgs/MoveBaseAction` |

Endpoint profiles (shorthand in config):

| Profile | Interface | Type | Feedback | Cancel |
|---------|-----------|------|----------|--------|
| `move_base` | action | `move_base_msgs/MoveBaseAction` | yes | yes |
| `trigger_service` | service | `std_srvs/Trigger` | no | no |
| `string_topic` | topic | `std_msgs/String` | no | no |

The `namespace` field in the config is prepended to all endpoint names at
resolution time. Example: namespace `/fireclaw/firebot_01` with endpoint name
`/navigate` resolves to `/fireclaw/firebot_01/navigate`.

### 1.4 Safety Observer

A human safety observer is **mandatory** for every hardware smoke run.

The observer must:

1. Stand within arm's reach of the physical emergency stop button.
2. Watch the robot at all times during testing.
3. Call "STOP" immediately if the robot behaves unexpectedly.
4. Confirm the robot has halted before the operator resumes.

---

## 2. Preflight (Dry Run)

Run the FireClaw doctor before touching the robot. This validates config
structure, endpoint coverage, and transport settings without sending any ROS
messages.

```bash
python -m fireclaw_core.doctor \
  --adapter ros1 \
  --ros1-config <config.yaml> \
  --task-queue memory/task-queue.jsonl \
  --memory-index memory/index.sqlite \
  --plugin-dir plugins/
```

Expected result: all checks `pass` or `warn`. Any `fail` must be resolved
before proceeding.

The doctor checks:

- Config loads without parse errors.
- `robot_id` is present.
- All built-in action names have endpoints.
- `emergency_stop` is configured.
- Action endpoints declare `feedback_supported` and `cancel_supported`.
- Transport `enabled` is `true` (when running in ros1 mode).
- Timeouts are positive.

To auto-fix stale task queue records:

```bash
python -m fireclaw_core.doctor \
  --adapter ros1 \
  --ros1-config <config.yaml> \
  --fix
```

### 2.1 Config Validation Checklist

Before connecting to the robot, manually verify:

- [ ] `transport.enabled` is `true`.
- [ ] `transport.wait_for_server_seconds` is long enough for the action server
      to start (default 5.0 s; increase for slow hardware).
- [ ] `transport.wait_for_result_seconds` is long enough for the longest
      action (default 30.0 s; navigation may need 120+ s).
- [ ] Every endpoint `name` uses the correct ROS namespace for this robot.
- [ ] `emergency_stop` topic/service exists on the robot.
- [ ] No credentials, IP addresses, or site-specific paths are hardcoded in
      the config (use environment variables or a local-only config file).

---

## 3. Emergency Stop Validation

**This must pass before any other hardware test.**

### 3.1 Software Emergency Stop

Send an emergency stop through the FireClaw transport:

```bash
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id <robot_id> \
  --command "emergency stop" \
  --adapter-config <config.yaml>
```

Alternatively, call the emergency stop endpoint directly via the gateway:

```bash
curl -X POST http://localhost:18080/tasks/<task_id>/emergency-stop \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Operator-Scopes: emergency.stop" \
  -H "Content-Type: application/json" \
  -d '{"reason": "smoke test estop"}'
```

Expected: robot stops all motion immediately. The safety observer confirms
the robot has halted.

### 3.2 Physical Emergency Stop

After the software estop passes:

1. The safety observer presses the physical e-stop button.
2. Verify the robot enters a safe state (motors disengaged, position held).
3. Release the physical e-stop.
4. Run `doctor --fix` to clear any stale task state.
5. Do not proceed until the safety observer confirms the area is clear.

---

## 4. Topic Publish Proof

Verify the transport can publish a ROS message and the robot receives it.

```bash
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id <robot_id> \
  --command "report status" \
  --adapter-config <config.yaml>
```

Or run the dedicated topic test (requires turtlesim or equivalent):

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest \
  tests/test_ros1_smoke.py::test_ros1_topic_publish -v
```

Verification:

- [ ] `transport.execute()` returns `{"status": "succeeded"}`.
- [ ] `rostopic echo <topic_name>` shows the published message.
- [ ] Robot reacts to the message (if applicable).

---

## 5. Service Request Proof

Verify the transport can call a ROS service and receive a response.

```bash
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id <robot_id> \
  --command "emergency stop" \
  --adapter-config <config.yaml>
```

Or run the dedicated service test:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest \
  tests/test_ros1_smoke.py::test_ros1_service_call -v
```

Verification:

- [ ] `transport.execute()` returns `{"status": "succeeded", "response": {...}}`.
- [ ] The response object matches the expected service return type.
- [ ] The robot acted on the service call (if applicable).

---

## 6. Action Goal / Feedback / Cancel Proof

This is the most complex smoke test. It exercises the full action lifecycle:
server connection, goal sending, feedback monitoring, result retrieval, and
cancellation.

### 6.1 Action Goal and Feedback

```bash
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id <robot_id> \
  --command "navigate to floor 2" \
  --adapter-config <config.yaml>
```

Or run the dedicated action test:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest \
  tests/test_ros1_smoke.py::test_ros1_action_goal -v
```

Verification:

- [ ] `transport.execute()` returns `{"status": "succeeded", "response": ...}`.
- [ ] Action server received the goal (`rostopic echo` on the goal topic).
- [ ] Feedback events were delivered to the `feedback_sink` callback.
- [ ] Robot performed the action (navigation, search, etc.).

### 6.2 Action Cancel

Send a goal, then cancel it mid-execution:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest \
  tests/test_ros1_smoke.py::test_ros1_action_cancel -v
```

Or manually:

1. Submit a long-running action (e.g., navigate to a distant floor).
2. While the action is running, call `transport.cancel_active_action()`.
3. Verify the robot stops.

Verification:

- [ ] `transport.execute()` returns `{"status": "cancelled"}`.
- [ ] The robot stopped the in-progress action.
- [ ] No stale action state remains (check `active_action_client` is `None`).

### 6.3 Action Timeout

Verify the transport handles an unresponsive action server correctly:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest \
  tests/test_ros1_smoke.py::test_ros1_action_timeout -v
```

Verification:

- [ ] `transport.execute()` returns `{"status": "timeout"}` after
      `wait_for_result_seconds` elapses.
- [ ] No crash or hang.

---

## 7. Full Smoke Suite

After individual proofs pass, run the complete suite:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -v
```

Expected: all tests pass. The default suite (without `FIRECLAW_RUN_ROS1_SMOKE`)
must continue to skip these tests so CI never requires a live robot.

---

## 8. Artifact Collection

After a successful smoke run, create a proof bundle that collects all artifacts
with automatic secret redaction:

```bash
python -m fireclaw_core.ros1_proof_bundle \
  --output-dir results/ros1-proof-$(date +%Y%m%d) \
  --robot-id <robot_id> \
  --environment sim \
  --doctor-report <doctor-output.json> \
  --smoke-artifacts <smoke-artifacts.jsonl> \
  --notes "optional operator notes"
```

The bundle writes:
- `summary.json` — Redacted bundle summary (robot_id, environment, timestamps, pass/fail counts)
- `doctor-report.json` — Full doctor check results (redacted)
- `ros1-smoke-artifacts.json` — All smoke test artifacts (redacted)
- `README.md` — Human-readable bundle overview

All string values are recursively redacted of secrets (API keys, tokens, passwords)
before writing. The bundle directory is safe to commit or share.

Collect these additional artifacts for the deployment record:

| Artifact | Source | Keep? |
|----------|--------|-------|
| Smoke test result JSON | `docs/deployment/ros1-hardware-smoke-artifact.schema.json` instance | Yes (gitignored copy) |
| Doctor output | stdout capture | Yes |
| Event log tail | `memory/fireclaw-events.jsonl` (last 100 lines) | Yes (redacted) |
| Task queue snapshot | `memory/task-queue.jsonl` (terminal records only) | Yes (redacted) |
| Config used | Copy of the config file | Yes (without secrets) |
| rostopic echo output | Terminal capture | Optional |
| Screenshots / video | Robot behavior | Optional |

### What NOT to Commit

The following must never appear in git:

- Robot IP addresses or hostnames (`ROS_MASTER_URI`, `ROS_IP`).
- API tokens or bearer credentials.
- Site-specific floor plans, maps, or building layouts.
- Raw sensor data (camera feeds, LIDAR scans).
- Operator personal information.
- Network topology details beyond what is needed to reproduce the test.

Use the artifact schema (see below) to record results without including secrets.

---

## 9. Artifact Schema

Smoke test results should be recorded as JSON conforming to
[`ros1-hardware-smoke-artifact.schema.json`](ros1-hardware-smoke-artifact.schema.json).

Example:

```json
{
  "robot_id": "firebot_01",
  "timestamp": "2026-06-10T14:30:00Z",
  "tests": [
    {
      "test_type": "emergency_stop",
      "endpoint_name": "/fireclaw/firebot_01/emergency_stop",
      "interface": "service",
      "passed": true,
      "duration_ms": 45,
      "notes": "Robot halted within 0.5 s"
    },
    {
      "test_type": "topic_publish",
      "endpoint_name": "/fireclaw/firebot_01/status",
      "interface": "topic",
      "passed": true,
      "duration_ms": 12
    },
    {
      "test_type": "action_goal",
      "endpoint_name": "/fireclaw/firebot_01/navigate",
      "interface": "action",
      "passed": true,
      "duration_ms": 8500,
      "notes": "Reached floor 2, 3 feedback events received"
    },
    {
      "test_type": "action_cancel",
      "endpoint_name": "/fireclaw/firebot_01/navigate",
      "interface": "action",
      "passed": true,
      "duration_ms": 2100,
      "notes": "Cancelled mid-navigation, robot stopped cleanly"
    }
  ],
  "overall_passed": true,
  "operator_notes": "First hardware smoke on firebot_01. All checks clean.",
  "fireclaw_version": "0.1.0",
  "ros_distro": "noetic"
}
```

---

## 10. Rollback Procedure

If any smoke test fails or the robot behaves unexpectedly:

1. **Stop the robot.** Physical e-stop or `Ctrl+C` in the operator terminal.
2. **Clear stale state.**
   ```bash
   python -m fireclaw_core.doctor --adapter ros1 --ros1-config <config.yaml> --fix
   ```
3. **Revert config** to the last known-good version:
   ```bash
   git checkout HEAD~1 -- <config.yaml>
   ```
4. **Verify doctor passes** on the reverted config:
   ```bash
   python -m fireclaw_core.doctor --adapter ros1 --ros1-config <config.yaml>
   ```
5. **Do not resume** until the safety observer confirms the area is clear.
6. **Re-run the smoke test** only after the root cause is understood.

---

## 11. Quick Reference

```bash
# Full smoke run (copy-paste, replace placeholders)
export ROS_MASTER_URI=http://<ROBOT_IP>:11311
export ROS_IP=<OPERATOR_IP>
export FIRECLAW_RUN_ROS1_SMOKE=1
export FIRECLAW_ROBOT_ID=<robot_id>

# Preflight
python -m fireclaw_core.doctor --adapter ros1 --ros1-config <config.yaml>

# Individual proofs
python -m fireclaw_core.mission_cli submit-subtask --robot-id <robot_id> --command "emergency stop" --adapter-config <config.yaml>
python -m fireclaw_core.mission_cli submit-subtask --robot-id <robot_id> --command "report status" --adapter-config <config.yaml>
python -m fireclaw_core.mission_cli submit-subtask --robot-id <robot_id> --command "navigate to floor 2" --adapter-config <config.yaml>

# Full suite
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -v

# Post-run cleanup
python -m fireclaw_core.doctor --adapter ros1 --ros1-config <config.yaml> --fix

# Create proof bundle (redacted artifacts)
python -m fireclaw_core.ros1_proof_bundle \
  --output-dir results/ros1-proof-$(date +%Y%m%d) \
  --robot-id <robot_id> \
  --environment sim \
  --doctor-report <doctor-output.json> \
  --smoke-artifacts <smoke-artifacts.jsonl>
```
