from __future__ import annotations

from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.infra.operator_cli import run_recovery_flow
from fireclaw_core.infra.operator_readiness import (
    build_operator_doctor_snapshot,
    build_operator_status_snapshot,
    format_operator_status,
)


def _ready_deployment():
    return {
        "status": "ready",
        "fingerprint": "fingerprint-1",
        "runtime_checks": {
            "ok": True,
            "checks": [
                {
                    "plugin_id": "fireclaw.navigation.move-base",
                    "kind": "ros1_action",
                    "target": "/move_base",
                    "ok": True,
                    "detail": None,
                }
            ],
        },
    }


def _open_gateway_state():
    return {
        "robot_state": {"online": True},
        "active_tasks": [],
        "emergency_stop": {"active": False},
        "resource_admission": {
            "admission": {"closed": False, "revision": 2},
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


def test_operator_status_requires_runtime_gateway_and_admission_readiness():
    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=_open_gateway_state(),
    )

    assert snapshot["phase"] == "ready"
    assert snapshot["safe_state"] == "motion_admitted_idle"
    assert snapshot["reason_code"] == "ready"
    assert snapshot["components"]["gateway"] == {
        "liveness": "online",
        "access": "available",
        "health_status": "ok",
        "robot_online": True,
        "dry_run": False,
    }
    assert snapshot["components"]["deployment"]["capabilities"] == [
        {
            "plugin_id": "fireclaw.navigation.move-base",
            "status": "ready",
            "check_count": 1,
            "failed_checks": [],
        }
    ]
    assert "不等于现场静止证明" in format_operator_status(snapshot)


def test_operator_status_preserves_frozen_admission_as_primary_blocker():
    state = _open_gateway_state()
    state["resource_admission"] = {
        "admission": {
            "closed": True,
            "reason": "physical_runtime_stop_unconfirmed",
            "task_id": "task-timeout",
            "revision": 3,
        },
        "pending_recovery": {"request_id": "recovery-1", "status": "pending"},
    }

    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["safe_state"] == "motion_blocked"
    assert snapshot["reason_code"] == "physical_runtime_stop_unconfirmed"
    assert snapshot["evidence_id"] == "recovery-1"
    assert "fireclaw recover" in snapshot["operator_action"]


def test_operator_status_blocks_task_admission_when_runtime_storage_failed():
    state = _open_gateway_state()
    state["runtime_storage"] = {
        "status": "degraded",
        "code": "runtime_database_locked",
        "task_admission_allowed": False,
        "occurred_at": "2026-08-13T10:00:00+00:00",
    }

    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["reason_code"] == "runtime_database_locked"
    assert snapshot["components"]["runtime_storage"] == {
        "status": "degraded",
        "reason_code": "runtime_database_locked",
        "task_admission_allowed": False,
        "occurred_at": "2026-08-13T10:00:00+00:00",
    }
    assert "dedupe_key" in snapshot["operator_action"]


def test_frozen_status_explains_missing_hardware_stop_witness():
    state = _open_gateway_state()
    state["resource_admission"] = {
        "admission": {
            "closed": True,
            "reason": "physical_runtime_stop_unconfirmed",
            "revision": 3,
        },
        "pending_recovery": None,
        "stop_evidence_providers": [],
    }

    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
    )

    admission = snapshot["components"]["resource_admission"]
    assert admission["stop_evidence_provider_count"] == 0
    assert "硬件拥有" in snapshot["operator_action"]


def test_operator_status_never_treats_public_health_as_robot_readiness():
    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_error={
            "code": "gateway_authentication_required",
            "message": "unauthorized",
        },
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["safe_state"] == "unknown"
    assert snapshot["components"]["gateway"]["liveness"] == "online"
    assert snapshot["components"]["gateway"]["access"] == "unavailable"


def test_offline_status_points_to_deploy_before_runtime_when_not_installed():
    snapshot = build_operator_status_snapshot(
        deployment={
            "status": "not_deployed",
            "deployment_root": "/var/lib/fireclaw/robot-1",
        },
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_error={"code": "robot_gateway_unreachable"},
        supervisor_state={"status": "no_runs"},
    )

    assert snapshot["phase"] == "offline"
    assert snapshot["reason_code"] == "deployment_not_deployed"
    assert "deploy apply" in snapshot["operator_action"]


def test_offline_status_uses_one_command_service_start_when_installed():
    snapshot = build_operator_status_snapshot(
        deployment={
            "status": "ready",
            "deployment_root": "/var/lib/fireclaw/robot-1",
        },
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_error={"code": "robot_gateway_unreachable"},
        supervisor_state={"status": "stopped"},
        managed_service_state={
            "status": "inactive",
            "unit_name": "fireclaw-robot-1.service",
            "enabled": True,
            "active": False,
        },
    )

    assert "deploy service start" in snapshot["operator_action"]
    assert snapshot["components"]["managed_service"]["status"] == "inactive"


def test_operator_status_exposes_per_plugin_failed_readiness():
    deployment = _ready_deployment()
    deployment["status"] = "installed_not_ready"
    deployment["runtime_checks"]["ok"] = False
    deployment["runtime_checks"]["checks"][0]["ok"] = False
    deployment["runtime_checks"]["checks"][0]["detail"] = "action unavailable"

    snapshot = build_operator_status_snapshot(
        deployment=deployment,
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=_open_gateway_state(),
    )

    assert snapshot["phase"] == "blocked"
    capability = snapshot["components"]["deployment"]["capabilities"][0]
    assert capability["status"] == "unavailable"
    assert capability["failed_checks"][0]["target"] == "/move_base"


def test_operator_status_blocks_when_robot_adapter_reports_offline():
    state = _open_gateway_state()
    state["robot_state"]["online"] = False

    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["safe_state"] == "unknown"
    assert snapshot["reason_code"] == "robot_reported_offline"


def test_operator_status_fails_closed_when_safety_state_is_incomplete():
    cases = [
        ("emergency_stop", {}, "emergency_stop_state_unknown"),
        (
            "resource_admission",
            {"admission": {}},
            "resource_admission_state_unknown",
        ),
        ("robot_state", {}, "robot_state_unknown"),
    ]

    for key, value, expected_reason in cases:
        state = _open_gateway_state()
        state[key] = value
        snapshot = build_operator_status_snapshot(
            deployment=_ready_deployment(),
            robot_id="robot-1",
            profile_path="/profiles/robot.toml",
            gateway_health={"status": "ok", "dry_run": False},
            gateway_state=state,
        )

        assert snapshot["phase"] == "blocked"
        assert snapshot["safe_state"] == "unknown"
        assert snapshot["reason_code"] == expected_reason


def test_operator_status_never_calls_unknown_gateway_mode_real_ready():
    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok"},
        gateway_state=_open_gateway_state(),
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "gateway_execution_mode_unknown"


def test_real_status_is_degraded_without_hardware_stop_witness():
    state = _open_gateway_state()
    state["resource_admission"]["stop_evidence_providers"] = []

    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "stop_evidence_provider_unavailable"
    assert "watchdog" in snapshot["operator_action"]


def test_real_status_rejects_present_but_unqualified_runtime_witness():
    state = _open_gateway_state()
    state["resource_admission"]["stop_evidence_providers"] = [
        {
            "service_id": "fireclaw.safety.stop-evidence.navigation",
            "evidence_class": "runtime_stationarity_v1",
            "hardware_owned": False,
            "qualified_for_real": False,
        }
    ]

    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
    )

    admission = snapshot["components"]["resource_admission"]
    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == (
        "hardware_stop_evidence_provider_unqualified"
    )
    assert admission["stop_evidence_provider_count"] == 1
    assert admission["qualified_real_stop_evidence_provider_count"] == 0


def test_operator_doctor_warnings_are_degraded_not_healthy_ready():
    snapshot = build_operator_doctor_snapshot(
        report={
            "status": "healthy",
            "error_count": 0,
            "warning_count": 1,
            "total_count": 2,
            "findings": [
                {
                    "severity": "warning",
                    "category": "onboarding",
                    "message": "No emergency stop capability.",
                }
            ],
        }
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "fleet_doctor_warnings"


def test_operator_doctor_never_calls_malformed_report_ready():
    snapshot = build_operator_doctor_snapshot(
        report={"status": "healthy", "error_count": 0, "warning_count": 0}
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "fleet_doctor_report_invalid"


def test_unified_status_requires_fleet_doctor_to_be_ready():
    doctor = build_operator_doctor_snapshot(
        report={
            "status": "healthy",
            "error_count": 0,
            "warning_count": 1,
            "total_count": 1,
            "findings": [
                {
                    "severity": "warning",
                    "robot_id": "robot-1",
                    "message": "Localization covariance is elevated.",
                }
            ],
        }
    )
    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=_open_gateway_state(),
        fleet_doctor=doctor,
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "fleet_doctor_warnings"
    assert snapshot["components"]["fleet_doctor"]["warning_count"] == 1
    rendered = format_operator_status(snapshot)
    assert "Fleet Doctor：degraded（errors=0, warnings=1）" in rendered
    assert "Localization covariance is elevated." in rendered


def test_unified_status_does_not_mask_robot_local_safety_freeze():
    state = _open_gateway_state()
    state["resource_admission"]["admission"] = {
        "closed": True,
        "reason": "physical_runtime_stop_unconfirmed",
        "task_id": "task-timeout",
        "revision": 3,
    }
    doctor = build_operator_doctor_snapshot(
        report={
            "status": "unhealthy",
            "error_count": 1,
            "warning_count": 0,
            "total_count": 1,
            "findings": [{"severity": "error", "message": "fleet error"}],
        }
    )
    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=state,
        fleet_doctor=doctor,
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["reason_code"] == "physical_runtime_stop_unconfirmed"
    assert snapshot["components"]["fleet_doctor"]["phase"] == "blocked"
    assert "fireclaw recover" in snapshot["operator_action"]


def test_unified_status_rejects_malformed_doctor_snapshot():
    snapshot = build_operator_status_snapshot(
        deployment=_ready_deployment(),
        robot_id="robot-1",
        profile_path="/profiles/robot.toml",
        gateway_health={"status": "ok", "dry_run": False},
        gateway_state=_open_gateway_state(),
        fleet_doctor={"kind": "fleet_doctor", "phase": "ready"},
    )

    assert snapshot["phase"] == "degraded"
    assert snapshot["reason_code"] == "fleet_doctor_snapshot_invalid"


class _RecoveryClient:
    def __init__(self):
        self.confirm_calls = []
        self.request_calls = []

    def get_resource_admission(self, entry):
        if self.confirm_calls:
            return {"admission": {"closed": False, "revision": 2}}
        return {"admission": {"closed": True, "revision": 1}}

    def request_resource_admission_recovery(self, entry, *, reason):
        self.request_calls.append((entry.robot_id, reason))
        return {
            "status": "pending_confirmation",
            "robot_id": entry.robot_id,
            "request_id": "recovery-1",
            "expires_at": "2026-08-12T10:00:00+00:00",
            "confirmation_phrase": "RECOVER robot-1 recovery-1",
            "stop_evidence": {"status": "stopped", "blockers": []},
        }

    def confirm_resource_admission_recovery(
        self,
        entry,
        *,
        request_id,
        confirmation_phrase,
    ):
        self.confirm_calls.append((request_id, confirmation_phrase))
        return {
            "status": "recovered",
            "robot_id": entry.robot_id,
            "request_id": request_id,
            "admission": {"closed": False, "revision": 2},
        }


def test_recovery_flow_request_only_never_infers_confirmation():
    client = _RecoveryClient()
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")

    snapshot = run_recovery_flow(
        client=client,
        entry=entry,
        reason="operator inspected the scene",
        request_only=True,
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["reason_code"] == "operator_confirmation_required"
    assert client.confirm_calls == []


def test_recovery_flow_fails_closed_on_missing_admission_state():
    class InvalidStateClient(_RecoveryClient):
        def get_resource_admission(self, entry):
            return {"robot_id": entry.robot_id, "admission": {}}

    client = InvalidStateClient()
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")

    snapshot = run_recovery_flow(
        client=client,
        entry=entry,
        reason="operator inspected the scene",
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["reason_code"] == "resource_admission_state_invalid"
    assert client.request_calls == []
    assert client.confirm_calls == []


def test_recovery_flow_reports_gateway_authentication_failure():
    class UnauthorizedClient(_RecoveryClient):
        def get_resource_admission(self, entry):
            return {
                "status": "error",
                "http_status": 403,
                "error": "forbidden",
            }

    client = UnauthorizedClient()
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")

    snapshot = run_recovery_flow(
        client=client,
        entry=entry,
        reason="operator inspected the scene",
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["reason_code"] == "gateway_authentication_required"
    assert client.request_calls == []


def test_recovery_flow_rejects_incomplete_pending_response():
    class InvalidRequestClient(_RecoveryClient):
        def request_resource_admission_recovery(self, entry, *, reason):
            self.request_calls.append((entry.robot_id, reason))
            return {
                "status": "pending_confirmation",
                "robot_id": entry.robot_id,
                "request_id": "recovery-1",
            }

    client = InvalidRequestClient()
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")

    snapshot = run_recovery_flow(
        client=client,
        entry=entry,
        reason="operator inspected the scene",
        request_only=True,
    )

    assert snapshot["phase"] == "blocked"
    assert snapshot["reason_code"] == "recovery_response_invalid"
    assert client.confirm_calls == []


def test_recovery_flow_does_not_send_locally_mismatched_phrase():
    client = _RecoveryClient()
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")

    snapshot = run_recovery_flow(
        client=client,
        entry=entry,
        reason="operator inspected the scene",
        confirmation_phrase="yes",
    )

    assert snapshot["reason_code"] == "operator_confirmation_not_submitted"
    assert snapshot["safe_state"] == "motion_blocked"
    assert client.confirm_calls == []


def test_recovery_flow_rechecks_admission_after_exact_confirmation():
    client = _RecoveryClient()
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")

    snapshot = run_recovery_flow(
        client=client,
        entry=entry,
        reason="operator inspected the scene",
        confirmation_phrase="RECOVER robot-1 recovery-1",
    )

    assert snapshot["phase"] == "ready"
    assert snapshot["reason_code"] == "resource_admission_recovered"
    assert client.confirm_calls == [
        ("recovery-1", "RECOVER robot-1 recovery-1")
    ]
    assert "旧任务不会恢复" in snapshot["operator_action"]
