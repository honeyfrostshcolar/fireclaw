from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fireclaw_core.agent.computer_tools import ComputerSandbox
from fireclaw_core.gateway.config import load_config
from fireclaw_core.gateway import gateway as robot_gateway_module
from fireclaw_core.infra.path_security import (
    discover_fireclaw_project_root,
    validate_sandbox_workspace_root,
)
from fireclaw_core.infra.runtime_paths import (
    fireclaw_runtime_directory,
    resolve_fireclaw_runtime_root,
)
from fireclaw_core.policy.deployment import SandboxProfile
from fireclaw_core.policy.deployment import deployment_profile_from_config

_TEST_IMAGE_ID = "sha256:" + ("a" * 64)


@pytest.mark.parametrize(
    "path",
    [
        Path("/"),
        Path("/etc"),
        Path("/etc/fireclaw"),
        Path("/proc/self"),
        Path("/var/run"),
    ],
)
def test_workspace_rejects_system_paths(path: Path) -> None:
    with pytest.raises(ValueError, match="protected host path"):
        validate_sandbox_workspace_root(path)


def test_workspace_rejects_home_and_credential_directories() -> None:
    home = Path.home()

    with pytest.raises(ValueError, match="protected host path"):
        validate_sandbox_workspace_root(home)
    with pytest.raises(ValueError, match="protected host path"):
        validate_sandbox_workspace_root(home / ".ssh")
    with pytest.raises(ValueError, match="protected host path"):
        validate_sandbox_workspace_root(home / ".aws" / "credentials")
    with pytest.raises(ValueError, match="too broad"):
        validate_sandbox_workspace_root(home.parent)


def test_workspace_rejects_os_home_credentials_when_home_env_is_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        import pwd

        os_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pytest.skip("POSIX account home lookup is unavailable")
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    with pytest.raises(ValueError, match="protected host path"):
        validate_sandbox_workspace_root(os_home / ".ssh")


def test_workspace_rejects_project_and_plugin_source_directories() -> None:
    project_root = discover_fireclaw_project_root()
    assert project_root is not None

    for path in (
        project_root,
        project_root / "src",
        project_root / "extensions" / "navigation-move-base",
        project_root / "skills",
        project_root / ".git" / "objects",
    ):
        with pytest.raises(ValueError, match="protected host path"):
            validate_sandbox_workspace_root(path)


def test_workspace_allows_dedicated_project_data_directory(
    tmp_path: Path,
) -> None:
    project_root = discover_fireclaw_project_root()
    assert project_root is not None
    data_root = project_root / "data"
    workspace = data_root / "mission-agent-workspace"

    assert validate_sandbox_workspace_root(
        workspace,
        allowed_roots=(data_root,),
    ) == workspace.resolve(strict=False)
    assert validate_sandbox_workspace_root(
        tmp_path / "robot-workspace",
        allowed_roots=(tmp_path,),
    ) == (tmp_path / "robot-workspace").resolve(strict=False)


def test_workspace_rejects_paths_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="outside allowed roots"):
        validate_sandbox_workspace_root(
            tmp_path.parent / "other" / "workspace",
            allowed_roots=(tmp_path,),
        )


def test_deployment_workspace_is_resolved_under_fixed_runtime_root(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    allowed = runtime_root / "data" / "robot" / "agent-workspace"
    profile = deployment_profile_from_config(
        {
            "mode": "simulation",
            "sandbox": {
                    "robot_agent": {
                        "enabled": True,
                        "image": "fireclaw-agent-sim:test",
                        "image_digest": _TEST_IMAGE_ID,
                        "workspace_root": "data/robot/agent-workspace/session-a",
                    }
            },
        },
        role="robot_agent",
        default_workspace_root=allowed,
        allowed_workspace_roots=(allowed,),
        path_base=runtime_root,
    )

    assert profile.sandbox.workspace_root == (
        allowed / "session-a"
    ).resolve()


def test_deployment_workspace_cannot_reuse_authoritative_data_root(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    allowed = runtime_root / "data" / "mission" / "agent-workspace"

    with pytest.raises(ValueError, match="outside allowed roots"):
        deployment_profile_from_config(
            {
                "mode": "simulation",
                "sandbox": {
                    "mission_agent": {
                        "enabled": True,
                        "image": "fireclaw-agent-sim:test",
                        "image_digest": _TEST_IMAGE_ID,
                        "workspace_root": "data/mission",
                    }
                },
            },
            role="mission_agent",
            default_workspace_root=allowed,
            allowed_workspace_roots=(allowed,),
            path_base=runtime_root,
        )


def test_existing_parent_symlink_cannot_escape_allowed_root(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside allowed roots"):
        validate_sandbox_workspace_root(
            allowed / "linked" / "workspace",
            allowed_roots=(allowed,),
        )


def test_sandbox_rejects_workspace_retargeted_after_validation(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    workspace = allowed / "workspace"
    outside.mkdir()
    sandbox = ComputerSandbox(
        SandboxProfile(
            enabled=True,
            image="fireclaw-agent-sim:test",
            image_digest=_TEST_IMAGE_ID,
            workspace_root=workspace,
            allowed_workspace_roots=(allowed,),
        ),
        process_factory=lambda *args, **kwargs: None,  # type: ignore[arg-type]
    )
    workspace.rmdir()
    workspace.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        ValueError,
        match="outside allowed roots|changed through a symlink",
    ):
        sandbox.execute({"argv": ["pwd"]})


def test_runtime_root_resolution_is_independent_of_launch_directory(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "deployment"
    config_dir.mkdir()
    config_path = config_dir / "fireclaw.toml"
    config_path.write_text("", encoding="utf-8")

    first = resolve_fireclaw_runtime_root(
        config_path=config_path,
        launch_cwd=tmp_path / "first",
        env={},
        home=tmp_path / "home",
    )
    second = resolve_fireclaw_runtime_root(
        config_path=config_path,
        launch_cwd=tmp_path / "second",
        env={},
        home=tmp_path / "home",
    )

    assert first == config_dir.resolve()
    assert second == first


def test_config_loads_explicit_runtime_root(tmp_path: Path) -> None:
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        '[runtime]\nroot_dir = "/srv/fireclaw/robot-1"\n',
        encoding="utf-8",
    )

    assert load_config(config_path)["runtime_root"] == "/srv/fireclaw/robot-1"


def test_runtime_root_uses_fireclaw_home_and_rejects_sensitive_path(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "fireclaw-home"

    assert resolve_fireclaw_runtime_root(
        env={"FIRECLAW_HOME": str(runtime_root)},
        launch_cwd=tmp_path,
        home=tmp_path,
    ) == runtime_root.resolve()

    with pytest.raises(ValueError, match="protected path"):
        resolve_fireclaw_runtime_root(
            configured=Path.home() / ".ssh",
            env={},
        )
    with pytest.raises(ValueError, match="must be an absolute path"):
        resolve_fireclaw_runtime_root(
            env={"FIRECLAW_HOME": "relative/runtime"},
            launch_cwd=tmp_path,
        )


def test_runtime_directory_changes_and_restores_process_cwd(
    tmp_path: Path,
) -> None:
    original = Path.cwd()
    runtime_root = tmp_path / "runtime"

    with fireclaw_runtime_directory(runtime_root) as active:
        assert active == runtime_root.resolve()
        assert Path.cwd() == active
        assert active.stat().st_mode & 0o777 == 0o700

    assert Path.cwd() == original


def test_robot_gateway_runs_inside_configured_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "robot-runtime"
    config_path = tmp_path / "robot.toml"
    config_path.write_text(
        f'[runtime]\nroot_dir = "{runtime_root}"\n',
        encoding="utf-8",
    )
    observed: dict[str, Path] = {}

    def _fake_run(merged, args) -> int:
        observed["cwd"] = Path.cwd()
        return 0

    monkeypatch.setattr(
        robot_gateway_module,
        "_run_robot_gateway",
        _fake_run,
    )
    original = Path.cwd()

    assert robot_gateway_module.main(["--config", str(config_path)]) == 0
    assert observed["cwd"] == runtime_root.resolve()
    assert Path.cwd() == original


def test_mission_gateway_runs_inside_configured_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fireclaw_core.mission import mission_cli
    from fireclaw_core.gateway import serve as serve_module

    runtime_root = tmp_path / "mission-runtime"
    config_path = tmp_path / "mission.toml"
    config_path.write_text(
        (
            f'[runtime]\nroot_dir = "{runtime_root}"\n\n'
            "[server]\nhost = \"127.0.0.1\"\nport = 0\n"
        ),
        encoding="utf-8",
    )
    observed: dict[str, Path] = {}

    def _fake_run_server_blocking(**kwargs) -> None:
        observed["cwd"] = Path.cwd()

    monkeypatch.setattr(
        serve_module,
        "run_server_blocking",
        _fake_run_server_blocking,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["fireclaw", "serve", "--config", str(config_path)],
    )
    original = Path.cwd()

    assert mission_cli.main() == 0
    assert observed["cwd"] == runtime_root.resolve()
    assert Path.cwd() == original
