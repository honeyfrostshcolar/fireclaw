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

The generic loader scans only `fireclaw.plugin.json` manifests. It reads the
manifest first, validates the declared entrypoint and API version, and imports
that entrypoint only when the Plugin is enabled. The provider owns the
entrypoint, Tool schemas, Runtime adapter, and Plugin-specific configuration;
Gateway core has no per-Plugin registration branch. All contributions still
commit through `FireClawPluginHost`; this directory is not a second registry.

Third-party Python Plugins should import the public SDK contract instead of
FireClaw implementation modules:

```python
from fireclaw_plugin_sdk import ToolSpec


def register(api):
    api.register_tool(
        ToolSpec(
            name="scan_status",
            description="Read the current scan status.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda arguments: {"status": "succeeded"},
        )
    )
```

The host converts each `ToolSpec` to its internal `AgentTool` model during the
atomic registration transaction. Plugin code should not import
`fireclaw_core.agent.tool_runtime` or `fireclaw_core.plugin.plugin_host`.

Executable Plugins are explicitly trusted or sandboxed deployment artifacts,
not arbitrary files discovered by filename. Future releases can add package
signatures, provenance, and an isolated Plugin process without changing the
manifest/entrypoint contract.

Copy `extensions/_template/` for a new Plugin. The first navigation package is
`extensions/navigation-move-base/`.
