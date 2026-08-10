from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.agent.tool_runtime import AgentTool, AgentToolRuntime
from fireclaw_core.plugin.extension_loader import (
    discover_fireclaw_extensions,
    load_fireclaw_extensions,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile


def _write_extension(
    root: Path,
    plugin_id: str,
    entrypoint: str,
    source: str,
    *,
    enabled_by_default: bool = True,
) -> Path:
    extension = root / plugin_id.replace(".", "-")
    entrypoint_path = extension / Path(entrypoint)
    entrypoint_path.parent.mkdir(parents=True)
    entrypoint_path.write_text(source, encoding="utf-8")
    (extension / "fireclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_id,
                "api_version": "1",
                "entrypoint": entrypoint,
                "enabled_by_default": enabled_by_default,
                "trust_level": "trusted",
            }
        ),
        encoding="utf-8",
    )
    return extension


def _simulation_profile() -> DeploymentProfile:
    workspace = Path("data/test-extension-loader")
    return DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=SandboxProfile(
            workspace_root=workspace,
            allowed_workspace_roots=(workspace,),
        ),
    )


def test_discovery_is_manifest_only_and_does_not_import_entrypoint(tmp_path: Path) -> None:
    marker = tmp_path / "imported"
    _write_extension(
        tmp_path,
        "example.inspect",
        "plugin.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n",
    )

    result = discover_fireclaw_extensions((tmp_path,))

    assert [item.manifest.plugin_id for item in result.candidates] == [
        "example.inspect"
    ]
    assert not marker.exists()


def test_loader_invokes_entrypoint_and_registers_contributions_transactionally(
    tmp_path: Path,
) -> None:
    _write_extension(
        tmp_path,
        "example.tool",
        "plugin.py",
        """
from fireclaw_core.agent.tool_runtime import AgentTool

def register(api):
    api.register_tool(AgentTool(
        name="example_inspect",
        description="Read a deterministic example value.",
        input_schema={"type": "object", "additionalProperties": False},
        handler=lambda arguments: {"status": "succeeded", "value": 7},
        effect="read",
        modes=("simulation",),
        roles=("robot_agent",),
        requires_sandbox=False,
    ))
""",
    )
    host = FireClawPluginHost()

    report = load_fireclaw_extensions(
        host,
        (tmp_path,),
        mode="simulation",
        role="robot_agent",
    )

    assert report.ok
    assert report.tool_ids == ("example_inspect",)
    assert host.get("tool", "example_inspect") is not None
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_simulation_profile(),
    )
    result = runtime.execute("example_inspect", {})
    assert result.status == "executed"
    assert result.output == {"status": "succeeded", "value": 7}


def test_loader_accepts_public_sdk_plugin_without_core_tool_import(
    tmp_path: Path,
) -> None:
    _write_extension(
        tmp_path,
        "example.public-sdk",
        "plugin.py",
        """
from fireclaw_plugin_sdk import ToolSpec

def register(api):
    api.register_tool(ToolSpec(
        name="public_sdk_status",
        description="Read a public SDK status.",
        input_schema={"type": "object", "additionalProperties": False},
        handler=lambda arguments: {"status": "succeeded"},
        roles=("robot_agent",),
        modes=("simulation",),
    ))
""",
    )
    host = FireClawPluginHost()

    report = load_fireclaw_extensions(
        host,
        (tmp_path,),
        mode="simulation",
        role="robot_agent",
    )

    assert report.ok
    assert report.tool_ids == ("public_sdk_status",)
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_simulation_profile(),
    )
    result = runtime.execute("public_sdk_status", {})
    assert result.status == "executed"
    assert result.output == {"status": "succeeded"}


def test_disabled_extension_is_not_imported(tmp_path: Path) -> None:
    marker = tmp_path / "imported"
    _write_extension(
        tmp_path,
        "example.disabled",
        "plugin.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n",
    )
    host = FireClawPluginHost()

    report = load_fireclaw_extensions(
        host,
        (tmp_path,),
        mode="simulation",
        role="robot_agent",
        plugin_configs={"example.disabled": {"enabled": False}},
    )

    assert report.disabled_plugin_ids == ("example.disabled",)
    assert not marker.exists()
    assert not host.records()


def test_manifest_rejects_entrypoint_escape_without_importing_code(tmp_path: Path) -> None:
    extension = tmp_path / "unsafe"
    extension.mkdir()
    (extension / "fireclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "example.unsafe",
                "api_version": "1",
                "entrypoint": "../outside.py",
            }
        ),
        encoding="utf-8",
    )
    result = discover_fireclaw_extensions((tmp_path,))

    assert result.candidates == ()
    assert result.diagnostics[0].code == "manifest_invalid"


def test_navigation_extension_is_discovered_without_gateway_registration_code() -> None:
    host = FireClawPluginHost()

    report = load_fireclaw_extensions(
        host,
        (Path("extensions"),),
        mode="simulation",
        role="robot_agent",
        services={"adapter": "dry-run", "gateway_config": None},
    )

    loaded_ids = {record.plugin_id for record in report.loaded}
    assert "fireclaw.navigation.move-base" in loaded_ids
    assert "move_base_set_parameters" in report.tool_ids


def test_navigation_plugin_owns_manifest_keyed_mutation_config() -> None:
    host = FireClawPluginHost()
    backend = object()

    report = load_fireclaw_extensions(
        host,
        (Path("extensions"),),
        mode="real",
        role="robot_agent",
        plugin_configs={
            "fireclaw.navigation.move-base": {
                "real_mutation_enabled": True,
                "real_mutable_parameters": ["dwa/max_vel_x"],
            }
        },
        services={"fireclaw.navigation.move-base.backend": backend},
    )

    assert report.ok
    set_tool = host.get("tool", "move_base_set_parameters")
    assert set_tool is not None
    assert set_tool.metadata["extension_owned"] is True
