# FireClaw Extensions

`extensions/` contains repository-local Plugin packages and their executable
implementations, following OpenClaw's separation between Plugins, Skills, and
Tools.

Use one directory per deployable Plugin:

```text
extensions/
  <plugin-name>/
    README.md
    plugin/       # registration and ownership boundary
    tools/        # atomic typed Tool definitions/handlers
    runtime/      # thin wrapper around the implementation
    ros_ws/src/   # optional ROS packages and algorithm source
    config/       # optional algorithm-owned configuration overlays
    launch/       # optional simulator/robot launch orchestration
    tests/        # contract, integration, and simulation tests
    skills/       # optional SKILL.md workflows bundled by the Plugin
```

A Plugin can register multiple Tools and bundle one or more Skills. Algorithms
and ROS packages live here, not in the root `skills/` workspace.

Not every Plugin needs every directory. In particular, real-robot bringup and
launch files may remain owned by the robot deployment. FireClaw can attach to
an externally managed Runtime through a trusted Adapter.

All contributions must still be activated through `FireClawPluginHost`; this
directory is not a second registry. Arbitrary Python auto-loading is not
enabled because a repository-local Plugin can carry physical robot authority.
Future discovery must enforce compatibility, trust/signature, permission, and
isolation policy.

Copy `extensions/_template/` for a new Plugin. The first navigation package is
`extensions/navigation-move-base/`.
