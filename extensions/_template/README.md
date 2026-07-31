# Robot Plugin Template

Copy this directory to `extensions/<plugin-name>/`.

Before activation, edit `fireclaw.plugin.json` with a stable Plugin ID and
declare the provider-owned `plugin/entrypoint.py`. FireClaw scans this
manifest, then invokes the entrypoint; no Gateway or core source edit is
required for a new Plugin.

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
