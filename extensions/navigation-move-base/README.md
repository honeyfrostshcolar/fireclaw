# ROS1 move_base Navigation Plugin

This Plugin package connects FireClaw to the ROS1 `move_base` Runtime.

The upstream ROS Navigation Stack is vendored as a pinned source snapshot at
`ros_ws/src/navigation/`. Its exact source is recorded in
`ros_ws/navigation.repos` and `ros_ws/UPSTREAM.md`.

## Contributions

The intended package shape is:

```text
Navigation Plugin
├── Navigation Skill
├── navigate_to_point Tool
├── move_base_navigation_status Tool
├── move_base_parameter_catalog/get/set_parameters Tools
├── move_base_cancel_navigation Tool
├── move_base_clear_costmaps Tool
├── Plugin-owned ROS1 action adapter
└── move_base Runtime
```

The package is discovered from `fireclaw.plugin.json` and activated through
`plugin/entrypoint.py`. The entrypoint registers all of its Tool contributions
atomically through the generic FireClaw Plugin Host. Gateway code does not name
any move_base Tool or call a move_base-specific registration function.

There is no `fireclaw_core.navigation` compatibility module. The catalog,
backend, physical handler, typed Tool contracts, Skill, ROS source, and tests
are all owned by this extension package.

Simulation exposes the finite typed parameter catalog for tuning experiments.
Real mode hides bounded mutation by default, and any explicitly enabled real
parameter still requires exact operator approval.

## Runtime Contract

```text
Tool: navigate_to_point
Inputs: x, y, yaw, frame_id
Plugin action handler: navigate_to_point
ROS action: /move_base
ROS type: move_base_msgs/MoveBaseAction
Execution deadline: 120 seconds (monotonic host clock)
Cancellation acknowledgement: 2 seconds
```

Those defaults may be overridden only by trusted Plugin deployment config via
`navigate_timeout_seconds` and `cancellation_ack_timeout_seconds`. They are
not `navigate_to_point` Tool inputs and therefore cannot be selected by the
LLM or an untrusted task payload.

On operator cancellation or deadline expiry, the core sends one cooperative
cancellation signal to the Plugin handler. `Ros1MoveBaseBackend` calls
`cancel_goal()` and waits for a confirmed actionlib terminal state. A confirmed
operator cancellation becomes `cancelled`; a confirmed deadline cancellation
becomes `timed_out`. Missing acknowledgement becomes `lost`, retains motion
leases, and closes further resource admission. Stopping a Python wait/thread is
never treated as proof that the robot stopped.

## Layout

- `ros_ws/src/navigation/`: pinned upstream ROS Navigation Stack source.
- `ros_ws/src/fireclaw_gazebo_contact_monitor/`: acceptance-only trusted
  Gazebo `ContactManager` WorldPlugin.
- `ros_ws/navigation.repos`: reproducible upstream checkout manifest.
- `fireclaw.plugin.json`: manifest read by the generic extension scanner.
- `plugin/entrypoint.py`: provider-owned activation and Tool registration.
- `plugin/move_base.py`: provider-owned parameter catalog and ROS adapters.
- `tools/`: atomic FireClaw Tool definitions and package documentation.
- `runtime/`: optional thin integration helpers; do not duplicate `move_base`.
- `skills/navigation/SKILL.md`: Agent navigation workflow.
- `tests/`: ROS contract, simulation, and opt-in Gazebo acceptance tests.
- `config/acceptance/`: validated, data-only acceptance scenarios.
- `launch/fireclaw_acceptance_world.launch`: fixed trusted TurtleBot3
  acceptance environment; it is not an LLM-callable Tool.
- `worlds/fireclaw_acceptance.world`: fixed world that loads the collision
  observer before the Robot is spawned.

A robot deployment may still own its launch and navigation parameters outside
FireClaw. Upstream package launch files remain inside `ros_ws/src/navigation/`.

## Runtime Ownership

The recommended real-robot mode is `external`: robot bringup, `systemd`, or a
trusted ROS launch process starts `move_base`; FireClaw checks readiness and
attaches to `/move_base`.

A future `managed` mode may let a trusted Plugin service start an allowlisted
command for simulation. The LLM must never compose arbitrary shell or
`roslaunch` commands, and launch arguments are not Tool parameters.

## Build

From `extensions/navigation-move-base/ros_ws/`:

```bash
source /opt/ros/noetic/setup.bash
catkin_make
```

The system-installed `move_base` remains usable. Sourcing this workspace's
`devel/setup.bash` selects the pinned source build as an overlay.

The live acceptance runner also requires the trusted contact observer. Build
that package after changing its C++ source or CMake metadata:

```bash
catkin_make -C extensions/navigation-move-base/ros_ws \
  --pkg fireclaw_gazebo_contact_monitor
```

The runner fails before starting a goal when the package inputs or shared
library are absent, or when the C++ implementation is newer than the library;
it never silently downgrades collision evidence to an unobserved zero. The
proof records both the implementation source and exact loaded binary hashes.

## Live Gazebo Acceptance

The live lane is opt-in and defaults to skipped in ordinary pytest runs. The
trusted runner starts isolated loopback ROS/Gazebo masters, launches the fixed
TurtleBot3 world, runs the marked test, writes a proof bundle, and cleans up
the exact launch process it started:

```bash
FIRECLAW_PYTHON=/path/to/python \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

The default is the success scenario. Run the live Mission cancellation lane
against a fresh world with:

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/cancel.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Run the live physical deadline lane with:

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/timeout.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Run the live native move_base abort lane with:

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/abort.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Run the diagnostics-first bounded recovery lane with:

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/stall-recover.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Run the diagnostics-first operator escalation lane with:

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/stall-escalate.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Paper validation/test runs use the versioned
`config/acceptance/frozen-suite.yaml`. It fixes the six scenario files, the
single-floor `map` point-target contract, seed `0`, shared world/map/robot/
navigation assets, and one repeat index per split. The trusted runner verifies
the selected scenario and SHA-256 hashes before starting Gazebo, and stores the
verified selection in `frozen-suite.json`:

```bash
FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=validation \
FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX=0 \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/success.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Use `FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=test` for the held-out test repetition.
The collision-calibration positive control remains simulation-only and is not
part of this six-scenario task suite.

The success scenario submits a structured task through Mission Run and the
Robot Gateway. Because this is a non-dry physical Tool invocation, the first
Robot task must reach `awaiting_confirmation`; the harness then uses the
Gateway's authenticated `/confirm` control endpoint. The confirmed task must
reach the Plugin-owned `Ros1MoveBaseBackend`, publish a `/move_base` goal and
feedback, receive actionlib `SUCCEEDED`, stop, and finish as `completed`.
No independent action client sends the acceptance goal.

The cancel scenario follows the same authorization and Plugin-owned execution
path, waits for live feedback and at least 0.15 m of displacement, then invokes
`MissionRunManager.cancel()`. It requires actionlib `PREEMPTED(2)` or
`RECALLED(8)`, explicit `cancellation_acknowledged=true` and
`runtime_stopped=true`, a canonical `cancelled` Robot task and Mission report,
a unique terminal event, bounded stop latency and bounded post-cancel motion.
The Mission scheduler waits for the Robot task's real terminal state; an
unconfirmed stop becomes `lost` instead of a synthetic cancellation.

The timeout scenario does not call the Mission cancellation surface. It first
requires live feedback and physical displacement, then lets the trusted
3-second Plugin action deadline expire. The Runtime emits
`action.cancel_requested` with `cancellation_reason="deadline_exceeded"`, and
the same action/task may become `timed_out` only after actionlib reports
`PREEMPTED(2)` or `RECALLED(8)` and the backend acknowledges that motion has
stopped. Mission's default timeout policy aborts the remaining plan while
preserving `timed_out`; it does not blindly retry a physical navigation goal.

The abort scenario fixes the goal outside the bounds computed from the live
`/map`, sets a trusted 2-second planner patience and disables native recovery,
then requires the same Plugin-owned action to end as actionlib `ABORTED(4)`.
It preserves the move_base status text and `move_base_aborted` error code at
the action boundary, confirms the runtime is stopped, and propagates the same
Robot task and Mission as canonical `failed`. It forbids cancellation,
deadline, retry, reassign, Adapter fallback, and synthetic success paths.

The two stall scenarios use a trusted launch-only fault injection that sets
the live DWA `max_vel_x` and `min_vel_x` to zero. After the first real goal
times out and move_base acknowledges `PREEMPTED(2)` or `RECALLED(8)`, the Robot
Agent must call the read-only `navigation_diagnostics` Tool and preserve its
evidence ID. `stall-recover` permits exactly one Plugin-owned
`move_base_set_parameters` call, restores `max_vel_x=0.22`, retries the exact
same goal once, and must finish `completed`. `stall-escalate` permits neither
mutation nor retry and must finish `escalated` with
`persistent_navigation_stall` and the same diagnostic evidence reference.
These deterministic lanes evaluate integration policy, not LLM planning
quality.

Proof bundles are written beneath `results/gazebo-acceptance/<run-id>/` and
include pre-confirmation and post-confirmation snapshots of the same Robot
task trace, Plugin inventory, ROS graph, goal/feedback/status stream, pose
evidence, full Mission Run, live navigation parameters, system versions,
JUnit, and ROS logs. Mission keeps polling that task while it is
`awaiting_confirmation`; after `/confirm`, the same task reaches `completed`,
an acknowledged `cancelled`, an acknowledged `timed_out`, or a native
move_base-derived `failed` terminal and the Mission produces the matching final
report. Cancel runs additionally write `cancellation-evidence.json`; timeout
runs write `timeout-evidence.json`; abort runs write `abort-evidence.json`,
`map-evidence.json`, and `navigation-parameters.json`; stall runs write
`stall-evidence.json`, `navigation-diagnostics.json`, and before/after live
navigation parameter snapshots.

Every live scenario also starts the acceptance-only
`libfireclaw_gazebo_contact_monitor.so` WorldPlugin. It reads Gazebo's physics
`ContactManager`, filters the complete `turtlebot3_burger` collision scope, and
publishes `gazebo_msgs/ContactsState` on
`/fireclaw/acceptance/contacts`. The observer must be connected before the
first `/move_base` goal and remain connected until the terminal stopped proof.
The bundle retains the normalized full contact stream, its classification and
episode counts, the observer source asset, and the exact loaded shared library
SHA-256.

The fixed `fireclaw.acceptance.prohibited-contact/v1` rule excludes only normal
left-wheel, right-wheel, or caster contact with `ground_plane`. Robot contact
with walls or other models, base/sensor contact with the ground, self-contact,
and an unexpected contact pair are prohibited collision episodes. A stream
overflow, missing publisher, late observer, or missing stopped terminal makes
the metric unavailable rather than collision-free.

After pytest exits, the trusted runner automatically normalizes that source
proof into `<run-id>/evaluation/` using the shared `ros_gazebo_system` schema.
The evaluation bundle embeds and hashes the raw proof and referenced assets,
retains every canonical outcome, and reports behavior-contract success
separately from completed-task success. New runs write
`collision-contact-stream.jsonl`, `collision-evidence.json`, and the loaded
`collision-monitor-plugin.so`. Older proofs without this instrumentation stay
readable, but their collision metric remains missing and cannot be interpreted
as zero collisions or used as a paper-ready result.

### Collision positive control

`config/acceptance/collision-calibration.yaml` is a simulation-only
measurement calibration, not a navigation Mission. It spawns the small static
`tests/acceptance/assets/collision_calibration_probe.sdf` through Gazebo's
`/gazebo/spawn_sdf_model` service in shallow overlap with the stationary Burger,
requires prohibited `base_link` contact in the raw ContactManager stream, and
then deletes the model through `/gazebo/delete_model`. The proof also requires
no `/move_base` goal, no non-zero `/cmd_vel`, stopped odometry, and bounded Robot
displacement.

The trusted runner routes this exact scenario to the independent
`gazebo_collision_calibration` evaluator. That evaluator reclassifies the raw
collision pair and verifies spawn/state/delete responses plus SDF, observer,
and source-proof hashes. It always records `task_metrics_applicable=false`, so
the deliberately induced contact cannot enter navigation success or
collision-free denominators.

## Initial Acceptance Criteria

1. `/move_base` exposes `move_base_msgs/MoveBaseAction`.
2. `map -> odom -> base_link` TF is continuous and correctly timestamped.
3. Localization, map, costmaps, and `/cmd_vel` operate without FireClaw.
4. An RViz `2D Nav Goal` succeeds before Agent integration.
5. Action feedback, success, abort, preempt, cancellation, and timeout are
   observable.
6. FireClaw sends a single-floor `map` goal through the Plugin-owned action
   adapter.
7. Emergency stop and `robot_motion`/`local_navigation` leases prevent new
   motion dispatch.
8. A blocked/stalled world triggers `navigation_diagnostics`, followed by an
   evidence-backed recovery or explicit escalation.
9. The final task report and audit ledger preserve feedback, terminal state,
   timeout/cancel cause, owner ID, and backend evidence.
10. A trap Adapter proves that navigation never falls back to a domain method
    on `RobotAdapter`.
11. Trusted contact instrumentation covers the full goal-to-terminal-stop
    window and reports either a validated collision count or explicit missing
    data.
12. A task-excluded positive control produces a real prohibited Gazebo contact,
    is independently detected from the raw stream, and leaves the Robot stopped
    after cleanup.
