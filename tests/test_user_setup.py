from __future__ import annotations

import argparse
from io import StringIO
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment import load_runtime_deployment_profile
from fireclaw_core.infra import operator_cli
from fireclaw_core.infra.user_setup import (
    FireClawSetupError,
    SIMULATION_TEMPLATE_ID,
    handle_setup,
    load_active_profile,
    resolve_active_profile_path,
    setup_fireclaw,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class _Plan:
    fingerprint = "setup-test-fingerprint"

    def to_dict(self) -> dict[str, object]:
        return {"status": "ready", "fingerprint": self.fingerprint}


class _PlanBuilder:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def __call__(self, profile_path: str | Path) -> _Plan:
        self.paths.append(Path(profile_path).resolve(strict=True))
        return _Plan()


def _prepare_fake_simulation_runtime(*, runtime_root, **_kwargs):
    root = Path(runtime_root)
    bundle_root = (
        root
        / "simulation-bundles"
        / "turtlebot3-burger-v1"
        / "releases"
        / ("a" * 64)
    )
    paths = (
        bundle_root / "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        bundle_root
        / "robots/turtlebot3_burger/ros_ws/src/fireclaw_turtlebot3_burger/launch/robot_base.launch",
        bundle_root
        / "robots/turtlebot3_burger/ros_ws/src/turtlebot3/turtlebot3_navigation/maps/map.yaml",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test fixture\n", encoding="utf-8")
    (bundle_root / "extensions").mkdir(exist_ok=True)
    workspace_setup = (
        root
        / "simulation-workspaces"
        / "turtlebot3-burger-v1"
        / "releases"
        / ("b" * 64)
        / "install/setup.bash"
    )
    workspace_setup.parent.mkdir(parents=True, exist_ok=True)
    workspace_setup.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return {
        "status": "ready",
        "bundle_root": str(bundle_root),
        "bundle_id": "turtlebot3-burger-v1",
        "bundle_version": "1.0.0",
        "bundle_sha256": "a" * 64,
        "bundle_receipt": str(bundle_root / "bundle-receipt.json"),
        "workspace_fingerprint": "b" * 64,
        "workspace_receipt": str(workspace_setup.parent.parent / "workspace-receipt.json"),
        "workspace_setup": str(workspace_setup),
    }


def _configured_setup(tmp_path: Path, *, deploy: bool = False):
    planner = _PlanBuilder()
    applied: list[_Plan] = []

    def apply(plan: _Plan) -> dict[str, object]:
        applied.append(plan)
        return {
            "status": "installed",
            "fingerprint": plan.fingerprint,
            "reused": False,
        }

    result = setup_fireclaw(
        mode="simulation",
        runtime_root=tmp_path / "fireclaw-home",
        source_root=SOURCE_ROOT,
        deploy=deploy,
        plan_builder=planner,
        deployment_applier=apply,
        simulation_preparer=_prepare_fake_simulation_runtime,
    )
    return result, planner, applied


def test_setup_generates_credential_free_simulation_profile_and_activates_it(
    tmp_path: Path,
) -> None:
    result, planner, applied = _configured_setup(tmp_path, deploy=True)

    profile_path = Path(result["profile_path"])
    profile_text = profile_path.read_text(encoding="utf-8")
    robot = load_robot_capability_profile(profile_path)
    deployment = load_runtime_deployment_profile(profile_path)
    active = load_active_profile(runtime_root=tmp_path / "fireclaw-home")

    assert result["status"] == "ready_to_start"
    assert result["robot_action_started"] is False
    assert result["safe_state"] == "simulation_only"
    assert result["profile_created"] is True
    assert robot.robot_id == "gazebo_turtlebot3"
    assert deployment.mode == "simulation"
    assert active.profile_path == profile_path
    assert active.template_id == SIMULATION_TEMPLATE_ID
    assert planner.paths == [profile_path]
    assert len(applied) == 1
    assert "{{" not in profile_text
    assert "api_key" not in profile_text
    assert 'dry_run = true' in profile_text
    assert stat.S_IMODE(profile_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(active.state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(active.state_path.parent.stat().st_mode) == 0o700
    runtime_root = (tmp_path / "fireclaw-home").resolve()
    assert str(runtime_root) in profile_text
    assert str(SOURCE_ROOT) not in profile_text
    assert "/devel/setup.bash" not in profile_text
    assert 'bundle_sha256 = "' + ("a" * 64) + '"' in profile_text


def test_setup_is_idempotent_and_preserves_existing_profile(tmp_path: Path) -> None:
    first, _, _ = _configured_setup(tmp_path)
    profile_path = Path(first["profile_path"])
    original = profile_path.read_bytes()

    second, planner, _ = _configured_setup(tmp_path)

    assert second["profile_path"] == first["profile_path"]
    assert second["profile_created"] is False
    assert profile_path.read_bytes() == original
    assert planner.paths == [profile_path]


def test_setup_preserves_invalid_existing_profile_and_does_not_activate(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "fireclaw-home"
    profile = (
        runtime_root
        / "profiles"
        / "gazebo-turtlebot3-burger-1.0.0-aaaaaaaaaaaa.toml"
    )
    profile.parent.mkdir(parents=True)
    profile.write_text("not valid TOML = [", encoding="utf-8")

    with pytest.raises(ValueError):
        setup_fireclaw(
            mode="simulation",
            runtime_root=runtime_root,
            source_root=SOURCE_ROOT,
            deploy=False,
            plan_builder=_PlanBuilder(),
            simulation_preparer=_prepare_fake_simulation_runtime,
        )

    assert profile.read_text(encoding="utf-8") == "not valid TOML = ["
    assert not (runtime_root / "state" / "active-profile.json").exists()


def test_setup_plan_failure_keeps_profile_for_safe_resume(tmp_path: Path) -> None:
    runtime_root = tmp_path / "fireclaw-home"

    def fail_plan(_profile_path: str | Path) -> _Plan:
        raise ValueError("ROS dependency is missing")

    with pytest.raises(ValueError, match="ROS dependency is missing"):
        setup_fireclaw(
            mode="simulation",
            runtime_root=runtime_root,
            source_root=SOURCE_ROOT,
            deploy=False,
            plan_builder=fail_plan,
            simulation_preparer=_prepare_fake_simulation_runtime,
        )

    assert (
        runtime_root
        / "profiles"
        / "gazebo-turtlebot3-burger-1.0.0-aaaaaaaaaaaa.toml"
    ).is_file()
    assert not (runtime_root / "state" / "active-profile.json").exists()


def test_real_setup_requires_existing_profile_and_never_auto_deploys(
    tmp_path: Path,
) -> None:
    with pytest.raises(FireClawSetupError) as missing:
        setup_fireclaw(
            mode="real",
            runtime_root=tmp_path / "real-home",
            deploy=False,
        )
    assert missing.value.code == "real_profile_required"

    simulation, _, _ = _configured_setup(tmp_path / "source")
    profile = Path(simulation["profile_path"])
    text = profile.read_text(encoding="utf-8").replace(
        'mode = "simulation"',
        'mode = "real"',
    )
    real_profile = tmp_path / "reviewed-real.toml"
    real_profile.write_text(text, encoding="utf-8")

    with pytest.raises(FireClawSetupError) as forbidden:
        setup_fireclaw(
            mode="real",
            profile_path=real_profile,
            runtime_root=tmp_path / "real-home",
            deploy=True,
            plan_builder=_PlanBuilder(),
        )
    assert forbidden.value.code == "real_setup_auto_deploy_forbidden"


def test_generated_simulation_profile_cannot_be_activated_as_real(
    tmp_path: Path,
) -> None:
    simulation, _, _ = _configured_setup(tmp_path / "source")
    profile = Path(simulation["profile_path"])
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            'mode = "simulation"',
            'mode = "real"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(FireClawSetupError) as failure:
        setup_fireclaw(
            mode="real",
            profile_path=profile,
            runtime_root=tmp_path / "real-home",
            deploy=False,
            plan_builder=_PlanBuilder(),
        )

    assert failure.value.code == "simulation_profile_for_real_forbidden"


def test_active_profile_state_symlink_is_rejected(tmp_path: Path) -> None:
    result, _, _ = _configured_setup(tmp_path)
    runtime_root = tmp_path / "fireclaw-home"
    state_dir = runtime_root / "state"
    real_state = runtime_root / "state-real"
    state_dir.rename(real_state)
    state_dir.symlink_to(real_state, target_is_directory=True)

    with pytest.raises(FireClawSetupError) as failure:
        load_active_profile(runtime_root=runtime_root)

    assert failure.value.code == "setup_path_unsafe"
    assert Path(result["profile_path"]).is_file()


def test_explicit_profile_does_not_require_active_state(tmp_path: Path) -> None:
    result, _, _ = _configured_setup(tmp_path)
    profile = Path(result["profile_path"])

    assert (
        resolve_active_profile_path(
            profile,
            runtime_root=tmp_path / "missing-home",
        )
        == profile
    )


def test_handle_setup_defaults_noninteractive_json_to_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_setup(**kwargs):
        observed.update(kwargs)
        return {
            "status": "configured",
            "mode": kwargs["mode"],
            "profile_path": str(tmp_path / "profile.toml"),
            "robot_action_started": False,
            "next_command": "fireclaw deploy apply",
        }

    monkeypatch.setattr(
        "fireclaw_core.infra.user_setup.setup_fireclaw",
        fake_setup,
    )
    args = argparse.Namespace(
        mode=None,
        profile=None,
        runtime_root=tmp_path,
        source_root=SOURCE_ROOT,
        no_deploy=True,
        no_start=True,
        no_browser=True,
        startup_timeout=1.0,
        simulation_bundle=None,
        json=True,
    )

    output = StringIO()
    code = handle_setup(args, stdin_is_tty=False, out=output)
    payload = json.loads(output.getvalue())

    assert code == 0
    assert observed["mode"] == "simulation"
    assert observed["deploy"] is False
    assert payload["robot_action_started"] is False


def test_interactive_real_setup_prompts_for_reviewed_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    profile = tmp_path / "reviewed-real.toml"

    def fake_setup(**kwargs):
        observed.update(kwargs)
        return {
            "status": "configured",
            "mode": kwargs["mode"],
            "profile_path": str(kwargs["profile_path"]),
            "robot_action_started": False,
            "next_command": "fireclaw status",
        }

    monkeypatch.setattr(
        "fireclaw_core.infra.user_setup.setup_fireclaw",
        fake_setup,
    )
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return str(profile)

    output = StringIO()
    args = argparse.Namespace(
        mode="real",
        profile=None,
        runtime_root=tmp_path,
        source_root=None,
        no_deploy=False,
        no_start=False,
        no_browser=False,
        startup_timeout=1.0,
        simulation_bundle=None,
        json=False,
    )

    assert handle_setup(
        args,
        out=output,
        input_fn=answer,
        stdin_is_tty=True,
    ) == 0
    assert observed["mode"] == "real"
    assert observed["profile_path"] == profile
    assert observed["deploy"] is False
    assert "人工审查" in prompts[0]


def test_real_setup_is_passive_and_writes_unapplied_discovery_draft(
    tmp_path: Path,
) -> None:
    simulation, _, _ = _configured_setup(tmp_path / "source")
    generated = Path(simulation["profile_path"])
    text = generated.read_text(encoding="utf-8")
    text = text.replace(
        'template_id = "gazebo-turtlebot3-burger-v1"',
        'template_id = "reviewed-real-profile"',
    ).replace('mode = "simulation"', 'mode = "real"')
    real_profile = tmp_path / "reviewed-real.toml"
    real_profile.write_text(text, encoding="utf-8")

    calls: list[tuple[str, object]] = []

    class Discoverer:
        def probe_ros_master(self):
            calls.append(("discover", None))
            return SimpleNamespace(
                to_dict=lambda: {
                    "discovered": True,
                    "master_uri": "http://operator:secret@127.0.0.1:11311",
                    "active_topics": [["/laser", "sensor_msgs/LaserScan"]],
                    "active_services": [],
                    "matched_topics": {"laser_scan_topic": "/laser"},
                    "matched_actions": {},
                    "offline_fallback_recommendations": [],
                    "reason": None,
                }
            )

    def preflight(path, *, live):
        calls.append(("preflight", live))
        assert Path(path) == real_profile.resolve()
        return {"status": "prepared", "checks": [], "live_checked": live}, None

    def forbidden(*_args, **_kwargs):
        raise AssertionError("real setup must not plan, deploy, start, or move")

    result = setup_fireclaw(
        mode="real",
        profile_path=real_profile,
        runtime_root=tmp_path / "real-home",
        deploy=False,
        plan_builder=forbidden,
        deployment_applier=forbidden,
        real_discoverer=Discoverer(),
        real_preflight_runner=preflight,
    )

    assert calls == [("discover", None), ("preflight", False)]
    assert result["status"] == "review_required"
    assert result["robot_action_started"] is False
    assert result["real_robot_motion_authorized"] is False
    assert result["diff"][0]["field"] == "navigation.scan_topic"
    assert result["diff"][0]["proposed"] == "/laser"
    draft = json.loads(Path(result["draft_path"]).read_text(encoding="utf-8"))
    assert draft["applied"] is False
    assert "secret@" not in draft["discovery"]["master_uri"]


def test_handle_setup_simulation_completes_start_and_open_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile.toml"
    starts: list[dict[str, object]] = []
    opens: list[dict[str, object]] = []

    def fake_setup(**kwargs):
        return {
            "status": "ready_to_start",
            "mode": kwargs["mode"],
            "profile_path": str(profile),
            "robot_action_started": False,
            "next_command": "fireclaw start",
        }

    class Manager:
        def start_daemon(self, **kwargs):
            starts.append(kwargs)
            return {
                "status": "running",
                "health_verified": True,
                "daemon": {"pid": 12},
            }

        def open_console(self, **kwargs):
            opens.append(kwargs)
            return {
                "status": "ready",
                "url": "http://127.0.0.1:8766",
                "browser_opened": False,
            }

    monkeypatch.setattr("fireclaw_core.infra.user_setup.setup_fireclaw", fake_setup)
    args = argparse.Namespace(
        mode="simulation",
        profile=None,
        runtime_root=tmp_path,
        source_root=None,
        simulation_bundle=tmp_path / "bundle.tar.gz",
        no_deploy=False,
        no_start=False,
        no_browser=True,
        startup_timeout=3.0,
        json=True,
    )
    output = StringIO()

    assert handle_setup(args, out=output, stdin_is_tty=False, manager=Manager()) == 0
    payload = json.loads(output.getvalue())
    assert payload["status"] == "ready"
    assert payload["simulation_runtime_started"] is True
    assert payload["real_robot_action_started"] is False
    assert starts == [
        {"profile_path": str(profile), "foreground": False, "timeout": 3.0}
    ]
    assert opens == [{"profile_path": str(profile), "browser": False}]


def test_handle_setup_real_never_calls_lifecycle_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "reviewed-real.toml"
    observed: dict[str, object] = {}

    def fake_setup(**kwargs):
        observed.update(kwargs)
        return {
            "status": "review_required",
            "mode": "real",
            "profile_path": str(profile),
            "draft_path": str(tmp_path / "draft.json"),
            "robot_action_started": False,
            "real_robot_action_started": False,
            "real_robot_motion_authorized": False,
            "next_command": "人工审查",
        }

    class ForbiddenManager:
        def __getattr__(self, name):
            raise AssertionError(f"real setup must not call lifecycle manager: {name}")

    monkeypatch.setattr("fireclaw_core.infra.user_setup.setup_fireclaw", fake_setup)
    args = argparse.Namespace(
        mode="real",
        profile=profile,
        runtime_root=tmp_path / "runtime",
        source_root=None,
        simulation_bundle=None,
        no_deploy=False,
        no_start=False,
        no_browser=False,
        startup_timeout=3.0,
        json=True,
    )
    output = StringIO()

    assert handle_setup(
        args,
        out=output,
        stdin_is_tty=False,
        manager=ForbiddenManager(),
    ) == 0
    payload = json.loads(output.getvalue())
    assert observed["deploy"] is False
    assert payload["real_robot_motion_authorized"] is False
    assert payload["real_robot_action_started"] is False


def test_status_uses_active_profile_when_flag_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _ = _configured_setup(tmp_path)
    runtime_root = tmp_path / "fireclaw-home"
    monkeypatch.setenv("FIRECLAW_HOME", str(runtime_root))
    observed: list[Path] = []

    def collect(profile_path, **_kwargs):
        observed.append(Path(profile_path))
        return {
            "phase": "ready",
            "safe_state": "motion_admitted_idle",
            "reason_code": "ready",
            "summary": "ready",
            "operator_action": "none",
            "retryable": False,
            "components": {},
            "evidence": {},
        }

    monkeypatch.setattr(operator_cli, "collect_operator_status", collect)
    args = argparse.Namespace(
        profile=None,
        output_root=None,
        no_runtime_check=False,
        gateway=None,
        api_token=None,
        timeout=1.0,
        tls_ca_file=None,
        tls_client_cert_file=None,
        tls_client_key_file=None,
        server=None,
        mission_api_token=None,
        mission_tls_ca_file=None,
        mission_tls_client_cert_file=None,
        mission_tls_client_key_file=None,
        json=True,
    )

    assert operator_cli.handle_status(args) == 0
    assert observed == [Path(result["profile_path"])]


def test_deploy_cli_uses_active_profile_when_flag_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result, _, _ = _configured_setup(tmp_path)
    runtime_root = tmp_path / "fireclaw-home"
    monkeypatch.setenv("FIRECLAW_HOME", str(runtime_root))
    observed: list[Path] = []

    class Plan:
        def to_dict(self) -> dict[str, object]:
            return {"status": "ready", "profile_path": str(observed[0])}

    def build(profile_path, *, output_root=None):
        assert output_root is None
        observed.append(Path(profile_path))
        return Plan()

    monkeypatch.setattr(
        "fireclaw_core.deployment.build_deployment_plan",
        build,
    )
    monkeypatch.setattr(
        "sys.argv",
        ["fireclaw", "deploy", "plan"],
    )
    from fireclaw_core.mission.mission_cli import main

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert observed == [Path(result["profile_path"])]
    assert payload["status"] == "ready"


def test_status_without_active_profile_explains_setup_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setenv("FIRECLAW_HOME", str(tmp_path / "empty-home"))
    args = argparse.Namespace(
        profile=None,
        output_root=None,
        no_runtime_check=False,
        gateway=None,
        api_token=None,
        timeout=1.0,
        tls_ca_file=None,
        tls_client_cert_file=None,
        tls_client_key_file=None,
        server=None,
        mission_api_token=None,
        mission_tls_ca_file=None,
        mission_tls_client_cert_file=None,
        mission_tls_client_key_file=None,
        json=True,
    )

    assert operator_cli.handle_status(args, out=output) == 2
    payload = json.loads(output.getvalue())
    assert payload["reason_code"] == "active_profile_missing"
    assert payload["operator_action"] == "先运行 fireclaw setup。"
    assert payload["safe_state"] == "unknown"


def test_recover_without_active_profile_does_not_claim_motion_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setenv("FIRECLAW_HOME", str(tmp_path / "empty-home"))
    args = argparse.Namespace(
        profile=None,
        gateway=None,
        api_token=None,
        timeout=1.0,
        reason=None,
        request_id=None,
        confirmation_phrase=None,
        request_only=False,
        tls_ca_file=None,
        tls_client_cert_file=None,
        tls_client_key_file=None,
        json=True,
    )

    assert operator_cli.handle_recover(args, out=output, stdin_is_tty=False) == 1
    payload = json.loads(output.getvalue())
    assert payload["reason_code"] == "active_profile_missing"
    assert payload["safe_state"] == "unknown"
    assert payload["operator_action"] == "先运行 fireclaw setup。"
