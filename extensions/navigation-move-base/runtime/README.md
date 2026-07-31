# move_base Runtime Integration

The Runtime is ROS1 `move_base`. The trusted boundaries are:

- `Ros1MoveBaseBackend` for physical `navigate_to_point` action goals and
  fixed-scope dynamic-reconfigure reads/updates,
  action cancellation, and costmap clearing.

The backend maps logical scopes (`move_base`, `dwa`, `local_costmap`,
`global_costmap`) to fixed ROS namespaces. It does not accept a namespace,
topic, service, or shell command from the LLM. Simulation uses
`InMemoryMoveBaseBackend` for deterministic contract tests.
