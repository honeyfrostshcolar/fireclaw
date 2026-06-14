# ROS1 Gazebo Debugging Guide

## Overview

This guide walks through using FireClaw with ROS1 Noetic + Gazebo Classic 11 for high-fidelity robot simulation debugging. Gazebo provides a physics-based environment where FireClaw's ROS1 adapter can drive a simulated robot through real ROS topics, services, and actions.

**Important:** Gazebo validation proves FireClaw's ROS1 config works against a simulated robot. It does NOT validate real hardware. See `ros1-hardware-smoke-proof.md` for real robot validation.

## Prerequisites

- Ubuntu 20.04 (native, VM, or Docker with GUI/X11)
- ROS1 Noetic (`ros-noetic-desktop-full`)
- Gazebo Classic 11 (`ros-noetic-gazebo-ros-pkgs`)
- Navigation stack (`ros-noetic-navigation`)
- FireClaw installed (`pip install -e .`)

### Install Dependencies

```bash
sudo apt-get install -y \
  ros-noetic-navigation \
  ros-noetic-gazebo-ros-pkgs \
  ros-noetic-gazebo-ros-control
```

### Verify Installation

```bash
source /opt/ros/noetic/setup.bash
roscore --help
gazebo --version
rospack find gazebo_ros
rospack find move_base_msgs
rospack find actionlib
```

All commands should succeed without errors.

## Quick Start (TurtleBot3 + move_base)

### Step 1: Start ROS Core

```bash
source /opt/ros/noetic/setup.bash
roscore &
```

### Step 2: Launch TurtleBot3 in Gazebo

```bash
export TURTLEBOT3_MODEL=burger
roslaunch turtlebot3_gazebo turtlebot3_world.launch
```

The robot should spawn in Gazebo. Verify:

```bash
rostopic list | grep move_base
```

Expected output includes `/move_base/goal`, `/move_base/result`, `/move_base/status`, `/move_base/feedback`.

### Step 3: Launch Navigation Stack

```bash
roslaunch turtlebot3_navigation turtlebot3_navigation.launch map_file:=$HOME/map.yaml
```

Or use the default empty map for testing:

```bash
roslaunch turtlebot3_navigation turtlebot3_navigation.launch
```

### Step 4: Verify Robot Can Navigate

Before involving FireClaw, confirm the robot responds to manual navigation goals:

```bash
rostopic echo /odom -n 1
rostopic echo /scan -n 1
```

### Step 5: Run FireClaw Doctor

```bash
source /opt/ros/noetic/setup.bash
export ROS_MASTER_URI=http://localhost:11311
export ROS_IP=127.0.0.1

.venv/bin/python -m fireclaw_core.doctor \
  --adapter ros1 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml
```

### Step 6: Run FireClaw Direct Subtask

Start the robot-local gateway. The profile in `fireclaw.toml` drives identity, adapter, ROS config, and storage paths automatically. The default config is dry-run — no ROS transport commands are sent unless `--real-run` is passed:

```bash
# 启动 robot-local gateway（safe default: dry run, no ROS transport execution）
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml

# 当 ROS/Gazebo 环境就绪后，使用 --real-run 发送实际 transport 命令
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --real-run
```

In a separate terminal, start the mission gateway (robot registry is built from profiles listed in `[mission].robot_profiles`):

```bash
# 启动 mission gateway（从 profile 自动构建 robot registry）
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
```

The Gazebo robot should move toward the target position.

### Robot-Local Agent Mode

Start the robot-local gateway and mission gateway from the profile-backed config:

```bash
# Terminal 1: robot-local gateway
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml

# Terminal 2: mission gateway
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
```

Submit commands through `fireclaw_core mission`. No manual `robots.json` export is needed.

### Step 7: Run Full Embodied Eval

```bash
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/gazebo_rescue_scenarios.json \
  --output-dir results/embodied-eval/gazebo-local \
  --adapter ros1 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml
```

### Step 8: Generate Proof Bundle

```bash
.venv/bin/python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/gazebo-local \
  --run-id gazebo-local \
  --mission-trace results/embodied-eval/gazebo-local/mission-trace.json \
  --mission-events results/embodied-eval/gazebo-local/mission-events.json \
  --task-flow results/embodied-eval/gazebo-local/task-flow.json \
  --session-lineage results/embodied-eval/gazebo-local/session-lineage.json \
  --memory-eval results/embodied-eval/gazebo-local/memory-eval.json \
  --doctor-report results/embodied-eval/gazebo-local/doctor-report.json
```

## FireClaw ROS1 Config

The Gazebo config is at `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`.

Key endpoints:

| FireClaw Action | ROS Interface | ROS Name | Purpose |
|---|---|---|---|
| `navigate_to_floor` | action (move_base) | `/move_base` | Navigate to floor waypoint |
| `search_for_victims` | topic | `/fireclaw/search_request` | Trigger victim search |
| `report_status` | topic | `/fireclaw/operator_report` | Report status to operator |
| `return_to_safe_zone` | action (move_base) | `/move_base` | Return to safe zone |
| `emergency_stop` | service | `/fireclaw/emergency_stop` | Emergency stop |

### Customizing for Your Robot

Edit the YAML config to match your ROS graph:

1. Change `robot_id` to identify your robot
2. Update endpoint `name` values to match your ROS topic/service/action names
3. Adjust `goal_template` to match your robot's frame and coordinate system
4. Update `targets` with actual waypoint positions from your map

## Architecture

```text
MissionGateway
  → MissionAgent (task decomposition)
    → RobotSubagentClient (dispatch)
      → FireClawGateway (adapter=ros1)
        → Ros1RobotAdapter (supports_real_execution=True)
          → Ros1Transport (topic/service/action)
            → Gazebo ROS nodes (move_base, sensors, etc.)
```

Gazebo is treated as a ROS1 robot endpoint provider. FireClaw does not know or care whether the ROS nodes are running on real hardware or in Gazebo — the ROS1 transport layer is identical.

## Troubleshooting

### "Connection refused" when running FireClaw

- Verify `roscore` is running: `rostopic list`
- Verify `ROS_MASTER_URI` is set: `echo $ROS_MASTER_URI`
- Verify `ROS_IP` matches your machine: `echo $ROS_IP`

### "move_base action server not found"

- Launch the navigation stack: `roslaunch turtlebot3_navigation turtlebot3_navigation.launch`
- Verify action topics exist: `rostopic list | grep move_base`

### Robot doesn't move

- Check Gazebo is running and robot is spawned
- Check `/odom` publishes: `rostopic echo /odom -n 1`
- Check move_base is active: `rostopic echo /move_base/status -n 1`

### "No online robots available"

- FireClaw's robot registry must list the robot with correct `base_url`
- With the profile-driven workflow, the registry is built automatically from `[mission].robot_profiles` in `fireclaw.toml`
- For embodied_eval, this is handled automatically
- For legacy workflows, export the robot profile manually:

```bash
.venv/bin/python -m fireclaw_core robot-profile export \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/mission/robots.json
```

## Sensor Discovery and SafetyGate

The ROS1 robot gateway derives `RobotState.available_sensors` from verified ROS topics.

For each candidate topic, FireClaw checks:

1. the topic is present in `rostopic list`;
2. `rostopic type <topic>` matches a default or profile mapping rule;
3. `rostopic echo -n 1 <topic>` returns a recent message within the configured timeout.

Only verified sensors are passed to SafetyGate. If `/camera/image_raw` is absent, has the wrong type, or does not publish a message, `rgb_camera` is not available and `search_for_victims` is blocked. This is expected in Gazebo unless a real camera topic is running.

Generate suggested profile rules:

```bash
.venv/bin/python -m fireclaw_core robot-profile discover \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output /tmp/gazebo_turtlebot3.discovered.toml
```

Review the suggestion before copying rules into a profile. Static `available_sensors` declarations must not circumvent SafetyGate verification.

## What This Proves

When all commands exit 0 and the Gazebo robot moves:

- FireClaw's ROS1 adapter config loads correctly
- ROS1 transport connects to Gazebo's ROS nodes
- Navigation goals are sent and received via move_base
- Mission trace reaches terminal status
- Proof bundle contains all required artifacts

This validates the full chain from operator command to simulated robot execution. For real robot validation, see `ros1-hardware-smoke-proof.md`.

## Sensor Health Diagnostics

FireClaw separates topic discovery from sensor verification.

- `discovered`: a topic/type matched a known or profile rule.
- `healthy`: the current stream passed the sensor health policy.
- `verified`: the sensor is mapped and healthy.
- `degraded`: the topic exists but the health policy failed.

SafetyGate only uses verified sensors from `RobotState.available_sensors`.

For Gazebo TurtleBot3, `search_for_victims` remains blocked unless a camera topic is present and its health check passes. A camera topic with empty or stale data is reported in `sensor_diagnostics.findings` but is not added to `available_sensors`.
