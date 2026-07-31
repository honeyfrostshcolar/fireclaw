from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.execution.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.navigation.move_base_plugin import (
    InMemoryMoveBaseBackend,
    MoveBaseParameterPolicy,
    move_base_parameter_catalog,
    register_move_base_navigation_plugin,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile


def _profile(tmp_path: Path, mode: str) -> DeploymentProfile:
    profile_mode = "real" if mode.startswith("real") else "simulation"
    root = tmp_path / mode
    return DeploymentProfile(
        mode=profile_mode,  # type: ignore[arg-type]
        role="robot_agent",
        sandbox=SandboxProfile(
            workspace_root=root,
            allowed_workspace_roots=(root,),
        ),
    )


def test_simulation_registers_catalog_and_bounded_tools(tmp_path: Path) -> None:
    host = FireClawPluginHost()
    backend = InMemoryMoveBaseBackend()
    record = register_move_base_navigation_plugin(
        host,
        backend,
        mode="simulation",
    )

    assert record.status == "active"
    assert {
        "move_base_parameter_catalog",
        "move_base_navigation_status",
        "move_base_get_parameters",
        "move_base_set_parameters",
        "move_base_cancel_navigation",
        "move_base_clear_costmaps",
    } <= {
        contribution.contribution_id
        for contribution in host.contributions("tool")
    }

    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path, "simulation"),
    )
    names = {schema["function"]["name"] for schema in runtime.tool_schemas()}
    assert "move_base_set_parameters" in names
    result = runtime.execute(
        "move_base_set_parameters",
        {
            "scope": "dwa",
            "parameters": {
                "max_vel_x": 0.35,
                "sim_time": 2.0,
            },
        },
    )
    assert result.status == "executed"
    assert backend.parameters["dwa"]["max_vel_x"] == 0.35


def test_parameter_policy_rejects_unknown_scope_or_unsafe_range() -> None:
    policy = MoveBaseParameterPolicy(mode="simulation")
    assert policy.validate_updates(
        "dwa",
        {"controller_frequency": 10.0},
    )
    assert policy.validate_updates(
        "dwa",
        {"max_vel_x": 100.0},
    )
    assert policy.validate_updates(
        "dwa",
        {"min_vel_x": 0.5, "max_vel_x": 0.2},
    )
    catalog = move_base_parameter_catalog(mode="simulation")
    assert any(
        item["name"] == "max_vel_x" and item["mutable"]
        for item in catalog
    )


def test_real_mode_hides_mutation_by_default_and_requires_approval_when_opened(
    tmp_path: Path,
) -> None:
    host = FireClawPluginHost()
    backend = InMemoryMoveBaseBackend()
    register_move_base_navigation_plugin(
        host,
        backend,
        mode="real",
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path, "real"),
    )
    assert runtime.projection("move_base_set_parameters") is None
    assert runtime.projection("move_base_get_parameters") is not None

    approved_host = FireClawPluginHost()
    register_move_base_navigation_plugin(
        approved_host,
        backend,
        mode="real",
        real_mutation_enabled=True,
        real_mutable_parameters=("max_vel_x",),
    )
    approved_runtime = AgentToolRuntime(
        plugin_host=approved_host,
        profile=_profile(tmp_path, "real-approved"),
    )
    projection = approved_runtime.projection("move_base_set_parameters")
    assert projection is not None
    assert projection.decision.status == "require_approval"
    result = approved_runtime.execute(
        "move_base_set_parameters",
        {"scope": "dwa", "parameters": {"max_vel_x": 0.2}},
    )
    assert result.status == "approval_required"
    assert backend.parameters["dwa"].get("max_vel_x") is None


def test_simulation_tool_schema_exposes_named_parameters() -> None:
    host = FireClawPluginHost()
    register_move_base_navigation_plugin(
        host,
        InMemoryMoveBaseBackend(),
        mode="simulation",
    )
    tool = host.get("tool", "move_base_set_parameters").value
    properties = tool.input_schema["properties"]["parameters"]["properties"]
    assert "max_vel_x" in properties
    assert properties["max_vel_x"]["minimum"] == 0.0
    assert "robot_base_frame" not in properties


def test_navigation_motion_is_registered_by_extension_and_has_no_adapter_method(
    tmp_path: Path,
) -> None:
    host = FireClawPluginHost()
    backend = InMemoryMoveBaseBackend()
    report = load_fireclaw_extensions(
        host,
        ("extensions",),
        mode="simulation",
        role="robot_agent",
        services={
            "adapter": "dry-run",
            "move_base_navigation_backend": backend,
        },
    )
    assert "fireclaw.navigation.move-base" in {
        record.plugin_id for record in report.loaded
    }
    contribution = host.get("physical_capability", "navigate_to_point")
    assert contribution is not None

    robot = DryRunRobotAdapter(robot_id="plugin-navigation")
    runtime = RobotActionRuntime(backend=RobotAdapterActionBackend(robot))
    registry = SkillRegistry(skills={}, host=host)
    skill = registry.register_plugin(
        contribution.value,
        robot=robot,
        action_runtime=runtime,
    )
    result = skill.run({"x": 1.0, "y": 2.0})
    assert result.ok is True
    assert result.action == "navigate_to_point"
    assert backend.calls[0]["operation"] == "navigate_to_point"
