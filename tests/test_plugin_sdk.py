from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.tool_runtime import AgentTool, AgentToolRuntime
from fireclaw_core.execution.action_runtime import (
    RegisteredActionBackend,
    RobotActionRuntime,
)
from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile
from fireclaw_plugin_sdk import PhysicalToolSpec, ToolSpec


def _profile(tmp_path: Path) -> DeploymentProfile:
    workspace = tmp_path / "sdk-plugin"
    return DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=SandboxProfile(
            workspace_root=workspace,
            allowed_workspace_roots=(workspace,),
        ),
    )


def test_public_tool_spec_is_converted_at_host_boundary(tmp_path: Path) -> None:
    host = FireClawPluginHost()
    host.activate(
        "example.public-sdk",
        lambda api: api.register_tool(
            ToolSpec(
                name="sdk_inspect",
                description="Read a deterministic SDK value.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=lambda _arguments: {"value": 7},
                roles=("robot_agent",),
                modes=("simulation",),
            )
        ),
        trust_level="trusted",
    )

    contribution = host.get("tool", "sdk_inspect")
    assert contribution is not None
    assert isinstance(contribution.value, AgentTool)

    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path),
    )
    result = runtime.execute("sdk_inspect", {})
    assert result.status == "executed"
    assert result.output == {"value": 7}


def test_tool_spec_rejects_physical_effect() -> None:
    try:
        ToolSpec(
            name="unsafe_physical",
            description="Must be a physical Skill instead.",
            input_schema={"type": "object"},
            handler=lambda _arguments: None,
            effect="physical",  # type: ignore[arg-type]
        )
    except ValueError as error:
        assert "Unsupported ToolSpec effect" in str(error)
    else:  # pragma: no cover - keeps the assertion explicit for test readers.
        raise AssertionError("physical ToolSpec unexpectedly accepted")


def test_public_physical_tool_uses_plugin_handler_without_adapter_method() -> None:
    host = FireClawPluginHost()
    host.activate(
        "example.physical-sdk",
        lambda api: api.register_physical_tool(
            PhysicalToolSpec(
                plugin_id="example.physical-sdk",
                name="inspect_fixture",
                label="Inspect fixture",
                description="Run a Plugin-owned fixture inspection.",
                input_schema={
                    "type": "object",
                    "properties": {"fixture": {"type": "string"}},
                    "required": ["fixture"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                action="inspect_fixture_action",
                handler=lambda arguments, **_kwargs: {
                    "status": "succeeded",
                    "data": {"fixture": arguments["fixture"]},
                },
                dry_run_only=True,
                timeout_seconds=10.0,
                cancellation_ack_timeout_seconds=0.5,
            )
        ),
        trust_level="trusted",
    )

    contribution = host.get("physical_capability", "inspect_fixture")
    assert contribution is not None
    robot = DryRunRobotAdapter(robot_id="physical-sdk-test")
    runtime = RobotActionRuntime(backend=RegisteredActionBackend(robot))
    registry = SkillRegistry(skills={}, host=host)
    skill = registry.register_plugin(
        contribution.value,
        robot=robot,
        action_runtime=runtime,
    )

    result = skill.run({"fixture": "door-a"})
    assert result.ok is True
    assert result.action == "inspect_fixture_action"
    assert result.data["fixture"] == "door-a"
    assert skill.timeout_seconds == 10.0
    assert skill.cancellation_ack_timeout_seconds == 0.5
    assert skill.metadata["cancellation_ack_timeout_seconds"] == 0.5


def test_public_physical_tool_cannot_forge_plugin_owner() -> None:
    host = FireClawPluginHost()
    try:
        host.activate(
            "example.physical-sdk",
            lambda api: api.register_physical_tool(
                PhysicalToolSpec(
                    plugin_id="another.plugin",
                    name="forged_fixture",
                    label="Forged fixture",
                    description="Must be rejected by the host identity boundary.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    action="forged_fixture_action",
                    handler=lambda _arguments: {"status": "succeeded"},
                )
            ),
            trust_level="trusted",
        )
    except ValueError as error:
        assert "plugin_id" in str(error)
    else:  # pragma: no cover - explicit assertion for the security contract.
        raise AssertionError("physical Tool owner mismatch was accepted")
    assert host.get("physical_capability", "forged_fixture") is None
