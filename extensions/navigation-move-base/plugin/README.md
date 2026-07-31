# Plugin Registration

The move_base Navigation Plugin owns its navigation Tool contributions,
bundled Navigation Skill, Adapter bindings, and lifecycle metadata.

`plugin/entrypoint.py` registers one atomic Plugin Host contribution containing:

- the physical `navigate_to_point` Tool and its move_base action handler;

- `move_base_parameter_catalog`;
- `move_base_navigation_status`;
- `move_base_get_parameters`;
- `move_base_set_parameters`;
- `move_base_cancel_navigation`;
- `move_base_clear_costmaps`.

`navigate_to_point` is owned by this Plugin and calls the provider-owned
trusted move_base backend. It is not bound to a same-named method on
`Ros1RobotAdapter`; the LLM never receives arbitrary ROS names or shell
commands.

The host only knows the manifest and generic `register(api)` contract. It does
not contain move_base-specific configuration branches or Tool schemas.

The provider uses the public `fireclaw_plugin_sdk.ToolSpec` contract. It does
not import `AgentTool`, `FireClawPluginHost`, or other FireClaw registry
implementation classes. The host converts each `ToolSpec` to its internal
runtime model during the atomic registration transaction.

Simulation mode exposes every parameter in the finite, typed catalog. Real
mode hides bounded mutation by default; enabling selected real parameters still
passes the Deployment Policy and requires exact operator authorization.
