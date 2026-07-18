# ROS1 Gazebo Simulation Debugging Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FireClaw usable for laptop/desktop ROS1 + Gazebo debugging before any real firefighting robot deployment.

**Architecture:** Keep three layers separate: FireClaw internal simulator validates agent/gateway/memory/lifecycle; ROS1 smoke validates transport primitives; Gazebo validates FireClaw's ROS1 config against a high-fidelity robot simulation. Gazebo is treated as a ROS1 robot endpoint provider, not as a new FireClaw core runtime.

**Tech Stack:** Ubuntu 20.04 or container/VM, ROS1 Noetic, Gazebo Classic 11, `gazebo_ros_pkgs`, `move_base`, `move_base_msgs/MoveBaseAction`, optional TurtleBot3 Gazebo/navigation stack, existing FireClaw `ros1` adapter, `embodied_eval`, `embodied_proof_bundle`, and validation sidecar.

---

## Current Usability

FireClaw is already usable for simulator-level embodied-agent debugging:

- focused validation tests pass;
- default `embodied_eval` returns `status="pass"` and exit code `0`;
- proof bundle consumes eval artifacts including `doctor-report.json`;
- ROS1 transport unit tests cover topic, service, action goal construction, feedback, cancel, timeout, and dict-to-ROS-message conversion;
- ROS1 smoke tests are gated by `FIRECLAW_RUN_ROS1_SMOKE=1`.

FireClaw is not yet one-command usable with Gazebo because the repository does not currently include:

- a Gazebo-specific ROS1 adapter config;
- a Gazebo launch/runbook that starts world, robot, navigation/action servers, and FireClaw in the right order;
- a Gazebo scenario fixture that proves `MissionGateway -> RobotSubagentClient -> FireClawGateway -> ROS1 adapter -> Gazebo ROS nodes -> mission trace`;
- a proof bundle that includes Gazebo environment metadata and ROS1 smoke artifacts.

## Definition of "Can Use It"

The system is ready for your intended local debugging workflow when this command chain is stable:

```bash
# 1. FireClaw internal embodied-agent gate
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py -q

# 2. ROS1 primitive transport gate
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q

# 3. Gazebo high-fidelity simulation gate
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/gazebo_rescue_scenarios.json \
  --output-dir results/embodied-eval/gazebo-local \
  --adapter ros1

# 4. Gazebo proof bundle
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

Expected:

- all commands exit `0`;
- Gazebo robot visibly moves or executes the mapped ROS action/topic/service;
- FireClaw mission trace reaches `succeeded` or another terminal status;
- proof bundle contains mission, ROS1/Gazebo, memory, lifecycle, and doctor artifacts.

---

## Phase 0: Freeze Current FireClaw Baseline

**Purpose:** Confirm FireClaw itself is not the problem before adding Gazebo.

**Commands:**

```bash
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_ros1_transport.py tests/test_ros1_config.py -q

rm -rf results/embodied-eval/local-sim results/embodied-proof/local-sim
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
.venv/bin/python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/embodied-proof/local-sim \
  --run-id local-sim \
  --mission-trace results/embodied-eval/local-sim/mission-trace.json \
  --mission-events results/embodied-eval/local-sim/mission-events.json \
  --task-flow results/embodied-eval/local-sim/task-flow.json \
  --session-lineage results/embodied-eval/local-sim/session-lineage.json \
  --memory-eval results/embodied-eval/local-sim/memory-eval.json \
  --doctor-report results/embodied-eval/local-sim/doctor-report.json
```

**Acceptance:**

- focused tests pass;
- simulator eval exits `0`;
- simulator proof bundle exits `0`.

---

## Phase 1: Prepare ROS1 + Gazebo Environment

**Purpose:** Create a reproducible ROS1/Gazebo runtime that FireClaw can target.

**Recommended environment:**

- Ubuntu 20.04 in native install, VM, or Docker with GUI/X11 support;
- ROS1 Noetic;
- Gazebo Classic 11;
- `gazebo_ros_pkgs`;
- `move_base`, `move_base_msgs`, `geometry_msgs`, `nav_msgs`, `sensor_msgs`, `std_srvs`, `std_msgs`;
- optional TurtleBot3 Gazebo/navigation stack for a standard mobile base.

**Commands:**

```bash
source /opt/ros/noetic/setup.bash
roscore --help
gazebo --version
rospack find gazebo_ros
rospack find move_base_msgs
rospack find actionlib
```

**Acceptance:**

- `roscore`, `gazebo`, `rostopic`, `rosservice`, and `rosrun` are on PATH;
- `rospack find gazebo_ros` works;
- `rospack find move_base_msgs` works.

---

## Phase 2: Start With a Known Gazebo Robot Stack

**Purpose:** Avoid debugging FireClaw and a custom firefighting simulation at the same time.

**Recommended first target:** TurtleBot3 or another standard ROS1 Gazebo robot with `move_base`.

**Minimum ROS graph required by FireClaw:**

- `/move_base` action server using `move_base_msgs/MoveBaseAction`;
- `/cmd_vel` topic using `geometry_msgs/Twist`;
- `/odom` topic using `nav_msgs/Odometry`;
- `/scan` topic using `sensor_msgs/LaserScan`;
- optional `/emergency_stop` service using `std_srvs/Trigger`.

**Manual verification:**

```bash
rostopic list
rosservice list
rosnode list
rostopic echo /odom -n 1
rostopic echo /scan -n 1
```

**Action verification:**

```bash
rostopic list | rg move_base
```

Expected to see action topics like:

```text
/move_base/goal
/move_base/result
/move_base/status
/move_base/feedback
```

**Acceptance:**

- robot spawns in Gazebo;
- `/move_base` action exists;
- robot can receive a manually sent navigation goal before FireClaw is involved.

---

## Phase 3: Add Gazebo ROS1 Adapter Config

**Files to add:**

- `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`
- `tests/test_ros1_config.py` coverage for this config

**Config shape:**

```yaml
robot_id: "gazebo_turtlebot3"
namespace: ""
transport:
  enabled: true
  wait_for_server_seconds: 10.0
  wait_for_result_seconds: 120.0
endpoints:
  navigate_to_floor:
    profile: move_base
    name: "/move_base"
    goal_template:
      target_pose:
        header:
          frame_id: "map"
        pose:
          position: "${targets.floor_${floor}.position}"
          orientation: "${targets.floor_${floor}.orientation}"
  search_for_victims:
    interface: topic
    name: "/fireclaw/search_request"
    type: "std_msgs/String"
    request_template:
      data: "search_floor_${floor}"
  report_status:
    interface: topic
    name: "/fireclaw/operator_report"
    type: "std_msgs/String"
    request_template:
      data: "status_report_floor_${floor}"
  return_to_safe_zone:
    profile: move_base
    name: "/move_base"
    goal_template:
      target_pose:
        header:
          frame_id: "map"
        pose:
          position: "${targets.safe_zone.position}"
          orientation: "${targets.safe_zone.orientation}"
emergency_stop:
  interface: service
  name: "/fireclaw/emergency_stop"
  type: "std_srvs/Trigger"
targets:
  floor_1:
    position: {x: 0.0, y: 0.0, z: 0.0}
    orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}
  floor_2:
    position: {x: 2.0, y: 0.0, z: 0.0}
    orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}
  safe_zone:
    position: {x: 0.0, y: 0.0, z: 0.0}
    orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}
```

**Acceptance:**

- config parses through `load_ros1_adapter_config`;
- doctor reports config pass/warn but not fail;
- no site-specific IP or secret is committed.

---

## Phase 4: Prove FireClaw -> ROS1 -> Gazebo Direct Subtask

**Purpose:** Verify FireClaw ROS1 adapter alignment before using the full mission planner.

**Commands:**

```bash
source /opt/ros/noetic/setup.bash
export ROS_MASTER_URI=http://localhost:11311
export ROS_IP=127.0.0.1

.venv/bin/python -m fireclaw_core.doctor \
  --adapter ros1 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml

.venv/bin/python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id gazebo_turtlebot3 \
  --command "去二楼" \
  --adapter-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml
```

**Acceptance:**

- FireClaw loads ROS1 config;
- ROS1 action client connects to `/move_base`;
- Gazebo robot receives a goal;
- FireClaw task trace records action feedback/result or a clear ROS1 timeout/error.

---

## Phase 5: Prove Full Mission Gateway With Gazebo

**Purpose:** Move from direct subtask debugging to the actual embodied-agent path.

**Required wiring:**

```text
MissionGateway
-> MissionAgent
-> RobotSubagentClient
-> FireClawGateway(adapter=ros1)
-> Ros1RobotAdapter
-> Ros1Transport
-> Gazebo ROS nodes
```

**Files to add:**

- `tests/fixtures/embodied_eval/gazebo_rescue_scenarios.json`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

**Scenario fixture:**

```json
[
  {
    "scenario_id": "gazebo-navigate-floor-2",
    "command": "去二楼救人",
    "expected_floor": 2,
    "expected_capability": "search_for_victims",
    "min_memory_records": 1,
    "requires_terminal_status": true
  }
]
```

**Run command:**

```bash
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/gazebo_rescue_scenarios.json \
  --output-dir results/embodied-eval/gazebo-local \
  --adapter ros1
```

If `embodied_eval` does not yet accept a ROS1 config path, add one:

```bash
--ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml
```

**Acceptance:**

- eval exits `0`;
- mission trace terminal status is `succeeded` or `completed`;
- task flow and session lineage are populated;
- memory records are created;
- proof artifacts are written.

---

## Phase 6: Package Gazebo Proof Bundle

**Purpose:** Make debugging results reproducible and shareable.

**Required artifacts:**

- `mission-trace.json`;
- `mission-events.json`;
- `task-flow.json`;
- `session-lineage.json`;
- `memory-eval.json`;
- `doctor-report.json`;
- ROS1 smoke artifact JSONL;
- Gazebo environment report:
  - ROS distro;
  - Gazebo version;
  - robot package;
  - launch file;
  - map/world name;
  - ROS topic/service/action list redacted of host-specific data.

**Command:**

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

**Acceptance:**

- bundle exits `0`;
- artifact README states this is Gazebo validation, not real robot validation;
- no private IPs, credentials, or site-specific paths are committed.

---

## Phase 7: Only After Gazebo Is Stable, Move Toward Robot-Specific Simulation

**Purpose:** Make Gazebo more firefighting-like without breaking the integration proof.

**Recommended additions after the first Gazebo gate passes:**

- custom fire/smoke markers as ROS topics rather than complex physics at first;
- perception node that publishes victim/hazard detections;
- mapping from FireClaw `search_for_victims` to a Gazebo/ROS perception service or action;
- `emergency_stop` service that halts Gazebo robot velocity;
- multi-floor semantics through named waypoints before modeling actual elevators/stairs.

**Acceptance:**

- each new simulation feature has one ROS topic/service/action mapping;
- FireClaw config remains declarative;
- mission proof bundle still exits `0`.

---

## What Not To Do Yet

- Do not add ROS2 for this milestone.
- Do not build a Web dashboard before Gazebo proof works.
- Do not start with a custom firefighting robot model if TurtleBot3/move_base is not yet passing.
- Do not let `adapter=simulator` silently call ROS; Gazebo must use explicit `adapter=ros1`.
- Do not commit large Gazebo logs, maps, bags, screenshots, or site-specific configs.

## Final Acceptance

The Gazebo debugging milestone is complete when:

```bash
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_ros1_transport.py tests/test_ros1_config.py -q
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/gazebo_rescue_scenarios.json \
  --output-dir results/embodied-eval/gazebo-local \
  --adapter ros1 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml
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

all exit `0`, and the Gazebo robot visibly executes the mapped ROS action.
