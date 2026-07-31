# Navigation Tools

Atomic navigation operations belong to the Navigation Plugin:

- physical motion: `navigate_to_point`;
- bounded status/control: `move_base_navigation_status`,
  `move_base_cancel_navigation`, `move_base_clear_costmaps`;
- typed tuning: `move_base_parameter_catalog`,
  `move_base_get_parameters`, `move_base_set_parameters`.

These are Tools used by the Navigation Skill, not separate Skills. The
parameter catalog deliberately excludes ROS topic/frame names, footprint,
hardware limits, plugin class names, and arbitrary parameter-server keys.
