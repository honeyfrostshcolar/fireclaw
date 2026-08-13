from __future__ import annotations

import argparse
import json
import subprocess
import sys
from io import StringIO

from fireclaw_core.infra import operator_cli


def _profile(tmp_path, *, base_url="http://127.0.0.1:8765"):
    target = tmp_path / "robot.toml"
    target.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "{base_url}"
adapter = "ros1"
data_dir = "data/robot-1"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]
primitive_skills = ["navigate_to_point"]

[capability_skill_chains]
navigation = ["navigate_to_point"]
""".strip(),
        encoding="utf-8",
    )
    return target


class _StatusClient:
    def get_health(self, entry):
        return {
            "status": "ok",
            "robot_id": entry.robot_id,
            "dry_run": False,
        }

    def get_state(self, entry):
        return {
            "robot_state": {"online": True},
            "active_tasks": [],
            "emergency_stop": {"active": False},
            "resource_admission": {
                "admission": {"closed": False, "revision": 0},
                "pending_recovery": None,
                "stop_evidence_providers": [
                    {
                        "service_id": "safety.stop-evidence.test",
                        "evidence_class": "hardware_stop_v1",
                        "hardware_owned": True,
                        "qualified_for_real": True,
                    }
                ],
            },
        }


class _DoctorClient:
    def __init__(self, *, warning_count=0):
        self.warning_count = warning_count

    def get_fleet_doctor(self):
        findings = (
            [
                {
                    "severity": "warning",
                    "category": "readiness",
                    "message": "sensor degraded",
                }
            ]
            if self.warning_count
            else []
        )
        return {
            "status": "healthy",
            "error_count": 0,
            "warning_count": self.warning_count,
            "total_count": len(findings),
            "findings": findings,
        }


def test_collect_operator_status_aggregates_profile_deployment_and_gateway(tmp_path):
    profile = _profile(tmp_path)
    calls = []

    def inspect(path, *, output_root, check_runtime):
        calls.append((path, output_root, check_runtime))
        return {
            "status": "ready",
            "fingerprint": "fp-1",
            "runtime_checks": {"ok": True, "checks": []},
        }

    snapshot = operator_cli.collect_operator_status(
        profile,
        output_root=tmp_path / "deployments",
        deployment_inspector=inspect,
        robot_client=_StatusClient(),
        mission_client=_DoctorClient(),
    )

    assert snapshot["phase"] == "ready"
    assert snapshot["robot_id"] == "robot-1"
    assert snapshot["components"]["fleet_doctor"]["phase"] == "ready"
    assert snapshot["components"]["fleet_doctor"]["endpoint"] == {
        "base_url": "http://127.0.0.1:8766",
        "source": "default_loopback",
    }
    assert calls == [
        (str(profile.resolve()), tmp_path / "deployments", True)
    ]


def test_collect_operator_status_resolves_mission_gateway_from_profile(tmp_path):
    profile = _profile(tmp_path)
    with profile.open("a", encoding="utf-8") as handle:
        handle.write(
            """

[deployment]
id = "robot-1"
mode = "simulation"

[deployment.ros1]
distro = "noetic"
setup_files = ["/opt/ros/noetic/setup.bash"]

[plugins]
paths = ["extensions"]
selected = ["fireclaw.navigation.move-base"]

[server]
host = "0.0.0.0"
port = 9876
"""
        )

    snapshot = operator_cli.collect_operator_status(
        profile,
        output_root=tmp_path / "deployments",
        deployment_inspector=lambda *args, **kwargs: {
            "status": "ready",
            "fingerprint": "fp-1",
            "runtime_checks": {"ok": True, "checks": []},
        },
        robot_client=_StatusClient(),
        mission_client=_DoctorClient(),
    )

    assert snapshot["components"]["fleet_doctor"]["endpoint"] == {
        "base_url": "http://127.0.0.1:9876",
        "source": "profile",
    }


def test_collect_operator_status_degrades_when_fleet_doctor_is_unreachable(
    tmp_path,
):
    class UnreachableDoctor:
        def get_fleet_doctor(self):
            raise TimeoutError("doctor timed out")

    snapshot = operator_cli.collect_operator_status(
        _profile(tmp_path),
        deployment_inspector=lambda *args, **kwargs: {
            "status": "ready",
            "fingerprint": "fp-1",
            "runtime_checks": {"ok": True, "checks": []},
        },
        robot_client=_StatusClient(),
        mission_client=UnreachableDoctor(),
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "mission_gateway_unreachable"
    assert snapshot["components"]["fleet_doctor"]["phase"] == "offline"


def test_status_handler_defaults_to_human_output(monkeypatch):
    monkeypatch.setattr(
        operator_cli,
        "collect_operator_status",
        lambda *args, **kwargs: {
            "phase": "blocked",
            "robot_id": "robot-1",
            "summary": "运动资源准入已冻结。",
            "safe_state": "motion_blocked",
            "operator_action": "运行 recover。",
            "components": {
                "deployment": {"status": "ready", "capabilities": []},
                "gateway": {"liveness": "online", "access": "available"},
                "resource_admission": {"status": "frozen"},
                "tasks": {"active_count": 0},
            },
        },
    )
    out = StringIO()
    code = operator_cli.handle_status(
        argparse.Namespace(
            profile="robot.toml",
            output_root=None,
            no_runtime_check=False,
            gateway=None,
            api_token=None,
            timeout=1.0,
            tls_ca_file=None,
            tls_client_cert_file=None,
            tls_client_key_file=None,
            json=False,
        ),
        out=out,
    )

    assert code == 1
    assert "FireClaw 状态：BLOCKED" in out.getvalue()
    assert "运动资源准入已冻结" in out.getvalue()


def test_doctor_handler_json_preserves_machine_readable_envelope(monkeypatch):
    monkeypatch.setattr(
        operator_cli,
        "collect_fleet_doctor",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "kind": "fleet_doctor",
            "phase": "degraded",
            "safe_state": "unknown",
            "reason_code": "fleet_doctor_warnings",
            "retryable": True,
            "operator_action": "review warnings",
            "evidence_id": None,
            "summary": "one warning",
            "components": {"warning_count": 1, "findings": []},
            "evidence": {"report": {"warning_count": 1}},
        },
    )
    out = StringIO()
    code = operator_cli.handle_doctor(
        argparse.Namespace(
            server="http://127.0.0.1:8766",
            api_token=None,
            timeout=1.0,
            tls_ca_file=None,
            tls_client_cert_file=None,
            tls_client_key_file=None,
            json=True,
        ),
        out=out,
    )

    assert code == 1
    assert json.loads(out.getvalue())["reason_code"] == "fleet_doctor_warnings"


def test_top_level_cli_registers_operator_commands():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "status", "--help"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert "--no-runtime-check" in completed.stdout
    assert "--server" in completed.stdout
    assert "--mission-api-token" in completed.stdout
    assert "--json" in completed.stdout
