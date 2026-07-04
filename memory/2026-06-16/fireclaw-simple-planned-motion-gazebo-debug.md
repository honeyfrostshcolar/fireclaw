# FireClaw Simple Planned Motion Gazebo Debug

## Task Goal

Run the user's Gazebo + TurtleBot3 navigation + FireClaw gateway/serve flow with the mission command:

- `去二楼做一次简单的规划运动`

The user requested LLM trace preservation. The run used `--planner deterministic`, so no LLM calls were made and no trace JSONL was produced.

## Commands / Runtime Setup

- Gazebo:
  - `source /opt/ros/noetic/setup.bash`
  - `export TURTLEBOT3_MODEL=burger`
  - `roslaunch turtlebot3_gazebo turtlebot3_world.launch`
- TurtleBot3 full navigation stack:
  - `roslaunch turtlebot3_navigation turtlebot3_navigation.launch map_file:=$(rospack find turtlebot3_navigation)/maps/map.yaml`
- robot-gateway:
  - `.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --robot-profile examples/robot_profiles/gazebo_turtlebot3.toml`
- serve:
  - `.venv/bin/python -m fireclaw_core serve --config fireclaw.toml --robot-profile examples/robot_profiles/gazebo_turtlebot3.toml --data-dir data/debug-gazebo --planner deterministic --llm-trace-path data/debug-gazebo/traces/simple-planned-motion.jsonl`
- Mission:
  - `curl -sS -X POST http://127.0.0.1:8766/missions -H 'Content-Type: application/json' -H 'X-Operator-Scopes: task.submit,task.cancel,state.read,mission.submit,mission.cancel,mission.plan,mission.read' -d '{"command":"去二楼做一次简单的规划运动","operator":{"operator_id":"nankai","role":"operator"}}'`

## Observed Problems

1. ROS master had many stale nodes, including a dead `/move_base`.
   - `rosnode info /move_base` failed to contact `http://robot:39609/`.
   - Ran `printf 'y\n' | rosnode cleanup`.

2. Gateway `/state` timed out under the full navigation stack.
   - Before fix, direct `/state` took `25.663s`.
   - Cause: `Ros1CliGraphProvider.topic_types()` called `rostopic list`, then `rostopic type <topic>` once per topic.
   - With the full navigation stack, there were 64 topics; the per-topic loop took `22.235s`.
   - `rostopic list -v` returned all topic types in `0.354s`.

3. Deterministic mission planner did not recognize `做一次简单的规划运动`.
   - Exact command without a floor returned `clarify`.
   - The usable debug command became `去二楼做一次简单的规划运动`.
   - Added `规划运动|导航测试|简单移动` as patrol intent patterns.

4. ROS1 payload templating failed for the YAML syntax used by `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`.
   - Runtime error: `'str' object has no attribute 'x'`.
   - `ros1_payload` still contained strings such as `${targets.floor_${floor}.position}`.
   - Cause: `render_ros1_template()` supported `{{ ... }}` templates and simple substitutions only inside expressions, but the config uses whole-value `${...}` references.

5. Non-blocking mission memory warning:
   - `ValueError: Invalid record type: dispatch. Must be one of: ['command', 'correction', 'lesson', 'observation', 'outcome', 'plan']`
   - Cause: scheduler path wrote mission memory with record type `dispatch`.

## Files Modified

- `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - `Ros1CliGraphProvider.topic_types()` now prefers parsing `rostopic list -v`.
  - Keeps old `rostopic list` + `rostopic type` fallback.
- `tests/test_ros1_sensor_discovery.py`
  - Added regression test for parsing verbose topic list without per-topic type calls.
- `src/fireclaw_core/mission/mission_planner.py`
  - Maps `规划运动|导航测试|简单移动` to `patrol`.
- `tests/test_mission_planner.py`
  - Added regression test for `去二楼做一次简单的规划运动`.
- `src/fireclaw_core/ros/ros1_template.py`
  - Supports whole-value `${...}` expressions that can return non-string values.
  - Supports inline `${floor}` substitutions in plain strings.
- `tests/test_ros1_template.py`
  - Added regression test for `${targets.floor_${floor}.position}` and `status_report_floor_${floor}`.
- `src/fireclaw_core/mission/mission_agent.py`
  - Scheduler mission memory summary now uses record type `outcome` instead of invalid `dispatch`.
- `tests/test_mission_agent.py`
  - Scheduler path now asserts a mission memory `outcome` record is written.

## Verification

- Regression tests:
  - `.venv/bin/python -m pytest tests/test_ros1_template.py tests/test_ros1_sensor_discovery.py tests/test_mission_planner.py tests/test_mission_agent.py::test_plan_and_submit_uses_scheduler_by_default tests/test_provider.py tests/test_provider_runtime.py -q`
  - Result: `75 passed in 0.31s`.
- Gateway `/state` after discovery fix:
  - `real 0m2.608s`
  - `online=True`
  - `available_sensors=['imu', 'lidar']`
  - `verified=['imu', 'lidar']`
- Mission result:
  - `status=succeeded`
  - `intent=patrol`
  - generated subtask: `去2楼巡逻`
  - `navigate_to_floor status=succeeded`
  - `report_status status=succeeded`
- ROS evidence:
  - `/move_base/goal` received:
    - `frame_id: "map"`
    - `position.x: 2.0`
    - `position.y: 0.0`
    - `orientation.w: 1.0`
  - `/move_base/result` returned:
    - `status: 3`
    - `text: "Goal reached."`

## Current Conclusion

The planned-motion debug flow is now working end to end in Gazebo with the full TurtleBot3 navigation stack:

operator mission -> deterministic mission planner -> patrol capability -> robot-gateway -> ROS1 move_base action -> goal reached.

No LLM trace was generated because the run used deterministic planning and did not call an LLM provider.

## Remaining Notes

- The current `gazebo_turtlebot3` profile still has `search_for_victims` requiring camera-like sensors. The `patrol` capability is the right smoke-test path for move_base without camera.
- `robot.discovery_fingerprint` is missing in the profile, so `/scan` appears as `confirmation_stale=true` even though it is verified and confirmed. This does not block the patrol test.
- Gazebo printed a non-blocking `fuel.gazebosim.org` SSL error while trying to contact Fuel; the simulation still ran and navigation succeeded.
