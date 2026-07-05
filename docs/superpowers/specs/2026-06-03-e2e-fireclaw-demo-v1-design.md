# End-to-End FireClaw Demo v1 Design

## Goal

Provide one dependency-free command that demonstrates FireClaw's current framework loop:

```text
operator command -> Gateway task submission -> control audit -> mock ROS1 action runtime -> task trace + projected state
```

## Scope

This demo is a local framework demonstration. It does not import `rospy`, connect to a ROS master, execute real robot transport, start a long-running HTTP server, or claim real-robot readiness.

## Architecture

Add a small `fireclaw_core.demo` module that creates an in-process `FireClawGateway` with the `mock-ros1` adapter. The module submits the natural-language rescue command through `FireClawGateway.submit_agent(...)`, polls `FireClawGateway.task_trace(...)` until a terminal result appears, and returns a compact JSON summary.

The existing CLI gains `--demo rescue`. When that flag is present, the CLI runs the demo path instead of direct `FireClawAgent.run(...)`. Existing command behavior remains unchanged.

## Output Contract

The demo JSON includes:

- `status`
- `task_id`
- `session_id`
- `operator`
- `control`
- `state`
- `result`
- `event_types`
- `action_events`
- `robot_state`

The output must show that:

- the task succeeded;
- the adapter mode is `mock_ros1`;
- operator identity and control decision were recorded;
- action lifecycle events were emitted;
- projected task/action state is present in the trace.

## Testing

Add focused tests for:

- `run_rescue_demo(...)` returns the expected end-to-end evidence;
- `python -m fireclaw_core --demo rescue ...` prints the same evidence and exits successfully.

Run the full pytest suite before committing.

