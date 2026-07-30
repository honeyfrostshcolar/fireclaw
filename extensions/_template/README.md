# Robot Plugin Template

Copy this directory to `extensions/<plugin-name>/`.

Document:

- Plugin identity, version, ownership, and compatibility;
- bundled Skills and registered Tools;
- supported Adapters and Runtime dependencies;
- Tool schemas, permissions, safety classes, resource locks, and evidence;
- simulation and real-robot launch procedures;
- configuration ownership and parameters not exposed to the LLM.

Only create `ros_ws/`, `config/`, or `launch/` when the Plugin owns those
artifacts. A Plugin may instead declare an external Runtime and expose only
readiness checks plus trusted Adapter bindings.

Do not create a local registry. Activate every contribution through the shared
`FireClawPluginHost`.
