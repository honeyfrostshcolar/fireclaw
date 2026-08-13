"""Guided, auditable acceptance for real-robot hardware safety contracts.

The workflow is deliberately unavailable in simulation.  Preflight is
non-actuating.  Of the acceptance scenarios, only ``stop_proof`` invokes the
vendor-owned stop service; every negative scenario observes a condition that
an operator established under the vendor's safe-test procedure.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from fireclaw_core.agent.robot_profile import (
    RobotCapabilityProfile,
    load_robot_capability_profile,
)
from fireclaw_core.deployment.deployer import build_deployment_plan
from fireclaw_core.deployment.profile import (
    RuntimeDeploymentProfile,
    load_runtime_deployment_profile,
)
from fireclaw_core.evaluation.artifacts import (
    EvaluationRunBundle,
    canonical_json_sha256,
    make_run_id,
    sha256_file,
)
from fireclaw_core.plugin.extension_loader import (
    discover_fireclaw_extensions,
    load_fireclaw_extensions,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.ros.ros1_config import load_ros1_adapter_config
from fireclaw_plugin_sdk import HARDWARE_STOP_EVIDENCE_CLASS


PLUGIN_ID = "fireclaw.safety.ros1-hardware"
EVIDENCE_SERVICE = "fireclaw.safety.stop-evidence.ros1-hardware-safety"
ARTIFACT_SCHEMA_VERSION = "1"
ARTIFACT_KIND = "fireclaw.hardware_safety_acceptance"
PREFLIGHT_KIND = "fireclaw.hardware_safety_preflight"

SCENARIOS = (
    "stop_proof",
    "watchdog_loss",
    "emergency_stop_inactive",
    "driver_enabled",
    "brake_disengaged",
    "actuator_motion",
    "signal_stale",
    "inventory_incomplete",
)
SIGNAL_STALE_TARGETS = (
    "watchdog",
    "emergency_stop",
    "driver",
    "brake",
    "actuators",
    "independent_motion",
)
MOTION_TARGETS = ("actuators", "independent_motion")

EXPECTED_BLOCKERS: dict[str, tuple[str, ...]] = {
    "stop_proof": (),
    "watchdog_loss": ("watchdog_unhealthy",),
    "emergency_stop_inactive": ("hardware_emergency_stop_not_active",),
    "driver_enabled": ("hardware_driver_not_disabled",),
    "brake_disengaged": ("hardware_brake_not_engaged",),
    "actuator_motion": (
        "actuator_motion_detected",
        "independent_motion_detected",
    ),
    "signal_stale": (
        "watchdog_state_stale",
        "emergency_stop_state_stale",
        "driver_state_stale",
        "brake_state_stale",
        "actuator_state_stale",
        "independent_motion_state_stale",
    ),
    "inventory_incomplete": ("actuator_inventory_incomplete",),
}


@dataclass(frozen=True)
class _AcceptanceContext:
    deployment: RuntimeDeploymentProfile
    robot: RobotCapabilityProfile
    raw_profile: Mapping[str, Any]
    profile_sha256: str
    deployment_fingerprint: str | None
    provider: Any


def run_hardware_safety_preflight(
    profile_path: str | Path,
    *,
    live: bool,
    scenario: str | None = None,
    target_signal: str | None = None,
) -> tuple[dict[str, Any], _AcceptanceContext | None]:
    """Run non-actuating static and optional live checks."""

    checks: list[dict[str, str]] = []
    context: _AcceptanceContext | None = None
    profile_sha256: str | None = None
    robot_id: str | None = None
    deployment_fingerprint: str | None = None

    try:
        target = Path(profile_path).expanduser()
        if target.is_symlink():
            raise ValueError("Profile must not be a symlink")
        target = target.resolve(strict=True)
        if not target.is_file():
            raise ValueError("Profile must be a regular TOML file")
        profile_sha256 = sha256_file(target)
        with target.open("rb") as handle:
            raw_profile = tomllib.load(handle)
        deployment = load_runtime_deployment_profile(target)
        robot = load_robot_capability_profile(target)
        robot_id = robot.robot_id
        _append_check(checks, "profile.parse", True, "Profile parsers accepted the TOML")
    except Exception as exc:
        _append_check(checks, "profile.parse", False, _safe_error(exc))
        return (
            _preflight_result(
                checks=checks,
                live=live,
                scenario=scenario,
                target_signal=target_signal,
                robot_id=robot_id,
                profile_sha256=profile_sha256,
                deployment_fingerprint=None,
            ),
            None,
        )

    _append_check(
        checks,
        "deployment.real_mode",
        deployment.mode == "real",
        "deployment.mode is real" if deployment.mode == "real" else "deployment.mode must be real",
    )
    _append_check(
        checks,
        "robot.ros1_adapter",
        robot.adapter == "ros1",
        "robot.adapter is ros1" if robot.adapter == "ros1" else "robot.adapter must be ros1",
    )
    _append_check(
        checks,
        "identity.match",
        deployment.deployment_id == robot.robot_id,
        (
            "deployment and robot identities match"
            if deployment.deployment_id == robot.robot_id
            else "deployment.id must match robot.id"
        ),
    )
    _append_profile_review_checks(checks, raw_profile)

    selected = PLUGIN_ID in deployment.selected_plugin_ids
    _append_check(
        checks,
        "plugin.selected",
        selected,
        "hardware safety Plugin is selected" if selected else "select the hardware safety Plugin",
    )
    plugin_config = deployment.plugin_configs.get(PLUGIN_ID)
    enabled = isinstance(plugin_config, Mapping) and plugin_config.get("enabled") is True
    _append_check(
        checks,
        "plugin.enabled",
        enabled,
        "hardware safety Plugin is enabled" if enabled else "set the hardware safety Plugin enabled=true",
    )

    setup_files_ok = bool(deployment.ros_setup_files) and all(
        path.is_file() and not path.is_symlink()
        for path in deployment.ros_setup_files
    )
    _append_check(
        checks,
        "files.ros_setup",
        setup_files_ok,
        "all ROS setup files exist" if setup_files_ok else "one or more ROS setup files are missing or symlinks",
    )
    launch_ok = (
        deployment.robot_launch is not None
        and deployment.robot_launch.is_file()
        and not deployment.robot_launch.is_symlink()
    )
    _append_check(
        checks,
        "files.robot_launch",
        launch_ok,
        "robot launch file exists" if launch_ok else "deployment.launch.robot_launch is missing or a symlink",
    )
    plugin_paths_ok = bool(deployment.plugin_paths) and all(
        path.is_dir() and not path.is_symlink()
        for path in deployment.plugin_paths
    )
    _append_check(
        checks,
        "files.plugin_paths",
        plugin_paths_ok,
        "all Plugin roots exist" if plugin_paths_ok else "one or more Plugin roots are missing or symlinks",
    )

    ros_config_ok = False
    if robot.ros1_config is not None:
        ros_config_path = Path(robot.ros1_config)
        if ros_config_path.is_file() and not ros_config_path.is_symlink():
            try:
                adapter_config = load_ros1_adapter_config(ros_config_path)
                ros_config_ok = adapter_config.robot_id == robot.robot_id
            except Exception:
                ros_config_ok = False
    _append_check(
        checks,
        "files.ros1_config",
        ros_config_ok,
        "ROS1 adapter config exists and robot_id matches" if ros_config_ok else "robot.ros1_config is missing, invalid, or belongs to another robot",
    )

    provider = None
    if plugin_paths_ok and enabled:
        try:
            provider = _load_hardware_provider(deployment)
            _append_check(checks, "plugin.provider", True, "trusted hardware-owned Provider loaded")
        except Exception as exc:
            _append_check(checks, "plugin.provider", False, _safe_error(exc))
    else:
        _append_check(checks, "plugin.provider", False, "Plugin prerequisites are incomplete")

    static_ok_before_plan = _checks_satisfied(checks)
    if static_ok_before_plan:
        try:
            plan = build_deployment_plan(target)
            deployment_fingerprint = plan.fingerprint
            _append_check(checks, "deployment.plan", True, "immutable deployment plan resolved")
        except Exception as exc:
            _append_check(checks, "deployment.plan", False, _safe_error(exc))
    else:
        _append_check(checks, "deployment.plan", False, "fix prior static checks before planning deployment")

    if provider is not None:
        context = _AcceptanceContext(
            deployment=deployment,
            robot=robot,
            raw_profile=raw_profile,
            profile_sha256=str(profile_sha256),
            deployment_fingerprint=deployment_fingerprint,
            provider=provider,
        )

    if live and _checks_satisfied(checks) and provider is not None:
        try:
            live_result = provider.preflight()
            live_checks = live_result.get("checks") if isinstance(live_result, Mapping) else None
            if not isinstance(live_checks, list):
                raise TypeError("Provider preflight checks must be an array")
            for item in live_checks:
                if not isinstance(item, Mapping):
                    continue
                check_id = f"live.{item.get('id', 'unknown')}"
                status = str(item.get("status", "fail"))
                if status == "fail" and _expected_preflight_failure(
                    check_id,
                    scenario,
                    target_signal,
                ):
                    status = "expected"
                checks.append(
                    {
                        "id": check_id,
                        "status": status,
                        "message": str(item.get("message", "")),
                    }
                )
        except Exception as exc:
            _append_check(checks, "live.provider_preflight", False, _safe_error(exc))
    elif live:
        _append_check(checks, "live.provider_preflight", False, "static preflight is blocked")

    return (
        _preflight_result(
            checks=checks,
            live=live,
            scenario=scenario,
            target_signal=target_signal,
            robot_id=robot_id,
            profile_sha256=profile_sha256,
            deployment_fingerprint=deployment_fingerprint,
        ),
        context,
    )


def handle_hardware_safety(args: argparse.Namespace) -> int:
    if args.hardware_safety_command == "preflight":
        result, _ = run_hardware_safety_preflight(
            args.profile,
            live=not args.offline,
        )
        _emit(result, as_json=args.json)
        return 0 if result["status"] in {"prepared", "ready"} else 2
    if args.hardware_safety_command == "accept":
        return _handle_accept(args)
    if args.hardware_safety_command == "verify":
        result = verify_acceptance_artifact(args.artifact)
        _emit(result, as_json=args.json)
        return 0 if result["status"] == "valid" else 2
    if args.hardware_safety_command == "report":
        result = build_acceptance_report(
            args.profile,
            firmware_version=args.firmware_version,
            artifact_dir=args.artifact_dir,
        )
        _emit(result, as_json=args.json)
        return 0 if result["status"] == "passed" else 1
    raise ValueError(f"Unknown hardware-safety command: {args.hardware_safety_command}")


def _handle_accept(args: argparse.Namespace) -> int:
    if not _filled_string(args.operator_id) or not _filled_string(
        args.firmware_version
    ):
        _emit(
            {
                "status": "blocked",
                "code": "acceptance_identity_invalid",
                "operator_action": (
                    "Fill a real operator ID and exact firmware version; "
                    "placeholders are not accepted."
                ),
            },
            as_json=args.json,
        )
        return 2
    target_error = _target_validation_error(args.scenario, args.target_signal)
    if target_error is not None:
        _emit(
            {
                "status": "blocked",
                "code": "acceptance_target_invalid",
                "operator_action": target_error,
            },
            as_json=args.json,
        )
        return 2
    preflight, context = run_hardware_safety_preflight(
        args.profile,
        live=True,
        scenario=args.scenario,
        target_signal=args.target_signal,
    )
    if preflight["status"] != "ready" or context is None:
        result = {
            "status": "blocked",
            "code": "hardware_safety_preflight_blocked",
            "preflight": preflight,
            "operator_action": "Fix failed preflight checks; no hardware command was sent.",
        }
        _emit(result, as_json=args.json)
        return 2

    if (
        args.scenario == "brake_disengaged"
        or args.target_signal == "brake"
    ) and not _brake_required(context):
        result = {
            "status": "blocked",
            "code": "brake_not_required",
            "operator_action": "This Profile declares no hardware brake; omit this scenario.",
        }
        _emit(result, as_json=args.json)
        return 2

    phrase = acceptance_confirmation_phrase(
        robot_id=context.robot.robot_id,
        scenario=args.scenario,
        profile_sha256=context.profile_sha256,
        target_signal=args.target_signal,
    )
    supplied = args.confirmation_phrase
    if supplied is None and not args.json:
        print("The operator attests that the area is clear, the robot is in the vendor safe-test mode,")
        print("a physical emergency stop and safety observer are present, and the named scenario is established.")
        print(f"Type exactly: {phrase}")
        try:
            supplied = input("> ")
        except EOFError:
            supplied = None
    if supplied is None:
        _emit(
            {
                "status": "confirmation_required",
                "confirmation_phrase": phrase,
                "operator_action": "Review the vendor procedure, establish the scenario, then rerun with the exact phrase.",
            },
            as_json=args.json,
        )
        return 2
    if supplied != phrase:
        _emit(
            {
                "status": "blocked",
                "code": "confirmation_phrase_mismatch",
                "operator_action": "No hardware command was sent.",
            },
            as_json=args.json,
        )
        return 2

    run_id = make_run_id(f"hardware-safety-{args.scenario}")
    try:
        bundle = _claim_acceptance_bundle(
            args.artifact_dir,
            robot_id=context.robot.robot_id,
            run_id=run_id,
        )
    except Exception as exc:
        _emit(
            {
                "status": "blocked",
                "code": "acceptance_artifact_sink_unavailable",
                "message": _safe_error(exc),
                "operator_action": (
                    "Fix the local artifact directory. No hardware command "
                    "was sent."
                ),
            },
            as_json=args.json,
        )
        return 2

    started_at = datetime.now(timezone.utc).isoformat()
    if args.scenario == "stop_proof":
        evidence = context.provider.collect_stop_evidence(
            robot_id=context.robot.robot_id,
            reason="field acceptance stop_proof",
        )
        actuating = True
    else:
        evidence = context.provider.inspect_hardware_state(
            robot_id=context.robot.robot_id,
        )
        actuating = False
    passed, reasons = evaluate_acceptance_scenario(
        args.scenario,
        evidence,
        target_signal=args.target_signal,
    )
    finished_at = datetime.now(timezone.utc).isoformat()
    artifact: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": ARTIFACT_KIND,
        "run_id": run_id,
        "robot_id": context.robot.robot_id,
        "scenario": args.scenario,
        "target_signal": args.target_signal,
        "actuating": actuating,
        "profile_sha256": context.profile_sha256,
        "deployment_fingerprint": context.deployment_fingerprint,
        "firmware_version": args.firmware_version,
        "operator_id": args.operator_id,
        "attestations": [
            "test_area_clear",
            "vendor_safe_test_mode",
            "physical_emergency_stop_available",
            "independent_safety_observer_present",
        ],
        "started_at": started_at,
        "finished_at": finished_at,
        "preflight": preflight,
        "expected_blockers": list(
            _expected_blockers(args.scenario, args.target_signal)
        ),
        "evidence": evidence,
        "passed": passed,
        "failure_reasons": reasons,
    }
    artifact["artifact_sha256"] = canonical_json_sha256(artifact)
    try:
        artifact_path = _finalize_acceptance_artifact(bundle, artifact)
    except Exception as exc:
        _emit(
            {
                "status": "failed",
                "code": "acceptance_artifact_write_failed",
                "message": _safe_error(exc),
                "run_directory": str(bundle.run_dir),
                "operator_action": (
                    "The hardware observation already occurred but durable "
                    "evidence could not be finalized. Keep the robot safe, "
                    "repair storage, and repeat the scenario."
                ),
            },
            as_json=args.json,
        )
        return 2
    result = {
        "status": "passed" if passed else "failed",
        "scenario": args.scenario,
        "target_signal": args.target_signal,
        "actuating": actuating,
        "artifact": str(artifact_path),
        "artifact_sha256": artifact["artifact_sha256"],
        "failure_reasons": reasons,
        "operator_action": (
            "Run the next scenario; use report only after all required scenarios pass."
            if passed
            else "Keep the robot in a safe state and investigate the recorded blockers."
        ),
    }
    _emit(result, as_json=args.json)
    return 0 if passed else 1


def acceptance_confirmation_phrase(
    *,
    robot_id: str,
    scenario: str,
    profile_sha256: str,
    target_signal: str | None = None,
) -> str:
    case = scenario if target_signal is None else f"{scenario}:{target_signal}"
    return f"HARDWARE SAFETY TEST {robot_id} {case} {profile_sha256[:12]}"


def evaluate_acceptance_scenario(
    scenario: str,
    evidence: Mapping[str, Any],
    *,
    target_signal: str | None = None,
) -> tuple[bool, list[str]]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported hardware safety scenario: {scenario}")
    details = evidence.get("details")
    if not isinstance(details, Mapping):
        return False, ["evidence_details_missing"]
    blockers_raw = details.get("blockers", ())
    blockers = {
        item for item in blockers_raw if isinstance(item, str)
    } if isinstance(blockers_raw, (list, tuple)) else set()
    if "hardware_observer_failed" in blockers:
        return False, ["hardware_observer_failed"]

    reasons: list[str] = []
    if scenario == "stop_proof":
        if evidence.get("evidence_class") != HARDWARE_STOP_EVIDENCE_CLASS:
            reasons.append("wrong_stop_evidence_class")
        if evidence.get("status") != "stopped":
            reasons.append("hardware_stop_not_proven")
        if blockers:
            reasons.append("stop_evidence_has_blockers")
        return not reasons, reasons

    if evidence.get("evidence_class") != "hardware_safety_observation_v1":
        reasons.append("wrong_observation_evidence_class")

    target_error = _target_validation_error(scenario, target_signal)
    if target_error is not None:
        return False, ["acceptance_target_invalid"]
    expected = set(_expected_blockers(scenario, target_signal))
    if not expected.issubset(blockers):
        reasons.append("expected_safety_rejection_not_observed")

    if scenario == "watchdog_loss":
        watchdog = _mapping(details.get("watchdog"))
        driver = _mapping(details.get("driver"))
        if (
            watchdog.get("fresh") is not True
            or watchdog.get("stop_asserted") is not True
            or not _signal_samples_sufficient(watchdog, details)
        ):
            reasons.append("watchdog_did_not_assert_fresh_stop")
        if (
            driver.get("fresh") is not True
            or driver.get("enabled") is not False
            or not _signal_samples_sufficient(driver, details)
        ):
            reasons.append("driver_not_disabled_after_watchdog_loss")
        if not _stationary(details):
            reasons.append("robot_not_proven_stationary_after_watchdog_loss")
    elif scenario == "emergency_stop_inactive":
        signal = _mapping(details.get("emergency_stop"))
        if (
            signal.get("fresh") is not True
            or signal.get("active") is not False
            or not _signal_samples_sufficient(signal, details)
        ):
            reasons.append("fresh_inactive_emergency_stop_not_observed")
    elif scenario == "driver_enabled":
        signal = _mapping(details.get("driver"))
        if (
            signal.get("fresh") is not True
            or signal.get("enabled") is not True
            or not _signal_samples_sufficient(signal, details)
        ):
            reasons.append("fresh_enabled_driver_not_observed")
    elif scenario == "brake_disengaged":
        signal = _mapping(details.get("brake"))
        if (
            signal.get("fresh") is not True
            or signal.get("engaged") is not False
            or not _signal_samples_sufficient(signal, details)
        ):
            reasons.append("fresh_disengaged_brake_not_observed")
    elif scenario == "inventory_incomplete":
        signal = _mapping(details.get("actuators"))
        if (
            signal.get("fresh") is not True
            or signal.get("inventory_complete") is not False
            or not _motion_samples_sufficient(signal, details)
        ):
            reasons.append("fresh_incomplete_inventory_not_observed")
    elif scenario == "actuator_motion":
        signal = _mapping(details.get(str(target_signal)))
        if (
            signal.get("fresh") is not True
            or not _motion_samples_sufficient(signal, details)
        ):
            reasons.append("fresh_target_motion_not_observed")

    return not reasons, reasons


def verify_acceptance_artifact(path: str | Path) -> dict[str, Any]:
    try:
        target = Path(path).expanduser()
        if target.is_symlink():
            raise ValueError("artifact must not be a symlink")
        target = target.resolve(strict=True)
        if not target.is_file():
            raise ValueError("artifact must be a regular JSON file")
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("artifact root must be an object")
        claimed = raw.pop("artifact_sha256", None)
        actual = canonical_json_sha256(raw)
        if claimed != actual:
            raise ValueError("artifact digest mismatch")
        if raw.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported artifact schema_version")
        if raw.get("kind") != ARTIFACT_KIND:
            raise ValueError("unexpected artifact kind")
        if raw.get("scenario") not in SCENARIOS:
            raise ValueError("unsupported artifact scenario")
        if _target_validation_error(
            str(raw.get("scenario")),
            raw.get("target_signal"),
        ) is not None:
            raise ValueError("artifact target_signal is invalid")
        if not isinstance(raw.get("passed"), bool):
            raise ValueError("artifact passed must be boolean")
        if raw.get("actuating") is not (raw.get("scenario") == "stop_proof"):
            raise ValueError("artifact actuating flag does not match scenario")
        profile_digest = raw.get("profile_sha256")
        if (
            not isinstance(profile_digest, str)
            or len(profile_digest) != 64
            or any(character not in "0123456789abcdef" for character in profile_digest)
        ):
            raise ValueError("artifact profile_sha256 is invalid")
        if not isinstance(raw.get("evidence"), Mapping):
            raise ValueError("artifact evidence must be an object")
        if not isinstance(raw.get("preflight"), Mapping):
            raise ValueError("artifact preflight must be an object")
        if not _timezone_iso(raw.get("started_at")) or not _timezone_iso(
            raw.get("finished_at")
        ):
            raise ValueError("artifact timestamps must be timezone-aware ISO-8601")
        for required in (
            "run_id",
            "robot_id",
            "target_signal",
            "actuating",
            "profile_sha256",
            "deployment_fingerprint",
            "firmware_version",
            "operator_id",
            "attestations",
            "started_at",
            "finished_at",
            "preflight",
            "expected_blockers",
            "passed",
            "evidence",
            "failure_reasons",
        ):
            if required not in raw:
                raise ValueError(f"artifact missing required field: {required}")
        if not _filled_string(raw.get("robot_id")):
            raise ValueError("artifact robot_id is invalid")
        if not _filled_string(raw.get("operator_id")):
            raise ValueError("artifact operator_id is invalid")
        if not _filled_string(raw.get("firmware_version")):
            raise ValueError("artifact firmware_version is invalid")
        attestations = raw.get("attestations")
        required_attestations = {
            "test_area_clear",
            "vendor_safe_test_mode",
            "physical_emergency_stop_available",
            "independent_safety_observer_present",
        }
        if not isinstance(attestations, list) or not required_attestations.issubset(
            {item for item in attestations if isinstance(item, str)}
        ):
            raise ValueError("artifact operator attestations are incomplete")
        preflight = _mapping(raw.get("preflight"))
        if (
            preflight.get("status") != "ready"
            or preflight.get("live_checked") is not True
        ):
            raise ValueError("artifact preflight is not a live READY result")
        started_at = datetime.fromisoformat(
            str(raw["started_at"]).replace("Z", "+00:00")
        )
        finished_at = datetime.fromisoformat(
            str(raw["finished_at"]).replace("Z", "+00:00")
        )
        if finished_at < started_at:
            raise ValueError("artifact finished_at precedes started_at")
        evidence = _mapping(raw.get("evidence"))
        if evidence.get("robot_id") != raw.get("robot_id"):
            raise ValueError("artifact evidence belongs to another robot")
        if (
            evidence.get("deployment_mode") != "real"
            or evidence.get("dry_run") is not False
        ):
            raise ValueError("artifact evidence is not from real non-dry-run mode")
        expected_blockers = list(
            _expected_blockers(
                str(raw["scenario"]),
                raw.get("target_signal"),
            )
        )
        if raw.get("expected_blockers") != expected_blockers:
            raise ValueError("artifact expected_blockers do not match scenario")
        evaluated, reasons = evaluate_acceptance_scenario(
            str(raw["scenario"]),
            evidence,
            target_signal=raw.get("target_signal"),
        )
        if raw.get("passed") is not evaluated:
            raise ValueError("artifact passed flag does not match evidence")
        if raw.get("failure_reasons") != reasons:
            raise ValueError("artifact failure_reasons do not match evidence")
        return {
            "status": "valid",
            "artifact": str(target),
            "artifact_sha256": claimed,
            "scenario": raw["scenario"],
            "passed": raw["passed"] is True,
            "content": raw,
        }
    except Exception as exc:
        return {
            "status": "invalid",
            "artifact": str(path),
            "code": "acceptance_artifact_invalid",
            "message": _safe_error(exc),
        }


def build_acceptance_report(
    profile_path: str | Path,
    *,
    firmware_version: str,
    artifact_dir: str | Path,
) -> dict[str, Any]:
    if not _filled_string(firmware_version):
        return {
            "status": "blocked",
            "code": "firmware_version_invalid",
            "message": "firmware_version must be exact and must not be a placeholder",
        }
    try:
        static_preflight, context = run_hardware_safety_preflight(
            profile_path,
            live=False,
        )
        if static_preflight.get("status") != "prepared" or context is None:
            return {
                "status": "blocked",
                "code": "acceptance_profile_not_prepared",
                "preflight": static_preflight,
            }
        robot = context.robot
        profile_sha256 = context.profile_sha256
        brake_required = _brake_required(context)
        root = Path(artifact_dir).expanduser()
        if root.is_symlink():
            raise ValueError("artifact directory must not be a symlink")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("artifact directory must be a directory")
    except Exception as exc:
        return {
            "status": "blocked",
            "code": "acceptance_report_invalid",
            "message": _safe_error(exc),
        }

    required_cases = _required_cases(brake_required=brake_required)
    required = [case_id for case_id, _, _ in required_cases]
    latest: dict[str, dict[str, Any]] = {}
    invalid_count = 0
    for index, path in enumerate(sorted(root.rglob("acceptance.json"))):
        if index >= 10_000:
            invalid_count += 1
            break
        verified = verify_acceptance_artifact(path)
        if verified.get("status") != "valid":
            invalid_count += 1
            continue
        content = _mapping(verified.get("content"))
        if (
            content.get("robot_id") != robot.robot_id
            or content.get("profile_sha256") != profile_sha256
            or content.get("firmware_version") != firmware_version
        ):
            continue
        scenario = str(content.get("scenario"))
        target_signal = content.get("target_signal")
        case_id = _case_id(
            scenario,
            target_signal if isinstance(target_signal, str) else None,
        )
        previous = latest.get(case_id)
        if previous is None or str(content.get("finished_at")) > str(previous.get("finished_at")):
            latest[case_id] = dict(content) | {"artifact": str(path)}

    missing = [scenario for scenario in required if scenario not in latest]
    failed = [
        scenario
        for scenario in required
        if scenario in latest and latest[scenario].get("passed") is not True
    ]
    passed = not missing and not failed
    return {
        "status": "passed" if passed else "blocked",
        "kind": "fireclaw.hardware_safety_acceptance_report",
        "robot_id": robot.robot_id,
        "profile_sha256": profile_sha256,
        "firmware_version": firmware_version,
        "required_scenarios": required,
        "passed_scenarios": [
            scenario for scenario in required if scenario in latest and latest[scenario].get("passed") is True
        ],
        "missing_scenarios": missing,
        "failed_scenarios": failed,
        "artifacts": {
            scenario: latest[scenario]["artifact"]
            for scenario in required
            if scenario in latest
        },
        "invalid_artifact_count": invalid_count,
        "operator_action": (
            "Field acceptance is complete for this exact Profile and firmware."
            if passed
            else "Run or repeat every missing/failed scenario before enabling real missions."
        ),
    }


def _load_hardware_provider(deployment: RuntimeDeploymentProfile) -> Any:
    discovery = discover_fireclaw_extensions(deployment.plugin_paths)
    if discovery.diagnostics:
        raise ValueError("Plugin discovery contains diagnostics")
    discovered_ids = {
        candidate.manifest.plugin_id for candidate in discovery.candidates
    }
    if PLUGIN_ID not in discovered_ids:
        raise ValueError("hardware safety Plugin was not discovered")
    configs: dict[str, Mapping[str, Any]] = {
        plugin_id: {"enabled": False}
        for plugin_id in discovered_ids
    }
    configs[PLUGIN_ID] = dict(deployment.plugin_configs.get(PLUGIN_ID, {}))
    host = FireClawPluginHost()
    report = load_fireclaw_extensions(
        host,
        deployment.plugin_paths,
        mode="real",
        role="robot_agent",
        plugin_configs=configs,
        services={"adapter": "ros1"},
        strict=True,
    )
    if not report.ok:
        raise ValueError("hardware safety Plugin did not load cleanly")
    contribution = host.get("service", EVIDENCE_SERVICE)
    record = host.record(PLUGIN_ID)
    if contribution is None or record is None:
        raise ValueError("hardware safety Provider was not registered")
    provider = contribution.value
    if contribution.owner_plugin_id != PLUGIN_ID or record.trust_level != "trusted":
        raise ValueError("hardware safety Provider ownership or trust is invalid")
    if (
        getattr(provider, "hardware_owned", None) is not True
        or getattr(provider, "evidence_class", None) != HARDWARE_STOP_EVIDENCE_CLASS
    ):
        raise ValueError("hardware safety Provider is not qualified for real recovery")
    return provider


def _append_profile_review_checks(
    checks: list[dict[str, str]],
    raw_profile: Mapping[str, Any],
) -> None:
    review = raw_profile.get("hardware_safety_acceptance")
    review = review if isinstance(review, Mapping) else {}
    _append_check(
        checks,
        "review.approved",
        review.get("profile_reviewed") is True,
        "hardware mapping was explicitly reviewed" if review.get("profile_reviewed") is True else "set hardware_safety_acceptance.profile_reviewed=true after review",
    )
    reviewer = review.get("reviewed_by")
    reviewer_ok = _filled_string(reviewer)
    _append_check(
        checks,
        "review.operator",
        reviewer_ok,
        "reviewer identity is recorded" if reviewer_ok else "fill hardware_safety_acceptance.reviewed_by",
    )
    reviewed_at = review.get("reviewed_at")
    reviewed_at_ok = False
    if isinstance(reviewed_at, str):
        try:
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
            reviewed_at_ok = parsed.tzinfo is not None
        except ValueError:
            reviewed_at_ok = False
    _append_check(
        checks,
        "review.timestamp",
        reviewed_at_ok,
        "review timestamp is timezone-aware" if reviewed_at_ok else "fill reviewed_at with a timezone-aware ISO-8601 timestamp",
    )
    reference_ok = _filled_string(review.get("vendor_safety_reference"))
    _append_check(
        checks,
        "review.vendor_reference",
        reference_ok,
        "vendor safety reference is recorded" if reference_ok else "fill the vendor manual/revision used for the mapping",
    )


def _preflight_result(
    *,
    checks: list[dict[str, str]],
    live: bool,
    scenario: str | None,
    target_signal: str | None,
    robot_id: str | None,
    profile_sha256: str | None,
    deployment_fingerprint: str | None,
) -> dict[str, Any]:
    satisfied = _checks_satisfied(checks)
    status = "ready" if live and satisfied else "prepared" if not live and satisfied else "blocked"
    return {
        "schema_version": "1",
        "kind": PREFLIGHT_KIND,
        "status": status,
        "robot_id": robot_id,
        "profile_sha256": profile_sha256,
        "deployment_fingerprint": deployment_fingerprint,
        "live_checked": live,
        "scenario": scenario,
        "target_signal": target_signal,
        "checks": checks,
        "operator_action": (
            "The non-actuating live contract is ready; proceed with one guided scenario."
            if status == "ready"
            else "Static preparation is complete; rerun without --offline on the robot."
            if status == "prepared"
            else "Fix every failed check before field acceptance."
        ),
    }


def _expected_preflight_failure(
    check_id: str,
    scenario: str | None,
    target_signal: str | None,
) -> bool:
    if scenario == "signal_stale" and target_signal is not None:
        return check_id.startswith(f"live.topic.{target_signal}.")
    if scenario == "inventory_incomplete":
        return check_id == "live.topic.actuators.sample"
    return False


def _target_validation_error(
    scenario: str,
    target_signal: Any,
) -> str | None:
    if scenario == "signal_stale":
        if target_signal not in SIGNAL_STALE_TARGETS:
            return (
                "signal_stale requires --target-signal set to one of: "
                + ", ".join(SIGNAL_STALE_TARGETS)
            )
        return None
    if scenario == "actuator_motion":
        if target_signal not in MOTION_TARGETS:
            return (
                "actuator_motion requires --target-signal set to one of: "
                + ", ".join(MOTION_TARGETS)
            )
        return None
    if target_signal is not None:
        return "--target-signal is only valid for signal_stale or actuator_motion"
    return None


def _expected_blockers(
    scenario: str,
    target_signal: str | None,
) -> tuple[str, ...]:
    if scenario == "signal_stale":
        return {
            "watchdog": ("watchdog_state_stale",),
            "emergency_stop": ("emergency_stop_state_stale",),
            "driver": ("driver_state_stale",),
            "brake": ("brake_state_stale",),
            "actuators": ("actuator_state_stale",),
            "independent_motion": ("independent_motion_state_stale",),
        }.get(str(target_signal), ())
    if scenario == "actuator_motion":
        return {
            "actuators": ("actuator_motion_detected",),
            "independent_motion": ("independent_motion_detected",),
        }.get(str(target_signal), ())
    return EXPECTED_BLOCKERS[scenario]


def _case_id(scenario: str, target_signal: str | None) -> str:
    return scenario if target_signal is None else f"{scenario}:{target_signal}"


def _required_cases(
    *,
    brake_required: bool,
) -> list[tuple[str, str, str | None]]:
    cases: list[tuple[str, str, str | None]] = []
    for scenario in (
        "stop_proof",
        "watchdog_loss",
        "emergency_stop_inactive",
        "driver_enabled",
    ):
        cases.append((_case_id(scenario, None), scenario, None))
    if brake_required:
        cases.append(("brake_disengaged", "brake_disengaged", None))
    for target in MOTION_TARGETS:
        cases.append((_case_id("actuator_motion", target), "actuator_motion", target))
    for target in SIGNAL_STALE_TARGETS:
        if target == "brake" and not brake_required:
            continue
        cases.append((_case_id("signal_stale", target), "signal_stale", target))
    cases.append(("inventory_incomplete", "inventory_incomplete", None))
    return cases


def _checks_satisfied(checks: list[dict[str, str]]) -> bool:
    return bool(checks) and all(
        item.get("status") in {"pass", "expected"}
        for item in checks
    )


def _append_check(
    checks: list[dict[str, str]],
    check_id: str,
    passed: bool,
    message: str,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "pass" if passed else "fail",
            "message": message,
        }
    )


def _brake_required(context: _AcceptanceContext) -> bool:
    brake = _mapping(
        _mapping(context.deployment.plugin_configs.get(PLUGIN_ID)).get("brake")
    )
    return brake.get("required") is True


def _stationary(details: Mapping[str, Any]) -> bool:
    actuators = _mapping(details.get("actuators"))
    motion = _mapping(details.get("independent_motion"))
    minimum = details.get("minimum_samples")
    hold = details.get("hold_seconds")
    if not isinstance(minimum, int) or isinstance(minimum, bool):
        return False
    if (
        not isinstance(hold, (int, float))
        or isinstance(hold, bool)
        or float(hold) < 0.75
    ):
        return False
    blockers = details.get("blockers")
    blocker_set = {
        item for item in blockers if isinstance(item, str)
    } if isinstance(blockers, (list, tuple)) else set()
    if blocker_set.intersection(
        {
            "actuator_state_stale",
            "actuator_inventory_incomplete",
            "actuator_motion_detected",
            "actuator_samples_insufficient",
            "actuator_hold_window_insufficient",
            "independent_motion_state_stale",
            "independent_motion_detected",
            "independent_motion_samples_insufficient",
            "independent_motion_hold_window_insufficient",
        }
    ):
        return False
    return (
        actuators.get("fresh") is True
        and actuators.get("inventory_complete") is True
        and _integer_at_least(actuators.get("stationary_samples"), minimum)
        and _number_at_least(actuators.get("sample_span_seconds"), float(hold))
        and motion.get("fresh") is True
        and _integer_at_least(motion.get("stationary_samples"), minimum)
        and _number_at_least(motion.get("sample_span_seconds"), float(hold))
    )


def _signal_samples_sufficient(
    signal: Mapping[str, Any],
    details: Mapping[str, Any],
) -> bool:
    minimum = details.get("minimum_samples")
    return (
        isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and _integer_at_least(signal.get("sample_count"), minimum)
    )


def _motion_samples_sufficient(
    signal: Mapping[str, Any],
    details: Mapping[str, Any],
) -> bool:
    minimum = details.get("minimum_samples")
    return (
        isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and _integer_at_least(signal.get("sample_count"), minimum)
        and _number_at_least(signal.get("sample_span_seconds"), 0.75)
    )


def _integer_at_least(value: Any, minimum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
    )


def _number_at_least(value: Any, minimum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value) >= minimum
    )


def _write_acceptance_artifact(
    artifact: Mapping[str, Any],
    *,
    root: str | Path,
) -> Path:
    robot_id = str(artifact["robot_id"])
    run_id = str(artifact["run_id"])
    bundle = _claim_acceptance_bundle(
        root,
        robot_id=robot_id,
        run_id=run_id,
    )
    return _finalize_acceptance_artifact(bundle, artifact)


def _claim_acceptance_bundle(
    root: str | Path,
    *,
    robot_id: str,
    run_id: str,
) -> EvaluationRunBundle:
    authored_root = Path(root).expanduser()
    if authored_root.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    authored_root.mkdir(parents=True, exist_ok=True)
    resolved_root = authored_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("artifact root must be a directory")
    robot_dir = resolved_root / robot_id
    if robot_dir.is_symlink():
        raise ValueError("robot artifact directory must not be a symlink")
    robot_dir.mkdir(mode=0o700, exist_ok=True)
    robot_dir = robot_dir.resolve(strict=True)
    robot_dir.relative_to(resolved_root)
    robot_dir.chmod(0o700)
    run_dir = robot_dir / run_id
    if run_dir.is_symlink():
        raise ValueError("acceptance run directory must not be a symlink")
    return EvaluationRunBundle(run_dir, run_id=run_id)


def _finalize_acceptance_artifact(
    bundle: EvaluationRunBundle,
    artifact: Mapping[str, Any],
) -> Path:
    target = bundle.write_json("acceptance.json", dict(artifact))
    bundle.finalize_artifact_manifest()
    bundle.run_dir.chmod(0o700)
    for path in bundle.run_dir.iterdir():
        if path.is_file():
            path.chmod(0o600)
    return target


def _filled_string(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.strip().lower()
    return not any(marker in lowered for marker in ("replace_me", "todo", "changeme"))


def _timezone_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message if message else type(exc).__name__


def _emit(result: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(dict(result), ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status: {str(result.get('status', 'unknown')).upper()}")
    if result.get("code"):
        print(f"code: {result['code']}")
    if result.get("message"):
        print(f"message: {result['message']}")
    _print_checks(result.get("checks"))
    nested_preflight = result.get("preflight")
    if isinstance(nested_preflight, Mapping):
        print(f"preflight: {str(nested_preflight.get('status', 'unknown')).upper()}")
        _print_checks(nested_preflight.get("checks"))
    for key in (
        "robot_id",
        "scenario",
        "target_signal",
        "firmware_version",
        "artifact",
        "artifact_sha256",
        "run_directory",
        "confirmation_phrase",
    ):
        if result.get(key) is not None:
            print(f"{key}: {result[key]}")
    for key in (
        "passed_scenarios",
        "missing_scenarios",
        "failed_scenarios",
        "failure_reasons",
    ):
        values = result.get(key)
        if isinstance(values, list):
            print(f"{key}: {', '.join(str(value) for value in values) or '-'}")
    action = result.get("operator_action")
    if action:
        print(f"next: {action}")


def _print_checks(value: Any) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, Mapping):
            print(
                f"[{str(item.get('status', 'unknown')).upper()}] "
                f"{item.get('id')}: {item.get('message', '')}"
            )


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_SCHEMA_VERSION",
    "SCENARIOS",
    "SIGNAL_STALE_TARGETS",
    "acceptance_confirmation_phrase",
    "build_acceptance_report",
    "evaluate_acceptance_scenario",
    "handle_hardware_safety",
    "run_hardware_safety_preflight",
    "verify_acceptance_artifact",
]
