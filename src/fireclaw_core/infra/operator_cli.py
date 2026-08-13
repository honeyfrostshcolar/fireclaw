"""Human-facing status, doctor, and guided admission-recovery commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.gateway.auth import resolve_gateway_api_token
from fireclaw_core.gateway.transport import GatewayTlsClientConfig
from fireclaw_core.infra.operator_readiness import (
    build_operator_doctor_snapshot,
    build_operator_recovery_snapshot,
    build_operator_status_snapshot,
    format_operator_doctor,
    format_operator_recovery,
    format_operator_status,
)
from fireclaw_core.infra.user_setup import (
    FireClawSetupError,
    resolve_active_profile_path,
)
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient
from fireclaw_core.subagent.subagent_client import RobotSubagentClient


_DEFAULT_MISSION_GATEWAY_URL = "http://127.0.0.1:8766"


def collect_operator_status(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    check_runtime: bool = True,
    gateway_url: str | None = None,
    api_token: str | None = None,
    timeout_seconds: float = 10.0,
    tls: GatewayTlsClientConfig | None = None,
    mission_gateway_url: str | None = None,
    mission_api_token: str | None = None,
    mission_tls: GatewayTlsClientConfig | None = None,
    deployment_inspector: Callable[..., dict[str, Any]] | None = None,
    service_inspector: Callable[..., dict[str, Any]] | None = None,
    robot_client: RobotSubagentClient | None = None,
    mission_client: MissionGatewayClient | None = None,
) -> dict[str, Any]:
    """Collect one bounded operator snapshot from every authoritative source."""

    from fireclaw_core.deployment import (
        DeploymentError,
        inspect_deployment_status,
        inspect_runtime_supervisor_state,
        inspect_systemd_user_service,
    )

    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    profile = load_robot_capability_profile(profile_path)
    resolved_profile = str(Path(profile_path).expanduser().resolve(strict=True))
    inspect = deployment_inspector or inspect_deployment_status
    try:
        deployment = inspect(
            resolved_profile,
            output_root=output_root,
            check_runtime=check_runtime,
        )
    except (DeploymentError, OSError, ValueError) as exc:
        deployment = {
            "status": "error",
            "code": getattr(exc, "code", "deployment_invalid"),
            "message": str(exc),
        }
    try:
        supervisor = inspect_runtime_supervisor_state(
            resolved_profile,
            output_root=output_root,
        )
    except (DeploymentError, OSError, ValueError) as exc:
        supervisor = {
            "status": "invalid",
            "reason_code": "supervisor_state_unavailable",
            "message": _safe_exception_message(exc),
        }
    inspect_service = service_inspector or inspect_systemd_user_service
    try:
        managed_service = inspect_service(
            resolved_profile,
            output_root=output_root,
            status_probe=lambda *_args, **_kwargs: deployment,
        )
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        managed_service = {
            "status": "unavailable",
            "reason_code": "managed_service_state_unavailable",
            "message": _safe_exception_message(exc),
        }

    entry = RobotRegistryEntry(
        robot_id=profile.robot_id,
        base_url=(gateway_url or profile.base_url).rstrip("/"),
        capabilities=profile.capabilities,
        enabled=profile.enabled,
    )
    client = robot_client or RobotSubagentClient(
        timeout_seconds=timeout_seconds,
        api_token=resolve_gateway_api_token(
            api_token,
            env_var="FIRECLAW_ROBOT_GATEWAY_TOKEN",
        ),
        tls=tls,
    )

    health: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    gateway_error: dict[str, Any] | None = None
    try:
        health = client.get_health(entry)
    except (HTTPError, OSError, TimeoutError, URLError, ValueError) as exc:
        gateway_error = {
            "code": "robot_gateway_unreachable",
            "message": _safe_exception_message(exc),
        }
    if health is not None:
        try:
            candidate = client.get_state(entry)
            if candidate.get("http_status") in {401, 403}:
                gateway_error = {
                    "code": "gateway_authentication_required",
                    "message": "Robot Gateway rejected the configured credential.",
                }
            elif candidate.get("status") == "error" and candidate.get(
                "http_status"
            ):
                gateway_error = {
                    "code": "robot_gateway_state_unavailable",
                    "message": str(candidate.get("error") or "Gateway state unavailable."),
                }
            else:
                state = candidate
        except (HTTPError, OSError, TimeoutError, URLError, ValueError) as exc:
            gateway_error = {
                "code": "robot_gateway_state_unavailable",
                "message": _safe_exception_message(exc),
            }

    mission_url, resolved_mission_tls, mission_source = (
        _resolve_status_mission_gateway(
            resolved_profile,
            output_root=output_root,
            override_url=mission_gateway_url,
            override_tls=mission_tls,
        )
    )
    fleet_doctor = collect_fleet_doctor(
        mission_url,
        api_token=mission_api_token,
        timeout_seconds=timeout_seconds,
        tls=resolved_mission_tls,
        mission_client=mission_client,
    )
    doctor_components = fleet_doctor.get("components")
    if isinstance(doctor_components, dict):
        doctor_components["endpoint"] = {
            "base_url": mission_url,
            "source": mission_source,
        }
    doctor_evidence = fleet_doctor.get("evidence")
    if isinstance(doctor_evidence, dict):
        doctor_evidence["probe"] = {
            "base_url": mission_url,
            "source": mission_source,
        }

    return build_operator_status_snapshot(
        deployment=deployment,
        robot_id=profile.robot_id,
        profile_path=resolved_profile,
        gateway_health=health,
        gateway_state=state,
        gateway_error=gateway_error,
        supervisor_state=supervisor,
        managed_service_state=managed_service,
        fleet_doctor=fleet_doctor,
    )


def _resolve_status_mission_gateway(
    profile_path: str | Path,
    *,
    output_root: str | Path | None,
    override_url: str | None,
    override_tls: GatewayTlsClientConfig | None,
) -> tuple[str, GatewayTlsClientConfig | None, str]:
    """Resolve a read-only Mission Gateway probe without exposing secrets."""

    profile_base_url: str | None = None
    profile_tls: GatewayTlsClientConfig | None = None
    try:
        from fireclaw_core.deployment import load_runtime_deployment_profile

        deployment_profile = load_runtime_deployment_profile(
            profile_path,
            output_root=output_root,
        )
        managed = deployment_profile.mission_gateway
        profile_base_url = managed.base_url
        if any(
            (
                managed.tls_ca_file,
                managed.tls_client_cert_file,
                managed.tls_client_key_file,
            )
        ):
            profile_tls = GatewayTlsClientConfig(
                ca_file=(
                    str(managed.tls_ca_file)
                    if managed.tls_ca_file is not None
                    else None
                ),
                cert_file=(
                    str(managed.tls_client_cert_file)
                    if managed.tls_client_cert_file is not None
                    else None
                ),
                key_file=(
                    str(managed.tls_client_key_file)
                    if managed.tls_client_key_file is not None
                    else None
                ),
            )
    except (OSError, ValueError):
        # Deployment validity is already represented by its own component. A
        # malformed deployment must not prevent the remaining bounded probes.
        pass

    if override_url is not None:
        normalized = override_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("Mission Gateway URL must be non-empty")
        return normalized, override_tls or profile_tls, "cli_override"
    if profile_base_url is not None:
        return profile_base_url.rstrip("/"), override_tls or profile_tls, "profile"
    return _DEFAULT_MISSION_GATEWAY_URL, override_tls, "default_loopback"


def collect_fleet_doctor(
    server_url: str,
    *,
    api_token: str | None = None,
    timeout_seconds: float = 10.0,
    tls: GatewayTlsClientConfig | None = None,
    mission_client: MissionGatewayClient | None = None,
) -> dict[str, Any]:
    try:
        if timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        client = mission_client or MissionGatewayClient(
            server_url,
            api_token=resolve_gateway_api_token(api_token),
            timeout=timeout_seconds,
            tls=tls,
        )
        return build_operator_doctor_snapshot(report=client.get_fleet_doctor())
    except (HTTPError, OSError, TimeoutError, URLError, ValueError) as exc:
        code = (
            "mission_gateway_authentication_required"
            if isinstance(exc, HTTPError) and exc.code in {401, 403}
            else "mission_gateway_unreachable"
        )
        return build_operator_doctor_snapshot(
            error={"code": code, "message": _safe_exception_message(exc)}
        )


def run_recovery_flow(
    *,
    client: RobotSubagentClient,
    entry: RobotRegistryEntry,
    reason: str | None,
    request_id: str | None = None,
    confirmation_phrase: str | None = None,
    request_only: bool = False,
    input_fn: Callable[[str], str] | None = None,
    on_pending: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the two-stage protocol without ever inventing operator consent."""

    if request_id is not None:
        phrase = confirmation_phrase
        if phrase is None and input_fn is not None:
            phrase = input_fn("请输入该请求显示的完整确认短语（回车保持冻结）：")
        if not isinstance(phrase, str) or not phrase.strip():
            return build_operator_recovery_snapshot(
                {
                    "status": "confirmation_not_submitted",
                    "request_id": request_id,
                    "message": "操作员没有提交确认短语。",
                }
            )
        confirmed = client.confirm_resource_admission_recovery(
            entry,
            request_id=request_id,
            confirmation_phrase=phrase.strip(),
        )
        verified = (
            client.get_resource_admission(entry)
            if confirmed.get("status") == "recovered"
            else None
        )
        return build_operator_recovery_snapshot(
            confirmed,
            verified_state=verified,
        )

    current = client.get_resource_admission(entry)
    if current.get("http_status"):
        http_status = current.get("http_status")
        return build_operator_recovery_snapshot(
            {
                "status": "error",
                "error_code": (
                    "gateway_authentication_required"
                    if http_status in {401, 403}
                    else "resource_admission_state_unavailable"
                ),
                "message": str(
                    current.get("error") or "无法读取资源准入状态。"
                ),
            }
        )
    admission = current.get("admission")
    if (
        not isinstance(admission, dict)
        or not isinstance(admission.get("closed"), bool)
    ):
        return build_operator_recovery_snapshot(
            {
                "status": "error",
                "robot_id": entry.robot_id,
                "error_code": "resource_admission_state_invalid",
                "message": "Gateway 未返回明确的资源准入冻结状态。",
            }
        )
    if admission["closed"] is False:
        return build_operator_recovery_snapshot(
            {
                "status": "not_frozen",
                "robot_id": entry.robot_id,
                "admission": admission,
            },
            verified_state=current,
        )
    if not isinstance(reason, str) or not reason.strip():
        return build_operator_recovery_snapshot(
            {
                "status": "blocked",
                "robot_id": entry.robot_id,
                "error_code": "recovery_reason_required",
                "message": "恢复请求必须说明操作员核对现场后的原因。",
            }
        )

    requested = client.request_resource_admission_recovery(
        entry,
        reason=reason.strip(),
    )
    pending_snapshot = build_operator_recovery_snapshot(requested)
    if requested.get("status") != "pending_confirmation":
        return pending_snapshot
    if not all(
        isinstance(requested.get(key), str) and requested[key].strip()
        for key in ("request_id", "confirmation_phrase")
    ):
        return build_operator_recovery_snapshot(
            {
                **requested,
                "status": "error",
                "error_code": "recovery_response_invalid",
                "message": "Gateway 返回的恢复请求缺少请求 ID 或确认短语。",
            }
        )
    if on_pending is not None:
        on_pending(pending_snapshot)
    if request_only:
        return pending_snapshot

    expected_phrase = requested.get("confirmation_phrase")
    phrase = confirmation_phrase
    if phrase is None and input_fn is not None:
        phrase = input_fn("请输入上方完整确认短语（回车保持冻结）：")
    if not isinstance(phrase, str) or not phrase.strip():
        return build_operator_recovery_snapshot(
            {
                **requested,
                "status": "confirmation_not_submitted",
                "message": "操作员没有提交确认短语。",
            }
        )
    if not isinstance(expected_phrase, str) or phrase.strip() != expected_phrase:
        # Do not send a locally mismatched phrase merely to create a denial.
        return build_operator_recovery_snapshot(
            {
                **requested,
                "status": "confirmation_not_submitted",
                "message": "输入与本次完整确认短语不一致，未提交到 Gateway。",
            }
        )

    confirmed = client.confirm_resource_admission_recovery(
        entry,
        request_id=str(requested["request_id"]),
        confirmation_phrase=phrase.strip(),
    )
    verified = (
        client.get_resource_admission(entry)
        if confirmed.get("status") == "recovered"
        else None
    )
    return build_operator_recovery_snapshot(
        confirmed,
        verified_state=verified,
    )


def handle_status(
    args: argparse.Namespace,
    *,
    out: TextIO = sys.stdout,
) -> int:
    try:
        profile_path = resolve_active_profile_path(args.profile)
        snapshot = collect_operator_status(
            profile_path,
            output_root=args.output_root,
            check_runtime=not args.no_runtime_check,
            gateway_url=args.gateway,
            api_token=args.api_token,
            timeout_seconds=args.timeout,
            tls=_tls_from_args(args),
            mission_gateway_url=getattr(args, "server", None),
            mission_api_token=getattr(args, "mission_api_token", None),
            mission_tls=_mission_tls_from_args(args),
        )
    except (OSError, ValueError) as exc:
        snapshot = _command_error_snapshot("status", exc)
        _write_snapshot(snapshot, json_output=args.json, formatter=format_operator_status, out=out)
        return 2
    _write_snapshot(snapshot, json_output=args.json, formatter=format_operator_status, out=out)
    return 0 if snapshot.get("phase") == "ready" else 1


def handle_doctor(
    args: argparse.Namespace,
    *,
    out: TextIO = sys.stdout,
) -> int:
    snapshot = collect_fleet_doctor(
        args.server,
        api_token=args.api_token,
        timeout_seconds=args.timeout,
        tls=_tls_from_args(args),
    )
    _write_snapshot(snapshot, json_output=args.json, formatter=format_operator_doctor, out=out)
    return 0 if snapshot.get("phase") == "ready" else 1


def handle_recover(
    args: argparse.Namespace,
    *,
    out: TextIO = sys.stdout,
    input_fn: Callable[[str], str] = input,
    stdin_is_tty: bool | None = None,
) -> int:
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        profile_path = resolve_active_profile_path(args.profile)
        profile = load_robot_capability_profile(profile_path)
        entry = RobotRegistryEntry(
            robot_id=profile.robot_id,
            base_url=(args.gateway or profile.base_url).rstrip("/"),
            capabilities=profile.capabilities,
            enabled=profile.enabled,
        )
        client = RobotSubagentClient(
            timeout_seconds=args.timeout,
            api_token=resolve_gateway_api_token(
                args.api_token,
                env_var="FIRECLAW_ROBOT_GATEWAY_TOKEN",
            ),
            tls=_tls_from_args(args),
        )
        interactive = (
            sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
        ) and not args.json
        reason = args.reason
        if (
            args.request_id is None
            and reason is None
            and interactive
        ):
            reason = input_fn("请说明核对现场后申请恢复的原因（回车取消）：")
        prompt = input_fn if interactive else None
        pending_printed = False

        def show_pending(snapshot: dict[str, Any]) -> None:
            nonlocal pending_printed
            _write_snapshot(
                snapshot,
                json_output=False,
                formatter=format_operator_recovery,
                out=out,
            )
            print(
                "注意：恢复只开放未来任务的运动资源，不会恢复或重跑旧任务。",
                file=out,
                flush=True,
            )
            pending_printed = True

        snapshot = run_recovery_flow(
            client=client,
            entry=entry,
            reason=reason,
            request_id=args.request_id,
            confirmation_phrase=args.confirmation_phrase,
            request_only=args.request_only,
            input_fn=prompt,
            on_pending=(show_pending if interactive else None),
        )
    except (HTTPError, OSError, TimeoutError, URLError, ValueError) as exc:
        snapshot = build_operator_recovery_snapshot(
            {
                "status": (
                    "configuration_required"
                    if isinstance(exc, FireClawSetupError)
                    else "error"
                ),
                "error_code": getattr(
                    exc,
                    "code",
                    "recovery_command_failed",
                ),
                "message": _safe_exception_message(exc),
                "operator_action": getattr(exc, "operator_action", None),
            }
        )
        pending_printed = False

    recovery_status = (
        snapshot.get("components", {}).get("status")
        if isinstance(snapshot.get("components"), dict)
        else None
    )
    if (
        not pending_printed
        or args.json
        or recovery_status != "pending_confirmation"
    ):
        _write_snapshot(
            snapshot,
            json_output=args.json,
            formatter=format_operator_recovery,
            out=out,
        )
    return 0 if snapshot.get("phase") == "ready" else 1


def _write_snapshot(
    snapshot: dict[str, Any],
    *,
    json_output: bool,
    formatter: Callable[[dict[str, Any]], str],
    out: TextIO,
) -> None:
    if json_output:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2), file=out)
    else:
        print(formatter(snapshot), file=out, flush=True)


def _tls_from_args(args: argparse.Namespace) -> GatewayTlsClientConfig:
    return GatewayTlsClientConfig(
        ca_file=getattr(args, "tls_ca_file", None),
        cert_file=getattr(args, "tls_client_cert_file", None),
        key_file=getattr(args, "tls_client_key_file", None),
    )


def _mission_tls_from_args(
    args: argparse.Namespace,
) -> GatewayTlsClientConfig | None:
    values = (
        getattr(args, "mission_tls_ca_file", None),
        getattr(args, "mission_tls_client_cert_file", None),
        getattr(args, "mission_tls_client_key_file", None),
    )
    if not any(values):
        return None
    return GatewayTlsClientConfig(
        ca_file=values[0],
        cert_file=values[1],
        key_file=values[2],
    )


def _command_error_snapshot(command: str, exc: BaseException) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": command,
        "robot_id": "unknown",
        "phase": "blocked",
        "safe_state": "unknown",
        "reason_code": getattr(exc, "code", f"{command}_command_invalid"),
        "retryable": True,
        "operator_action": getattr(
            exc,
            "operator_action",
            "检查命令参数和 Profile 后重试。",
        ),
        "evidence_id": None,
        "summary": _safe_exception_message(exc),
        "components": {},
        "evidence": {},
    }


def _safe_exception_message(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"Gateway HTTP {exc.code}."
    text = str(exc).strip()
    return text or type(exc).__name__


__all__ = [
    "collect_fleet_doctor",
    "collect_operator_status",
    "handle_doctor",
    "handle_recover",
    "handle_status",
    "run_recovery_flow",
]
