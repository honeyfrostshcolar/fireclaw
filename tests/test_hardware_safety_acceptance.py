from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fireclaw_core.evaluation.artifacts import canonical_json_sha256, make_run_id, sha256_file
from fireclaw_core.infra import hardware_safety_acceptance as acceptance
from fireclaw_plugin_sdk import HARDWARE_STOP_EVIDENCE_CLASS


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _write_profile(
    tmp_path: Path,
    *,
    mode: str = "real",
    reviewed: bool = True,
    brake_required: bool = False,
) -> Path:
    setup = tmp_path / "setup.bash"
    setup.write_text("export ROS_DISTRO=noetic\n", encoding="utf-8")
    launch = tmp_path / "robot.launch"
    launch.write_text("<launch/>\n", encoding="utf-8")
    ros_config = tmp_path / "ros1.yaml"
    ros_config.write_text(
        "robot_id: firebot-01\ntransport:\n  enabled: false\n",
        encoding="utf-8",
    )
    brake = "required = false"
    if brake_required:
        brake = """required = true
topic = "/hardware/brake"
message_type = "std_msgs/Bool"
engaged_field = "data""".strip()
    profile = tmp_path / "robot.toml"
    profile.write_text(
        f"""
[robot]
id = "firebot-01"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "{ros_config}"
data_dir = "{tmp_path / 'data'}"
capabilities = ["safety"]
enabled_skills = ["hold_position"]
llm_exposed_skills = ["hold_position"]
primitive_skills = ["hold_position"]

[capability_skill_chains]
safety = ["hold_position"]

[deployment]
id = "firebot-01"
mode = "{mode}"
output_root = "{tmp_path / 'deployments'}"

[deployment.ros1]
distro = "noetic"
setup_files = ["{setup}"]

[deployment.launch]
robot_launch = "{launch}"

[hardware_safety_acceptance]
profile_reviewed = {str(reviewed).lower()}
reviewed_by = "operator-01"
reviewed_at = "2026-08-13T10:00:00+08:00"
vendor_safety_reference = "Vendor safety manual rev. 4"

[plugins]
paths = ["{EXTENSIONS}"]
selected = ["fireclaw.safety.ros1-hardware"]

[plugins.config."fireclaw.safety.ros1-hardware"]
enabled = true
observation_timeout_seconds = 0.8
max_signal_age_seconds = 0.5
hold_seconds = 0.75
minimum_samples = 3
evidence_ttl_seconds = 15.0

[plugins.config."fireclaw.safety.ros1-hardware".stop]
service = "/hardware/safety/stop"
type = "std_srvs/SetBool"

[plugins.config."fireclaw.safety.ros1-hardware".watchdog]
topic = "/hardware/watchdog"
message_type = "vendor_msgs/HardwareWatchdog"
healthy_field = "healthy"
stop_asserted_field = "stop_asserted"

[plugins.config."fireclaw.safety.ros1-hardware".emergency_stop]
topic = "/hardware/emergency_stop"
message_type = "std_msgs/Bool"
field = "data"

[plugins.config."fireclaw.safety.ros1-hardware".driver]
topic = "/hardware/driver_enabled"
message_type = "std_msgs/Bool"
field = "data"

[plugins.config."fireclaw.safety.ros1-hardware".brake]
{brake}

[plugins.config."fireclaw.safety.ros1-hardware".actuators]
topic = "/hardware/joint_states"
message_type = "sensor_msgs/JointState"
expected_names = ["left_wheel", "right_wheel"]
ignored_names = ["caster_joint"]
velocity_threshold = 0.01

[plugins.config."fireclaw.safety.ros1-hardware".independent_motion]
topic = "/hardware/independent_odom"
message_type = "nav_msgs/Odometry"
linear_velocity_threshold = 0.01
angular_velocity_threshold = 0.02
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return profile


class _FakeProvider:
    def __init__(self, checks=None) -> None:
        self.checks = checks or [
            {"id": "ros.master", "status": "pass", "message": "ok"}
        ]

    def preflight(self):
        return {"status": "ready", "checks": self.checks}


class _AcceptanceProvider:
    def __init__(self) -> None:
        self.inspect_calls = 0
        self.stop_calls = 0

    def inspect_hardware_state(self, *, robot_id):
        self.inspect_calls += 1
        return {
            "evidence_class": "hardware_safety_observation_v1",
            "robot_id": robot_id,
            "status": "observed",
            "deployment_mode": "real",
            "dry_run": False,
            "details": {
                "blockers": ["hardware_emergency_stop_not_active"],
                "minimum_samples": 3,
                "emergency_stop": {
                    "fresh": True,
                    "active": False,
                    "sample_count": 3,
                },
            },
        }

    def collect_stop_evidence(self, **kwargs):
        self.stop_calls += 1
        raise AssertionError("negative acceptance must not invoke hardware stop")


def test_offline_preflight_proves_profile_is_prepared(tmp_path):
    profile = _write_profile(tmp_path)

    result, context = acceptance.run_hardware_safety_preflight(
        profile,
        live=False,
    )

    assert result["status"] == "prepared"
    assert result["live_checked"] is False
    assert result["deployment_fingerprint"]
    assert context is not None
    assert all(item["status"] == "pass" for item in result["checks"])


def test_live_preflight_uses_non_actuating_provider(monkeypatch, tmp_path):
    profile = _write_profile(tmp_path)
    provider = _FakeProvider()
    monkeypatch.setattr(acceptance, "_load_hardware_provider", lambda deployment: provider)

    result, context = acceptance.run_hardware_safety_preflight(
        profile,
        live=True,
    )

    assert result["status"] == "ready"
    assert context is not None and context.provider is provider
    assert result["checks"][-1]["id"] == "live.ros.master"


def test_inventory_negative_scenario_allows_only_expected_preflight_failure(
    monkeypatch,
    tmp_path,
):
    profile = _write_profile(tmp_path)
    provider = _FakeProvider(
        checks=[
            {
                "id": "topic.actuators.sample",
                "status": "fail",
                "message": "inventory mismatch",
            }
        ]
    )
    monkeypatch.setattr(acceptance, "_load_hardware_provider", lambda deployment: provider)

    result, _ = acceptance.run_hardware_safety_preflight(
        profile,
        live=True,
        scenario="inventory_incomplete",
    )

    assert result["status"] == "ready"
    assert result["checks"][-1]["status"] == "expected"


def test_confirmed_negative_acceptance_observes_only_and_writes_artifact(
    monkeypatch,
    tmp_path,
    capsys,
):
    provider = _AcceptanceProvider()
    profile_sha = "a" * 64
    context = SimpleNamespace(
        robot=SimpleNamespace(robot_id="firebot-01"),
        deployment=SimpleNamespace(
            plugin_configs={
                acceptance.PLUGIN_ID: {"brake": {"required": False}}
            }
        ),
        profile_sha256=profile_sha,
        deployment_fingerprint="deployment-1",
        provider=provider,
    )
    preflight = {"status": "ready", "live_checked": True, "checks": []}
    monkeypatch.setattr(
        acceptance,
        "run_hardware_safety_preflight",
        lambda *args, **kwargs: (preflight, context),
    )
    phrase = acceptance.acceptance_confirmation_phrase(
        robot_id="firebot-01",
        scenario="emergency_stop_inactive",
        profile_sha256=profile_sha,
    )
    args = argparse.Namespace(
        profile=tmp_path / "robot.toml",
        scenario="emergency_stop_inactive",
        target_signal=None,
        operator_id="operator-01",
        firmware_version="fw-1",
        artifact_dir=tmp_path / "artifacts",
        confirmation_phrase=phrase,
        json=True,
    )

    exit_code = acceptance._handle_accept(args)
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "passed"
    assert provider.inspect_calls == 1
    assert provider.stop_calls == 0
    artifact_path = Path(output["artifact"])
    assert artifact_path.is_file()
    assert artifact_path.stat().st_mode & 0o777 == 0o600
    assert acceptance.verify_acceptance_artifact(artifact_path)["status"] == "valid"


def test_stop_proof_requires_hardware_stop_evidence_without_blockers():
    evidence = {
        "evidence_class": HARDWARE_STOP_EVIDENCE_CLASS,
        "status": "stopped",
        "details": {"blockers": []},
    }

    passed, reasons = acceptance.evaluate_acceptance_scenario(
        "stop_proof",
        evidence,
    )

    assert passed is True
    assert reasons == []


def test_watchdog_loss_requires_independent_stationary_proof():
    evidence = {
        "evidence_class": "hardware_safety_observation_v1",
        "status": "observed",
        "details": {
            "blockers": ["watchdog_unhealthy"],
            "minimum_samples": 3,
            "hold_seconds": 0.75,
            "watchdog": {
                "fresh": True,
                "healthy": False,
                "stop_asserted": True,
                "sample_count": 3,
            },
            "driver": {"fresh": True, "enabled": False, "sample_count": 3},
            "actuators": {
                "fresh": True,
                "inventory_complete": True,
                "stationary_samples": 3,
                "sample_span_seconds": 0.8,
            },
            "independent_motion": {
                "fresh": True,
                "stationary_samples": 3,
                "sample_span_seconds": 0.8,
            },
        },
    }

    passed, reasons = acceptance.evaluate_acceptance_scenario(
        "watchdog_loss",
        evidence,
    )

    assert passed is True
    assert reasons == []

    evidence["details"]["hold_seconds"] = 0.1
    evidence["details"]["actuators"]["sample_span_seconds"] = 0.1
    evidence["details"]["independent_motion"]["sample_span_seconds"] = 0.1
    passed, reasons = acceptance.evaluate_acceptance_scenario(
        "watchdog_loss",
        evidence,
    )
    assert passed is False
    assert "robot_not_proven_stationary_after_watchdog_loss" in reasons


def _artifact(
    profile: Path,
    scenario: str,
    *,
    target_signal: str | None = None,
    firmware: str = "fw-1",
) -> dict:
    evidence = _passing_evidence(scenario, target_signal=target_signal)
    value = {
        "schema_version": acceptance.ARTIFACT_SCHEMA_VERSION,
        "kind": acceptance.ARTIFACT_KIND,
        "run_id": make_run_id(f"hardware-safety-{scenario}"),
        "robot_id": "firebot-01",
        "scenario": scenario,
        "target_signal": target_signal,
        "actuating": scenario == "stop_proof",
        "profile_sha256": sha256_file(profile),
        "deployment_fingerprint": "deployment-1",
        "firmware_version": firmware,
        "operator_id": "operator-01",
        "attestations": [
            "test_area_clear",
            "vendor_safe_test_mode",
            "physical_emergency_stop_available",
            "independent_safety_observer_present",
        ],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "preflight": {"status": "ready", "live_checked": True},
        "expected_blockers": list(
            acceptance._expected_blockers(scenario, target_signal)
        ),
        "evidence": evidence,
        "passed": True,
        "failure_reasons": [],
    }
    value["artifact_sha256"] = canonical_json_sha256(value)
    return value


def _passing_evidence(
    scenario: str,
    *,
    target_signal: str | None,
) -> dict:
    base = {
        "evidence_class": "hardware_safety_observation_v1",
        "robot_id": "firebot-01",
        "status": "observed",
        "deployment_mode": "real",
        "dry_run": False,
    }
    if scenario == "stop_proof":
        return base | {
            "evidence_class": HARDWARE_STOP_EVIDENCE_CLASS,
            "status": "stopped",
            "details": {"blockers": []},
        }
    blockers = list(acceptance._expected_blockers(scenario, target_signal))
    details: dict = {
        "blockers": blockers,
        "minimum_samples": 3,
        "hold_seconds": 0.8,
    }
    if scenario == "watchdog_loss":
        details.update(
            watchdog={
                "fresh": True,
                "healthy": False,
                "stop_asserted": True,
                "sample_count": 3,
            },
            driver={"fresh": True, "enabled": False, "sample_count": 3},
            actuators={
                "fresh": True,
                "inventory_complete": True,
                "stationary_samples": 3,
                "sample_span_seconds": 0.8,
            },
            independent_motion={
                "fresh": True,
                "stationary_samples": 3,
                "sample_span_seconds": 0.8,
            },
        )
    elif scenario == "emergency_stop_inactive":
        details["emergency_stop"] = {
            "fresh": True,
            "active": False,
            "sample_count": 3,
        }
    elif scenario == "driver_enabled":
        details["driver"] = {
            "fresh": True,
            "enabled": True,
            "sample_count": 3,
        }
    elif scenario == "brake_disengaged":
        details["brake"] = {
            "fresh": True,
            "engaged": False,
            "sample_count": 3,
        }
    elif scenario == "actuator_motion":
        details[str(target_signal)] = {
            "fresh": True,
            "sample_count": 3,
            "sample_span_seconds": 0.8,
        }
    elif scenario == "inventory_incomplete":
        details["actuators"] = {
            "fresh": True,
            "inventory_complete": False,
            "sample_count": 3,
            "sample_span_seconds": 0.8,
        }
    return base | {"details": details}


def test_artifact_digest_and_complete_report_are_verified(tmp_path):
    profile = _write_profile(tmp_path)
    artifact_root = tmp_path / "artifacts"
    required_cases = acceptance._required_cases(brake_required=False)
    first_path = None
    for _, scenario, target_signal in required_cases:
        path = acceptance._write_acceptance_artifact(
            _artifact(profile, scenario, target_signal=target_signal),
            root=artifact_root,
        )
        first_path = first_path or path

    verified = acceptance.verify_acceptance_artifact(first_path)
    report = acceptance.build_acceptance_report(
        profile,
        firmware_version="fw-1",
        artifact_dir=artifact_root,
    )

    assert verified["status"] == "valid"
    assert report["status"] == "passed"
    assert report["missing_scenarios"] == []
    assert set(report["passed_scenarios"]) == {
        case_id for case_id, _, _ in required_cases
    }


def test_recomputed_digest_cannot_turn_failed_evidence_into_pass(tmp_path):
    profile = _write_profile(tmp_path)
    artifact = _artifact(profile, "emergency_stop_inactive")
    artifact["evidence"]["details"]["emergency_stop"]["active"] = True
    artifact["artifact_sha256"] = canonical_json_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    path = acceptance._write_acceptance_artifact(
        artifact,
        root=tmp_path / "artifacts",
    )

    verified = acceptance.verify_acceptance_artifact(path)

    assert verified["status"] == "invalid"
    assert verified["message"] == "artifact passed flag does not match evidence"


def test_unreviewed_or_simulation_profile_is_never_prepared(tmp_path):
    profile = _write_profile(tmp_path, mode="simulation", reviewed=False)

    result, _ = acceptance.run_hardware_safety_preflight(profile, live=False)

    assert result["status"] == "blocked"
    failed = {item["id"] for item in result["checks"] if item["status"] == "fail"}
    assert "deployment.real_mode" in failed
    assert "review.approved" in failed


def test_top_level_cli_runs_offline_preflight(tmp_path):
    profile = _write_profile(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "hardware-safety",
            "preflight",
            "--profile",
            str(profile),
            "--offline",
            "--json",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "prepared"
    assert result["live_checked"] is False
