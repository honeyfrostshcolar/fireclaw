#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
run_id="${FIRECLAW_GAZEBO_ACCEPTANCE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"

if [[ ! "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "Unsafe FIRECLAW_GAZEBO_ACCEPTANCE_RUN_ID" >&2
  exit 2
fi

results_root="${FIRECLAW_GAZEBO_ACCEPTANCE_RESULTS_ROOT:-$repo_root/results/gazebo-acceptance}"
run_dir="$results_root/$run_id"
mkdir -p "$run_dir/ros-logs" "$run_dir/gazebo"

set +u
source /opt/ros/noetic/setup.bash
source "$repo_root/extensions/navigation-move-base/ros_ws/devel/setup.bash"
source "$repo_root/robots/turtlebot3_burger/ros_ws/devel/setup.bash"
set -u

contact_monitor_source="$repo_root/extensions/navigation-move-base/ros_ws/src/fireclaw_gazebo_contact_monitor/src/fireclaw_gazebo_contact_monitor.cpp"
contact_monitor_cmake="$repo_root/extensions/navigation-move-base/ros_ws/src/fireclaw_gazebo_contact_monitor/CMakeLists.txt"
contact_monitor_package="$repo_root/extensions/navigation-move-base/ros_ws/src/fireclaw_gazebo_contact_monitor/package.xml"
contact_monitor_library="$repo_root/extensions/navigation-move-base/ros_ws/devel/lib/libfireclaw_gazebo_contact_monitor.so"
if [[ ! -f "$contact_monitor_source" ]] || \
   [[ ! -f "$contact_monitor_cmake" ]] || \
   [[ ! -f "$contact_monitor_package" ]] || \
   [[ ! -f "$contact_monitor_library" ]] || \
   [[ "$contact_monitor_source" -nt "$contact_monitor_library" ]]; then
  echo "Gazebo contact monitor is missing or stale. Run:" >&2
  echo "  catkin_make -C $repo_root/extensions/navigation-move-base/ros_ws --pkg fireclaw_gazebo_contact_monitor" >&2
  exit 2
fi
export FIRECLAW_GAZEBO_CONTACT_MONITOR_LIBRARY="$contact_monitor_library"

export TURTLEBOT3_MODEL=burger
export ROS_MASTER_URI="${FIRECLAW_GAZEBO_ACCEPTANCE_ROS_MASTER_URI:-http://127.0.0.1:11371}"
export GAZEBO_MASTER_URI="${FIRECLAW_GAZEBO_ACCEPTANCE_GAZEBO_MASTER_URI:-http://127.0.0.1:11372}"
export ROS_IP=127.0.0.1
unset ROS_HOSTNAME || true
export ROS_HOME="$run_dir/ros-home"
export ROS_LOG_DIR="$run_dir/ros-logs"
export GAZEBO_LOG_PATH="$run_dir/gazebo"
export GAZEBO_MODEL_DATABASE_URI=""
export QT_QPA_PLATFORM=offscreen
export FIRECLAW_RUN_GAZEBO_ACCEPTANCE=1
export FIRECLAW_GAZEBO_ACCEPTANCE_RUN_ID="$run_id"
export FIRECLAW_GAZEBO_ACCEPTANCE_RUN_DIR="$run_dir"

case "$ROS_MASTER_URI" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    echo "Gazebo acceptance refuses a non-loopback ROS_MASTER_URI" >&2
    exit 2
    ;;
esac

case "$GAZEBO_MASTER_URI" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    echo "Gazebo acceptance refuses a non-loopback GAZEBO_MASTER_URI" >&2
    exit 2
    ;;
esac

python_bin="${FIRECLAW_PYTHON:-python3}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python executable not found: $python_bin" >&2
  exit 2
fi

scenario_path="${FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO:-$repo_root/extensions/navigation-move-base/config/acceptance/success.yaml}"
scenario_path="$(realpath -e -- "$scenario_path")"
case "$scenario_path" in
  "$repo_root"/*) ;;
  *)
    echo "Gazebo acceptance scenario must be repository-local" >&2
    exit 2
    ;;
esac
export FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$scenario_path"
acceptance_split="${FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT:-development}"
if [[ "$acceptance_split" == "validation" || "$acceptance_split" == "test" ]]; then
  collision_calibration_scenario="$repo_root/extensions/navigation-move-base/config/acceptance/collision-calibration.yaml"
  if [[ "$scenario_path" != "$collision_calibration_scenario" ]]; then
    "$python_bin" -m fireclaw_core.devtools.gazebo_acceptance_freeze \
      --suite "$repo_root/extensions/navigation-move-base/config/acceptance/frozen-suite.yaml" \
      --scenario "$scenario_path" \
      --split "$acceptance_split" \
      --repo-root "$repo_root" \
      >"$run_dir/frozen-suite-check.json"
  fi
fi

launch_args=(gui:=false seed:=0)
abort_scenario="$repo_root/extensions/navigation-move-base/config/acceptance/abort.yaml"
if [[ "$scenario_path" == "$abort_scenario" ]]; then
  launch_args+=(
    planner_patience:=2.0
    recovery_behavior_enabled:=false
  )
fi
stall_recover_scenario="$repo_root/extensions/navigation-move-base/config/acceptance/stall-recover.yaml"
stall_escalate_scenario="$repo_root/extensions/navigation-move-base/config/acceptance/stall-escalate.yaml"
if [[ "$scenario_path" == "$stall_recover_scenario" ]] || \
   [[ "$scenario_path" == "$stall_escalate_scenario" ]]; then
  launch_args+=(inject_stall:=true)
fi

launch_pid=""
cleanup() {
  if [[ -n "$launch_pid" ]] && kill -0 "$launch_pid" 2>/dev/null; then
    kill -INT "$launch_pid" 2>/dev/null || true
    for _index in $(seq 1 20); do
      if ! kill -0 "$launch_pid" 2>/dev/null; then
        break
      fi
      sleep 0.25
    done
    if kill -0 "$launch_pid" 2>/dev/null; then
      kill -TERM "$launch_pid" 2>/dev/null || true
    fi
    wait "$launch_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

roslaunch \
  "$repo_root/extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch" \
  "${launch_args[@]}" \
  >"$run_dir/roslaunch.log" 2>&1 &
launch_pid=$!

set +e
"$python_bin" -m pytest -q \
  -m gazebo_acceptance \
  "$repo_root/extensions/navigation-move-base/tests/acceptance" \
  --junitxml="$run_dir/junit.xml"
pytest_status=$?

# Freeze ROS/Gazebo-owned logs before hashing and embedding the source proof.
cleanup
launch_pid=""

collision_calibration_scenario="$repo_root/extensions/navigation-move-base/config/acceptance/collision-calibration.yaml"
if [[ "$scenario_path" == "$collision_calibration_scenario" ]]; then
  "$python_bin" -m fireclaw_core.devtools.gazebo_collision_calibration_eval \
    --source-proof "$run_dir" \
    --output-dir "$run_dir/evaluation" \
    --run-id "gazebo-collision-calibration-$run_id" \
    --split "$acceptance_split" \
    --repeat-index "${FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX:-0}"
else
  "$python_bin" -m fireclaw_core.devtools.ros_gazebo_system_eval \
    --source-proof "$run_dir" \
    --output-dir "$run_dir/evaluation" \
    --run-id "ros-gazebo-system-$run_id" \
    --split "$acceptance_split" \
    --repeat-index "${FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX:-0}"
fi
evaluation_status=$?
set -e

if (( pytest_status != 0 )); then
  exit "$pytest_status"
fi
exit "$evaluation_status"
