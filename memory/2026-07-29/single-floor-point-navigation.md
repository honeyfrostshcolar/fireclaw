# Single-floor point-navigation contract

## 2026-07-29 16:05:44 +08

### Task goal

Unify FireClaw's current spatial assumption:

- robots operate within one floor and one active 2D map;
- current navigation means moving to a point, not moving to another floor;
- multi-floor fields and methods may remain only as clearly marked compatibility
  and future-extension interfaces.

### OpenClaw analogue

This is a robotics-specific spatial and safety contract. OpenClaw does not
have a direct floor-navigation analogue. The implementation therefore keeps
FireClaw's existing tool/skill/runtime boundary while changing the active
robotics contract from `navigate_to_floor` to `navigate_to_point`.

### Current conclusion

The active contract is now:

```text
navigate_to_point(x, y, yaw=0.0, frame_id="map")
```

Operator commands such as `去坐标 (2.0, 1.5) 救人` produce a point target,
central structured dispatch carries `target.pose`, and the Robot Agent
executes `navigate_to_point` through the normal safety and action runtime.

`navigate_to_floor`, `target.floor`, `target_floor`, `current_floor`,
`reachable_floors`, and `victims_by_floor` remain available for persisted
records, legacy tests, and future multi-floor work. Default adapters expose
only floor 1, so legacy requests for another floor fail closed.

### Files inspected

- `src/fireclaw_core/planner/planner.py`
- `src/fireclaw_core/mission/mission_planner.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/task/task_contract.py`
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/execution/action_runtime.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/infra/operator_projection.py`
- current ROS1 configs and robot profiles under `examples/`
- current README/deployment/architecture documentation

### Main implementation changes

- Added typed `navigate_to_point` support to robot adapters, the action
  runtime, skill registry, ROS1/ROS2 boundaries, event projection, safety
  sensor policy, and completion contracts.
- Default dry-run, mock, and simulator environment state is single-floor:
  `reachable_floors=[1]`.
- Rule-based agent and mission planners parse current-map coordinates.
- `PlanningResult` and structured task conversion preserve `target_pose`.
- `MissionAgent.submit_subtask()` now structures point commands instead of
  leaving the Robot Agent to reparse them.
- A structured subtask with `target.pose` prepends `navigate_to_point` to its
  capability chain. A local search task without a point target still uses
  only search/report and does not invent navigation.
- Robot Agent policy validation prevents an LLM from changing the contracted
  point coordinates or frame.
- Non-finite `x`, `y`, and `yaw` are rejected both at structured-task
  validation and at the final robot adapter boundary.
- Current Gazebo/ROS1 profiles and demos use point navigation.
- Legacy transport-only Fibonacci and turtlesim configs are explicitly
  labeled as not implementing the current spatial contract.
- Added `docs/architecture/fireclaw-spatial-scope.md` as the authoritative
  spatial-scope document and added notices to older architecture material.

### Commands and results

Initial full regression after restricting default floors:

```text
44 failed, 1752 passed, 6 deselected
```

The failures were old integration tests that treated a default request for
floor 2 as a successful current task. They were migrated to point targets;
dedicated legacy floor tests were retained.

Focused core regression:

```text
117 passed in 12.38s
```

Final non-ROS regression:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
tests -m 'not ros1_smoke'

1799 passed, 6 deselected in 142.07s
```

Additional verification:

```text
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src tests
git diff --check
```

Both completed successfully.

### Failed attempts and corrections

- The CLI demo still used `去二楼救人` from `agent_cli.py` even after the
  demo function default changed. The real CLI routing default was updated.
- `MissionAgent.submit_subtask()` initially still parsed only floors. It now
  parses point targets first and keeps floor parsing as a compatibility path.
- Capability defaults intentionally omitted navigation for a local search.
  Point-bearing subtasks now add point navigation based on the explicit
  spatial target, avoiding both missing movement and invented movement.
- Operator text initially rendered a floor-specific search message when no
  floor was present. It now says that the current target area is being
  searched.

### Research and safety impact

Engineering correctness improves because a map-frame pose now survives the
whole chain from operator command to ROS action without being encoded as a
building floor. The coordinate contract is typed, auditable, immutable at
the Robot Agent policy boundary, and protected against non-finite values.

This is an architectural cleanup and safety prerequisite, not a standalone
research contribution. Future multi-floor work must model elevators,
stairs, map/frame transitions, transfer preconditions, and cross-floor
localization explicitly instead of reactivating `navigate_to_floor` as a
single opaque action.

### Next recommended step

Define the Robot Agent navigation capability as a production-grade skill
contract around the real local navigation stack: goal acceptance,
localization readiness, progress observations, cancellation, timeout,
recovery, and route-blocked evidence. Keep this within the single active map.

### Remaining uncertainty

- ROS1 smoke tests were deselected because they require a live ROS
  environment; the non-ROS adapter/config contract is covered.
- Historical plans and specs still contain floor examples by design. Current
  docs point to the authoritative spatial-scope document rather than
  rewriting historical records.
- Multi-floor interfaces are compatibility-only and are not validated as a
  deployable capability.
