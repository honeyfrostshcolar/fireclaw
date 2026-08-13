from __future__ import annotations

from pathlib import Path
import stat

import pytest

from fireclaw_core.deployment.profile import load_runtime_deployment_profile
from fireclaw_core.deployment.systemd_service import (
    SystemctlResult,
    SystemdServiceError,
    control_systemd_user_service,
    inspect_systemd_user_service,
    install_systemd_user_service,
    render_systemd_user_unit,
    systemd_unit_name,
    uninstall_systemd_user_service,
)


class _Systemctl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.enabled = False
        self.active = False

    def run(self, args):
        command = tuple(str(item) for item in args)
        self.calls.append(command)
        action = command[0]
        returncode = 0
        stdout = ""
        if action == "enable":
            self.enabled = True
        elif action == "disable":
            self.enabled = False
        elif action in {"start", "restart"}:
            self.active = True
        elif action == "stop":
            self.active = False
        elif action == "is-enabled":
            returncode = 0 if self.enabled else 1
            stdout = "enabled\n" if self.enabled else "disabled\n"
        elif action == "is-active":
            returncode = 0 if self.active else 3
            stdout = "active\n" if self.active else "inactive\n"
        return SystemctlResult(
            argv=("systemctl", "--user", *command),
            returncode=returncode,
            stdout=stdout,
        )


def _layout(tmp_path: Path, *, mode: str = "simulation"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    setup = tmp_path / "setup.bash"
    setup.write_text("export ROS_DISTRO=noetic\n", encoding="utf-8")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    output = tmp_path / "deployments"
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "robot-data"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[server]
host = "127.0.0.1"
port = 8766
data_dir = "mission-data"

[deployment]
id = "robot-1"
mode = "{mode}"
output_root = "{output}"

[deployment.ros1]
distro = "noetic"
setup_files = ["{setup}"]

[plugins]
paths = ["{plugin}"]
selected = ["example.runtime"]
""".strip(),
        encoding="utf-8",
    )
    profile = load_runtime_deployment_profile(profile_path)
    release = output / "robot-1" / "releases" / "fingerprint-1"
    unit_dir = release / "systemd"
    unit_dir.mkdir(parents=True)
    unit = unit_dir / systemd_unit_name(profile)
    unit.write_text(render_systemd_user_unit(profile), encoding="utf-8")
    unit.chmod(0o600)

    def status_probe(profile_path, *, output_root, check_runtime):
        assert check_runtime is False
        return {"status": "installed", "release_dir": str(release)}

    return profile_path, profile, release, status_probe


def test_rendered_unit_preserves_reverse_shutdown_and_mode_restart_policy(
    tmp_path: Path,
) -> None:
    _, simulation, _, _ = _layout(tmp_path / "simulation")
    _, real, _, _ = _layout(tmp_path / "real", mode="real")

    simulation_unit = render_systemd_user_unit(simulation)
    real_unit = render_systemd_user_unit(real)

    assert "KillMode=mixed" in simulation_unit
    assert "KillSignal=SIGINT" in simulation_unit
    assert "TimeoutStopSec=120" in simulation_unit
    assert "Restart=on-failure" in simulation_unit
    assert "RestartPreventExitStatus=2 78" in simulation_unit
    assert "Restart=no" in real_unit
    assert str(simulation.deployment_root / "current" / "bin" / "fireclaw-runtime") in simulation_unit
    assert "FIRECLAW_GATEWAY_TOKEN" not in simulation_unit


def test_install_is_atomic_owned_and_activates_in_explicit_order(
    tmp_path: Path,
) -> None:
    profile_path, _, _, status_probe = _layout(tmp_path / "deployment")
    installed_dir = tmp_path / "systemd-user"
    runner = _Systemctl()

    result = install_systemd_user_service(
        profile_path,
        unit_dir=installed_dir,
        runner=runner,
        status_probe=status_probe,
    )

    target = Path(result["unit_path"])
    assert target.is_file()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert runner.calls == [
        ("daemon-reload",),
        ("enable", "fireclaw-robot-1.service"),
        ("restart", "fireclaw-robot-1.service"),
    ]
    status = inspect_systemd_user_service(
        profile_path,
        unit_dir=installed_dir,
        runner=runner,
        status_probe=status_probe,
    )
    assert status["status"] == "active"
    assert status["enabled"] is True
    assert status["active"] is True


def test_install_refuses_to_overwrite_unmanaged_unit(tmp_path: Path) -> None:
    profile_path, _, _, status_probe = _layout(tmp_path / "deployment")
    installed_dir = tmp_path / "systemd-user"
    installed_dir.mkdir()
    (installed_dir / "fireclaw-robot-1.service").write_text(
        "[Service]\nExecStart=/bin/false\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemdServiceError) as exc_info:
        install_systemd_user_service(
            profile_path,
            unit_dir=installed_dir,
            runner=_Systemctl(),
            status_probe=status_probe,
        )

    assert exc_info.value.code == "service_unit_conflict"


def test_control_and_uninstall_require_the_exact_generated_unit(
    tmp_path: Path,
) -> None:
    profile_path, _, _, status_probe = _layout(tmp_path / "deployment")
    installed_dir = tmp_path / "systemd-user"
    runner = _Systemctl()
    install_systemd_user_service(
        profile_path,
        unit_dir=installed_dir,
        runner=runner,
        status_probe=status_probe,
    )

    stopped = control_systemd_user_service(
        profile_path,
        "stop",
        unit_dir=installed_dir,
        runner=runner,
        status_probe=status_probe,
    )
    removed = uninstall_systemd_user_service(
        profile_path,
        unit_dir=installed_dir,
        runner=runner,
        status_probe=status_probe,
    )

    assert stopped["action"] == "stop"
    assert removed["status"] == "uninstalled"
    assert not Path(removed["unit_path"]).exists()
    assert runner.calls[-3:] == [
        ("stop", "fireclaw-robot-1.service"),
        ("disable", "fireclaw-robot-1.service"),
        ("daemon-reload",),
    ]


def test_status_reports_stale_without_calling_systemctl(tmp_path: Path) -> None:
    profile_path, _, _, status_probe = _layout(tmp_path / "deployment")
    installed_dir = tmp_path / "systemd-user"
    runner = _Systemctl()
    result = install_systemd_user_service(
        profile_path,
        unit_dir=installed_dir,
        runner=runner,
        enable=False,
        start=False,
        status_probe=status_probe,
    )
    Path(result["unit_path"]).write_text(
        "# Managed by FireClaw. Do not edit this installed copy.\n",
        encoding="utf-8",
    )
    runner.calls.clear()

    status = inspect_systemd_user_service(
        profile_path,
        unit_dir=installed_dir,
        runner=runner,
        status_probe=status_probe,
    )

    assert status["status"] == "stale"
    assert runner.calls == []
