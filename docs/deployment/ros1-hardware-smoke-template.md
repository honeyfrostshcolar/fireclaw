# ROS1 Hardware Smoke Test Template

Use this template when validating FireClaw against a real ROS1 robot for the
first time, or after a significant configuration or adapter change.

Copy the checklist below and fill in the blanks for your specific robot.

---

## Prerequisites

- [ ] Ubuntu 20.04 (Noetic) installed on the operator workstation
- [ ] `ros-noetic-desktop-full`, `ros-noetic-rospy`, `ros-noetic-actionlib` installed
- [ ] FireClaw installed (`pip install -e .` from repo root)
- [ ] Real robot powered on and connected to the ROS master
- [ ] `turtlesim` or equivalent low-risk interface available for initial validation
- [ ] Safety observer present (human who can hit the physical e-stop)

## Environment Variables

Set these before running any smoke commands:

```bash
export ROS_MASTER_URI=http://<ROBOT_IP>:11311
export ROS_IP=<OPERATOR_WORKSTATION_IP>
export FIRECLAW_RUN_ROS1_SMOKE=1       # enables smoke test marker
export FIRECLAW_ROBOT_ID=<robot_id>    # must match ros1 config robot_id
```

## Safety Observer Role

The safety observer must:

1. Stand within arm's reach of the physical emergency stop button.
2. Watch the robot at all times during smoke testing.
3. Call "STOP" immediately if the robot behaves unexpectedly.
4. Confirm the robot has halted before the operator resumes testing.

**Emergency stop procedure:**

1. Hit the physical e-stop on the robot.
2. In the operator terminal, press `Ctrl+C` to interrupt FireClaw.
3. Run `python -m fireclaw_core.doctor --adapter ros1 --ros1-config <config> --fix` to clear any stale task state.
4. Inspect `memory/fireclaw-events.jsonl` for the last event before the stop.
5. Do not resume until the safety observer confirms the area is clear.

## Topics / Services / Actions to Test

Copy and fill in for your robot:

| # | Interface | ROS Name | Type | Expected Behavior | Pass? |
|---|-----------|----------|------|-------------------|-------|
| 1 | topic | `/turtle1/cmd_vel` | `geometry_msgs/Twist` | Robot moves forward | [ ] |
| 2 | service | `/fireclaw/<robot_id>/emergency_stop` | `std_srvs/Trigger` | Robot stops immediately | [ ] |
| 3 | action | `/fireclaw/<robot_id>/navigation` | `fireclaw_msgs/NavigateFloorAction` | Robot reaches target floor, feedback received | [ ] |
| 4 | action | `/fireclaw/<robot_id>/search` | `fireclaw_msgs/SearchAction` | Robot searches area, result returned | [ ] |
| 5 | action (cancel) | (same as #3) | (same) | Cancel mid-action, robot stops cleanly | [ ] |

## Smoke Test Commands

```bash
# 1. Doctor check (dry run)
python -m fireclaw_core.doctor \
  --adapter ros1 \
  --ros1-config <config.yaml> \
  --task-queue memory/task-queue.jsonl \
  --memory-index memory/index.sqlite \
  --plugin-dir plugins/

# 2. Doctor check with fix
python -m fireclaw_core.doctor \
  --adapter ros1 \
  --ros1-config <config.yaml> \
  --task-queue memory/task-queue.jsonl \
  --fix

# 3. Single topic publish (turtlesim)
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id <robot_id> \
  --command "move forward" \
  --adapter-config <config.yaml>

# 4. Single action goal
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id <robot_id> \
  --command "navigate to floor 2" \
  --adapter-config <config.yaml>

# 5. Full pytest smoke suite
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -v
```

## Rollback Procedure

If the smoke test fails or the robot behaves unexpectedly:

1. Stop the robot (physical e-stop or `Ctrl+C`).
2. Run `doctor --fix` to clear stale task queue records.
3. Revert the FireClaw config to the last known-good version:
   ```bash
   git checkout HEAD~1 -- <config.yaml>
   ```
4. Verify the doctor passes cleanly:
   ```bash
   python -m fireclaw_core.doctor --adapter ros1 --ros1-config <config.yaml>
   ```
5. Re-run the smoke test only after the safety observer confirms it is safe.

## Logging Paths

| Log | Path | Contents |
|-----|------|----------|
| Task events | `memory/fireclaw-events.jsonl` | Every event emitted during task execution |
| Task queue | `memory/task-queue.jsonl` | Durable task queue records |
| Memory index | `memory/index.sqlite` | FTS5 full-text search index |
| ROS1 adapter log | `logs/ros1-adapter.log` | Transport-level debug output (if configured) |

## Checklist

Copy this block into your deployment notes:

```
ROS1 Hardware Smoke Test — <robot_id> — <date>

Environment:
- ROS_MASTER_URI: _______________
- FIRECLAW_ROBOT_ID: _______________
- Config file: _______________
- Safety observer: _______________

Pre-flight:
- [ ] Doctor passes (no fail, no warn on critical checks)
- [ ] roscore running
- [ ] Robot driver node running
- [ ] Physical e-stop accessible

Smoke tests:
- [ ] Topic publish (cmd_vel) — robot moves
- [ ] Emergency stop service — robot stops
- [ ] Action goal (navigation) — robot navigates, feedback received
- [ ] Action cancel — robot stops mid-action
- [ ] Doctor --fix — stale records cleared

Post-flight:
- [ ] All task queue records terminal
- [ ] Event log clean (no unexpected errors)
- [ ] Robot in safe position

Result: PASS / FAIL
Notes: _______________
```
