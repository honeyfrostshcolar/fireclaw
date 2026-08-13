from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

import pytest

from fireclaw_core.deployment import (
    DeploymentError,
    apply_deployment,
    build_deployment_plan,
    inspect_deployment_status,
    load_plugin_runtime_descriptor,
    load_runtime_deployment_profile,
)
from fireclaw_core.deployment.command import DeploymentCommandResult
from fireclaw_core.plugin.extension_loader import discover_fireclaw_extensions


def _package_xml(name: str) -> str:
    return f"""
<package format="2">
  <name>{name}</name>
  <version>0.1.0</version>
  <description>test package</description>
  <maintainer email="test@example.com">Test</maintainer>
  <license>BSD</license>
</package>
""".strip()


def _write_package(root: Path, name: str) -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "package.xml").write_text(_package_xml(name), encoding="utf-8")
    (package / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.0.2)\nproject(%s)\n" % name,
        encoding="utf-8",
    )
    return package


def _write_plugin(
    root: Path,
    *,
    plugin_id: str = "example.runtime",
    include_system: bool = True,
    include_source: bool = True,
    package_name: str = "runtime_pkg",
) -> Path:
    plugin = root / plugin_id.replace(".", "-")
    (plugin / "plugin").mkdir(parents=True)
    (plugin / "plugin" / "entrypoint.py").write_text(
        "def register(api):\n    return None\n",
        encoding="utf-8",
    )
    (plugin / "runtime").mkdir()
    (plugin / "launch").mkdir()
    (plugin / "launch" / "runtime.launch").write_text(
        "<launch><arg name=\"runtime_config\"/></launch>\n",
        encoding="utf-8",
    )
    (plugin / "ros_ws" / "src").mkdir(parents=True)
    _write_package(plugin / "ros_ws" / "src", package_name)
    providers: list[dict[str, Any]] = []
    if include_system:
        providers.append(
            {
                "kind": "system_ros1",
                "ros_distro": "noetic",
                "packages": [package_name],
            }
        )
    if include_source:
        providers.append(
            {
                "kind": "ros1_catkin",
                "ros_distro": "noetic",
                "packages": [package_name],
                "workspace": "ros_ws",
                "source_paths": ["src"],
            }
        )
    descriptor = {
        "schema_version": "1",
        "runtime_id": f"{plugin_id}.runtime",
        "providers": providers,
        "launch": {
            "file": "launch/runtime.launch",
            "arguments": [
                {
                    "name": "runtime_config",
                    "binding": "runtime.config_file",
                    "type": "path",
                }
            ],
        },
        "readiness": [
            {"kind": "ros1_package", "name": package_name},
            {"kind": "ros1_action", "name": "/move_base"},
        ],
    }
    (plugin / "runtime" / "fireclaw.runtime.json").write_text(
        json.dumps(descriptor),
        encoding="utf-8",
    )
    (plugin / "fireclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_id,
                "api_version": "1",
                "entrypoint": "plugin/entrypoint.py",
                "runtime": "runtime/fireclaw.runtime.json",
                "trust_level": "trusted",
            }
        ),
        encoding="utf-8",
    )
    return plugin


def _write_profile(
    tmp_path: Path,
    *,
    plugin: Path,
    setup_file: Path,
    output_root: Path,
    plugin_ids: Sequence[str] = ("example.runtime",),
    manage_mission_gateway: bool = False,
) -> Path:
    runtime_config = tmp_path / "runtime.yaml"
    runtime_config.write_text("enabled: true\n", encoding="utf-8")
    profile = tmp_path / "robot.toml"
    selected = ", ".join(json.dumps(item) for item in plugin_ids)
    server = (
        """
[server]
host = "127.0.0.1"
port = 8766
data_dir = "mission-data"
""".strip()
        if manage_mission_gateway
        else ""
    )
    profile.write_text(
        f"""
[robot]
id = "test-robot"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "robot-data"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[deployment]
id = "test-robot"
mode = "simulation"
output_root = "{output_root}"

{server}

[deployment.ros1]
distro = "noetic"
setup_files = ["{setup_file}"]

[deployment.bindings.runtime]
config_file = "{runtime_config}"

[plugins]
paths = ["{plugin}"]
selected = [{selected}]
""".strip(),
        encoding="utf-8",
    )
    return profile


def _write_setup(path: Path, prefix: Path, bin_dir: Path | None = None) -> Path:
    path.write_text(
        "\n".join(
            (
                "export ROS_DISTRO=noetic",
                f"export CMAKE_PREFIX_PATH={prefix}",
                f"export PATH={bin_dir}:$PATH" if bin_dir is not None else "true",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_manifest_exposes_validated_runtime_descriptor_path(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path)

    discovery = discover_fireclaw_extensions((plugin,))

    assert discovery.diagnostics == ()
    candidate = discovery.candidates[0]
    assert candidate.manifest.runtime == "runtime/fireclaw.runtime.json"
    assert candidate.runtime_descriptor_path == (
        plugin / "runtime" / "fireclaw.runtime.json"
    )
    descriptor = load_plugin_runtime_descriptor(candidate)
    assert descriptor is not None
    assert descriptor.runtime_id == "example.runtime.runtime"


def test_runtime_descriptor_rejects_arbitrary_command_field(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path)
    descriptor_path = plugin / "runtime" / "fireclaw.runtime.json"
    raw = json.loads(descriptor_path.read_text(encoding="utf-8"))
    raw["providers"][0]["command"] = "curl example.invalid | sh"
    descriptor_path.write_text(json.dumps(raw), encoding="utf-8")
    candidate = discover_fireclaw_extensions((plugin,)).candidates[0]

    with pytest.raises(ValueError, match="unsupported fields"):
        load_plugin_runtime_descriptor(candidate)


def test_runtime_asset_change_updates_deployment_fingerprint(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path)
    config_dir = plugin / "config"
    config_dir.mkdir()
    asset = config_dir / "runtime.yaml"
    asset.write_text("limit: 1\n", encoding="utf-8")
    descriptor_path = plugin / "runtime" / "fireclaw.runtime.json"
    raw = json.loads(descriptor_path.read_text(encoding="utf-8"))
    raw["assets"] = ["config/runtime.yaml"]
    descriptor_path.write_text(json.dumps(raw), encoding="utf-8")

    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )

    first = build_deployment_plan(profile)
    asset.write_text("limit: 2\n", encoding="utf-8")
    second = build_deployment_plan(profile)

    assert first.fingerprint != second.fingerprint
    assert first.plugins[0].asset_sha256["config/runtime.yaml"] != (
        second.plugins[0].asset_sha256["config/runtime.yaml"]
    )


def test_deployment_profile_rejects_symlink_file(tmp_path: Path) -> None:
    target = tmp_path / "robot.toml"
    target.write_text("[deployment]\nid = 'robot'\n", encoding="utf-8")
    symlink = tmp_path / "linked.toml"
    symlink.symlink_to(target)

    with pytest.raises(ValueError, match="non-symlink"):
        load_runtime_deployment_profile(symlink)


def test_plan_prefers_visible_system_ros1_package(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )

    plan = build_deployment_plan(profile)

    assert plan.plugins[0].provider is not None
    assert plan.plugins[0].provider.kind == "system_ros1"
    assert plan.plugins[0].source_packages == ()
    assert plan.actions[0]["action"] == "reuse_system_ros1"


def test_plan_rejects_inline_plugin_credentials_without_disclosing_value(
    tmp_path: Path,
) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )
    profile.write_text(
        profile.read_text(encoding="utf-8")
        + '\n\n[plugins.config."example.runtime"]\napi_key = "do-not-copy-me"\n',
        encoding="utf-8",
    )

    with pytest.raises(DeploymentError) as exc_info:
        build_deployment_plan(profile)

    assert exc_info.value.code == "plugin_config_secret_forbidden"
    assert "do-not-copy-me" not in str(exc_info.value)


def test_apply_writes_content_addressed_release_and_static_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )
    plan = build_deployment_plan(profile)

    first = apply_deployment(plan)
    second = apply_deployment(plan)
    status = inspect_deployment_status(profile, check_runtime=False)

    release = Path(first["release_dir"])
    assert first["status"] == "installed"
    assert first["reused"] is False
    assert second["reused"] is True
    assert (plan.profile.deployment_root / "current").resolve() == release
    assert status["status"] == "installed"
    assert status["static_checks"]["ok"] is True
    assert str(release) in (release / "setup.bash").read_text(encoding="utf-8")
    assert ".staging-" not in (release / "setup.bash").read_text(encoding="utf-8")
    ElementTree.parse(release / "fireclaw_bringup.launch")
    with (release / "fireclaw.generated.toml").open("rb") as handle:
        generated = tomllib.load(handle)
    assert generated["plugins"]["paths"] == [str(plugin)]
    assert generated["plugins"]["config"]["example.runtime"]["enabled"] is True
    assert generated["robot_gateway"]["profile_path"] == str(profile.resolve())
    assert "embodied_runtime_mode" not in generated["robot_gateway"]
    assert plan.to_dict()["python_executable"] == str(Path(sys.executable).resolve())
    assert os.access(release / "bin" / "fireclaw-bringup", os.X_OK)
    assert os.access(release / "bin" / "fireclaw-gateway", os.X_OK)
    assert os.access(release / "bin" / "fireclaw-runtime", os.X_OK)
    gateway_wrapper = (release / "bin" / "fireclaw-gateway").read_text(
        encoding="utf-8"
    )
    cli_prefix = f"{Path(sys.executable).resolve()} -m fireclaw_core"
    assert f"{cli_prefix} deploy status" in gateway_wrapper
    assert f"exec {cli_prefix} robot-gateway" in gateway_wrapper
    assert "\nfireclaw " not in gateway_wrapper
    assert "--no-runtime-check" not in gateway_wrapper
    assert str(profile.resolve()) in gateway_wrapper
    assert str((release / "fireclaw.generated.toml").resolve()) in gateway_wrapper
    assert gateway_wrapper.index('robot-gateway "$@"') < gateway_wrapper.index(
        "--config"
    )
    runtime_wrapper = (release / "bin" / "fireclaw-runtime").read_text(
        encoding="utf-8"
    )
    assert f"exec {cli_prefix} deploy run" in runtime_wrapper
    assert str(profile.resolve()) in runtime_wrapper
    assert runtime_wrapper.index('deploy run "$@"') < runtime_wrapper.index(
        "--profile"
    )
    assert first["runtime"] == str(release / "bin" / "fireclaw-runtime")

    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    robot_profile = load_robot_capability_profile(
        generated["robot_gateway"]["profile_path"]
    )
    assert robot_profile.ros1_config == str((profile.parent / "ros1.yaml").resolve())
    assert robot_profile.data_dir == (profile.parent / "robot-data").resolve()


def test_status_detects_tampered_release_artifact(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )
    plan = build_deployment_plan(profile)
    apply_deployment(plan)
    (plan.release_dir / "fireclaw_bringup.launch").write_text(
        "<launch><!-- tampered --></launch>\n",
        encoding="utf-8",
    )

    status = inspect_deployment_status(profile, check_runtime=False)

    assert status["status"] == "invalid"
    assert "fireclaw_bringup.launch" in status["static_checks"]["mismatched"]


def test_apply_generates_fixed_mission_gateway_wrapper_when_server_is_managed(
    tmp_path: Path,
) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
        manage_mission_gateway=True,
    )

    plan = build_deployment_plan(profile)
    result = apply_deployment(plan)

    assert plan.profile.mission_gateway.enabled is True
    assert plan.profile.mission_gateway.base_url == "http://127.0.0.1:8766"
    wrapper = Path(result["mission_gateway"])
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    rendered = wrapper.read_text(encoding="utf-8")
    cli_prefix = f"{Path(sys.executable).resolve()} -m fireclaw_core"
    assert f"exec {cli_prefix} serve" in rendered
    assert f"--config {profile.resolve()}" in rendered
    assert f"--robot-profile {profile.resolve()}" in rendered
    assert 'if [[ "$#" -ne 0 ]]' in rendered


def test_profile_can_explicitly_leave_mission_gateway_external(
    tmp_path: Path,
) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
        manage_mission_gateway=True,
    )
    with profile.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n\n[deployment.supervisor.mission_gateway]\n"
            "enabled = false\n"
        )

    plan = build_deployment_plan(profile)
    result = apply_deployment(plan)

    assert plan.profile.mission_gateway.enabled is False
    assert result["mission_gateway"] is None
    assert not (plan.release_dir / "bin" / "fireclaw-mission-gateway").exists()


class _FakeCatkinRunner:
    def __init__(self, *, prefix: Path, bin_dir: Path) -> None:
        self.prefix = prefix
        self.bin_dir = bin_dir
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> DeploymentCommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command[0] == "/bin/bash":
            output = "\x00".join(
                (
                    "ROS_DISTRO=noetic",
                    f"CMAKE_PREFIX_PATH={self.prefix}",
                    f"PATH={self.bin_dir}:{os.environ.get('PATH', '')}",
                    "",
                )
            )
            return DeploymentCommandResult(command, 0, output, 0.01)
        install_arg = next(
            item for item in command if item.startswith("-DCMAKE_INSTALL_PREFIX=")
        )
        install = Path(install_arg.split("=", 1)[1])
        install.mkdir(parents=True)
        _write_package(install / "share", "runtime_pkg")
        (install / "setup.bash").write_text(
            f"export ROS_DISTRO=noetic\nexport CMAKE_PREFIX_PATH={install}:{self.prefix}\n",
            encoding="utf-8",
        )
        return DeploymentCommandResult(command, 0, "fake catkin success\n", 0.01)


class _ReadyRosRunner:
    def __init__(self, *, prefix: Path, bin_dir: Path) -> None:
        self.prefix = prefix
        self.bin_dir = bin_dir

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> DeploymentCommandResult:
        command = tuple(str(item) for item in argv)
        if command[0] == "/bin/bash":
            output = "\x00".join(
                (
                    "ROS_DISTRO=noetic",
                    f"CMAKE_PREFIX_PATH={self.prefix}",
                    f"PATH={self.bin_dir}:{os.environ.get('PATH', '')}",
                    "",
                )
            )
        elif Path(command[0]).name == "rostopic":
            output = "/move_base/goal\n/move_base/status\n/move_base/cancel\n"
        else:
            output = ""
        return DeploymentCommandResult(command, 0, output, 0.01)


def test_source_fallback_uses_fixed_catkin_make_argv(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, include_system=False)
    empty_prefix = tmp_path / "empty-prefix"
    empty_prefix.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    catkin_make = bin_dir / "catkin_make"
    catkin_make.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    catkin_make.chmod(0o755)
    setup = _write_setup(tmp_path / "setup.bash", empty_prefix, bin_dir)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )
    runner = _FakeCatkinRunner(prefix=empty_prefix, bin_dir=bin_dir)
    plan = build_deployment_plan(profile, runner=runner)

    result = apply_deployment(plan, runner=runner)

    assert plan.plugins[0].provider is not None
    assert plan.plugins[0].provider.kind == "ros1_catkin"
    catkin_commands = [command for command in runner.commands if command[0] == str(catkin_make)]
    assert len(catkin_commands) == 1
    command = catkin_commands[0]
    assert command[1:3] == ("-C", str(plan.release_dir / "workspace"))
    assert command[-1] == "install"
    assert result["status"] == "installed"
    assert (plan.release_dir / "install" / "setup.bash").is_file()


def test_status_reports_ready_when_declared_ros_graph_is_present(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, include_source=False)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rostopic = bin_dir / "rostopic"
    rostopic.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    rostopic.chmod(0o755)
    setup = _write_setup(tmp_path / "setup.bash", prefix, bin_dir)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )
    runner = _ReadyRosRunner(prefix=prefix, bin_dir=bin_dir)
    plan = build_deployment_plan(profile, runner=runner)
    apply_deployment(plan, runner=runner)

    status = inspect_deployment_status(profile, runner=runner)

    assert status["status"] == "ready"
    assert status["runtime_checks"]["ok"] is True
    assert all(item["ok"] for item in status["runtime_checks"]["checks"])


def test_plan_rejects_duplicate_source_packages_across_plugins(tmp_path: Path) -> None:
    first = _write_plugin(
        tmp_path,
        plugin_id="example.first",
        include_system=False,
    )
    second = _write_plugin(
        tmp_path,
        plugin_id="example.second",
        include_system=False,
    )
    empty_prefix = tmp_path / "empty-prefix"
    empty_prefix.mkdir()
    setup = _write_setup(tmp_path / "setup.bash", empty_prefix)
    profile = _write_profile(
        tmp_path,
        plugin=tmp_path,
        setup_file=setup,
        output_root=tmp_path / "deployments",
        plugin_ids=("example.first", "example.second"),
    )
    assert first.is_dir() and second.is_dir()

    with pytest.raises(DeploymentError) as exc_info:
        build_deployment_plan(profile)

    assert exc_info.value.code == "duplicate_source_package"


def test_navigation_plugin_runtime_descriptor_is_production_not_acceptance() -> None:
    extension = (
        Path(__file__).resolve().parents[1]
        / "extensions"
        / "navigation-move-base"
    )
    discovery = discover_fireclaw_extensions((extension,))

    assert discovery.diagnostics == ()
    descriptor = load_plugin_runtime_descriptor(discovery.candidates[0])
    assert descriptor is not None
    assert descriptor.runtime_id == "ros1.move_base"
    assert descriptor.launch is not None
    assert descriptor.launch.file.name == "fireclaw_navigation.launch"
    assert "acceptance" not in str(descriptor.launch.file)
    assert {path.name for path in descriptor.assets} == {
        "amcl.yaml",
        "costmap_common.yaml",
        "dwa_local_planner.yaml",
        "global_costmap.yaml",
        "local_costmap.yaml",
        "move_base.yaml",
    }
    ElementTree.parse(descriptor.launch.file)


@pytest.mark.skipif(
    not Path("/opt/ros/noetic/setup.bash").is_file(),
    reason="requires a local ROS Noetic installation for provider resolution",
)
def test_navigation_plan_reuses_local_noetic_runtime(tmp_path: Path) -> None:
    extension = (
        Path(__file__).resolve().parents[1]
        / "extensions"
        / "navigation-move-base"
    )
    map_file = tmp_path / "map.yaml"
    map_file.write_text("image: map.pgm\nresolution: 0.05\n", encoding="utf-8")
    profile = tmp_path / "navigation-profile.toml"
    profile.write_text(
        f"""
[robot]
id = "navigation-test"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "{tmp_path / 'ros1.yaml'}"
data_dir = "{tmp_path / 'data'}"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[deployment]
mode = "simulation"
output_root = "{tmp_path / 'deployments'}"

[deployment.ros1]
distro = "noetic"
setup_files = ["/opt/ros/noetic/setup.bash"]

[deployment.bindings.navigation]
map_file = "{map_file}"
scan_topic = "/scan"
scan_frame = "base_scan"
cmd_vel_topic = "/cmd_vel"
odom_topic = "/odom"
map_frame = "map"
odom_frame = "odom"
base_frame = "base_link"
footprint = [[-0.2, -0.2], [-0.2, 0.2], [0.2, 0.2], [0.2, -0.2]]
max_linear_velocity = 0.22
max_angular_velocity = 1.0

[plugins]
paths = ["{extension}"]
selected = ["fireclaw.navigation.move-base"]
""".strip(),
        encoding="utf-8",
    )

    plan = build_deployment_plan(profile)

    assert plan.plugins[0].provider is not None
    assert plan.plugins[0].provider.kind == "system_ros1"
    assert {item.name for item in plan.plugins[0].package_evidence} == {
        "move_base",
        "map_server",
        "amcl",
    }
    result = apply_deployment(plan)
    static_status = inspect_deployment_status(profile, check_runtime=False)
    generated_launch = ElementTree.parse(
        Path(result["release_dir"]) / "fireclaw_bringup.launch"
    ).getroot()
    generated_args = {
        item.attrib["name"]
        for item in generated_launch.findall(".//arg")
    }
    assert static_status["status"] == "installed"
    assert "stack_launch" not in generated_args
    assert generated_args == {
        "map_file",
        "scan_topic",
        "scan_frame",
        "cmd_vel_topic",
        "odom_topic",
        "map_frame",
        "odom_frame",
        "base_frame",
        "footprint",
        "max_linear_velocity",
        "max_angular_velocity",
        "max_lateral_velocity",
        "min_translational_velocity",
        "min_angular_velocity",
        "max_linear_acceleration",
        "max_angular_acceleration",
        "max_lateral_acceleration",
        "allow_reverse",
        "laser_max_range",
        "odom_model_type",
        "odom_alpha1",
        "odom_alpha2",
        "odom_alpha3",
        "odom_alpha4",
        "obstacle_range",
        "raytrace_range",
        "inflation_radius",
        "cost_scaling_factor",
        "initial_pose_x",
        "initial_pose_y",
        "initial_pose_yaw",
    }


def test_main_cli_exposes_deploy_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    plugin = _write_plugin(tmp_path)
    prefix = tmp_path / "ros-prefix"
    _write_package(prefix / "share", "runtime_pkg")
    setup = _write_setup(tmp_path / "setup.bash", prefix)
    profile = _write_profile(
        tmp_path,
        plugin=plugin,
        setup_file=setup,
        output_root=tmp_path / "deployments",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["fireclaw", "deploy", "plan", "--profile", str(profile)],
    )
    from fireclaw_core.mission.mission_cli import main

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["plugins"][0]["provider"]["kind"] == "system_ros1"


def test_python_module_entrypoint_routes_deploy_to_mission_cli(monkeypatch) -> None:
    from fireclaw_core import __main__ as package_main

    monkeypatch.setattr("sys.argv", ["python -m fireclaw_core", "deploy", "--help"])
    monkeypatch.setattr(package_main, "_mission_main", lambda: 73)

    assert package_main.main() == 73
