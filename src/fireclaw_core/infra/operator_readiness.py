"""Operator-facing projections for FireClaw readiness and recovery.

The raw deployment, Gateway, and safety records remain authoritative.  This
module only projects them into a compact, actionable envelope; it must never
turn process liveness into robot readiness or infer physical standstill from
an idle task queue.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


OPERATOR_STATUS_SCHEMA_VERSION = 1


def build_operator_status_snapshot(
    *,
    deployment: Mapping[str, Any],
    robot_id: str,
    profile_path: str,
    gateway_health: Mapping[str, Any] | None = None,
    gateway_state: Mapping[str, Any] | None = None,
    gateway_error: Mapping[str, Any] | None = None,
    supervisor_state: Mapping[str, Any] | None = None,
    managed_service_state: Mapping[str, Any] | None = None,
    fleet_doctor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project raw robot status without weakening any safety state."""

    deployment_status = _text(deployment.get("status"), "unknown")
    health = dict(gateway_health) if gateway_health is not None else None
    state = dict(gateway_state) if gateway_state is not None else None
    error = dict(gateway_error) if gateway_error is not None else None
    supervisor = (
        dict(supervisor_state) if supervisor_state is not None else None
    )
    managed_service = (
        dict(managed_service_state)
        if managed_service_state is not None
        else None
    )

    if state is not None:
        gateway_liveness = "online"
        gateway_access = "available"
    elif health is not None:
        gateway_liveness = "online"
        gateway_access = "unavailable"
    elif error is not None:
        gateway_liveness = "offline"
        gateway_access = "unavailable"
    else:
        gateway_liveness = "unknown"
        gateway_access = "unknown"

    resource = _mapping(state, "resource_admission")
    admission = _mapping(resource, "admission")
    admission_closed_value = admission.get("closed")
    admission_known = isinstance(admission_closed_value, bool)
    admission_closed = admission_closed_value is True
    pending_recovery = _mapping_or_none(resource.get("pending_recovery"))
    stop_evidence_value = resource.get("stop_evidence_providers")
    stop_evidence_providers_known = isinstance(stop_evidence_value, list)
    stop_evidence_providers = _list_of_mappings(stop_evidence_value)
    real_stop_evidence_providers = [
        item
        for item in stop_evidence_providers
        if item.get("qualified_for_real") is True
    ]
    emergency_stop = _mapping(state, "emergency_stop")
    emergency_stop_value = emergency_stop.get("active")
    emergency_stop_known = isinstance(emergency_stop_value, bool)
    emergency_stop_active = emergency_stop_value is True
    runtime_storage = _mapping(state, "runtime_storage")
    runtime_storage_status = _optional_text(
        runtime_storage.get("status")
    )
    robot_state = _mapping(state, "robot_state")
    robot_online = robot_state.get("online")
    active_tasks = _list_of_mappings(state.get("active_tasks") if state else None)

    if admission_closed or emergency_stop_active:
        safe_state = "motion_blocked"
    elif not emergency_stop_known or not admission_known or robot_online is not True:
        safe_state = "unknown"
    elif active_tasks:
        safe_state = "motion_active_or_pending"
    elif admission_known and admission.get("closed") is False:
        safe_state = "motion_admitted_idle"
    else:
        safe_state = "unknown"

    if gateway_liveness == "offline":
        phase = "offline"
        deployment_blockers = {
            "not_deployed": "deployment_not_deployed",
            "stale": "deployment_stale",
            "invalid": "deployment_invalid",
            "error": _text(deployment.get("code"), "deployment_error"),
        }
        reason_code = deployment_blockers.get(
            deployment_status,
            _text(
                error.get("code") if error else None,
                "robot_gateway_unreachable",
            ),
        )
        summary = "Robot Gateway 不可达，当前无法确认机器人运行状态。"
        retryable = True
        if deployment_status in deployment_blockers:
            operator_action = f"先运行 fireclaw deploy apply --profile {profile_path}。"
        else:
            service_status = _optional_text(
                managed_service.get("status") if managed_service else None
            )
            if service_status == "inactive":
                operator_action = (
                    f"运行 fireclaw deploy service start --profile {profile_path}，"
                    "再重新运行 status。"
                )
            elif service_status == "not_installed":
                operator_action = (
                    f"运行 fireclaw deploy service install --profile {profile_path}，"
                    "再重新运行 status。"
                )
            elif service_status in {"stale", "invalid"}:
                operator_action = (
                    f"重新运行 fireclaw deploy service install --profile {profile_path}，"
                    "并检查 systemd 状态。"
                )
            else:
                deployment_root = _optional_text(
                    deployment.get("deployment_root")
                )
                runtime = (
                    str(
                        Path(deployment_root)
                        / "current"
                        / "bin"
                        / "fireclaw-runtime"
                    )
                    if deployment_root is not None
                    else "已部署 release 中的 bin/fireclaw-runtime"
                )
                operator_action = (
                    f"查看 supervisor/systemd 日志后运行 {runtime}，"
                    "再重新运行 status。"
                )
    elif state is None:
        phase = "blocked"
        reason_code = _text(
            error.get("code") if error else None,
            "robot_gateway_state_unavailable",
        )
        summary = "Robot Gateway 进程在线，但受保护状态不可读取。"
        retryable = True
        operator_action = "检查 Gateway 管理员凭据与访问权限后重试。"
    elif emergency_stop_active:
        phase = "blocked"
        reason_code = "emergency_stop_active"
        summary = "机器人急停状态仍然有效，新的运动任务不会执行。"
        retryable = False
        operator_action = "先核对现场和急停来源；只有满足恢复条件时才运行 recover。"
    elif admission_closed:
        phase = "blocked"
        reason_code = _text(
            admission.get("reason"),
            "resource_admission_closed",
        )
        summary = "机器人运动资源准入已冻结，新的运动任务不会执行。"
        retryable = False
        if (
            stop_evidence_providers_known
            and health is not None
            and health.get("dry_run") is False
            and not real_stop_evidence_providers
        ):
            operator_action = (
                "保持冻结；先接入并启用硬件拥有的整机停止证据 Provider，"
                "再运行 fireclaw recover。"
            )
        else:
            operator_action = "保持 Runtime 在线，运行 fireclaw recover 并核对现场停止证据。"
    elif runtime_storage_status == "degraded":
        phase = "blocked"
        reason_code = _text(
            runtime_storage.get("code"),
            "runtime_state_unavailable",
        )
        summary = "权威运行状态当前不可写，Gateway 已停止接收新任务。"
        retryable = True
        operator_action = (
            "修复磁盘空间或数据库锁后，使用相同 dedupe_key 重试原命令；"
            "成功提交后再重新运行 status。"
        )
    elif not emergency_stop_known:
        phase = "blocked"
        reason_code = "emergency_stop_state_unknown"
        summary = "Gateway 未提供可信的急停状态，不能判定机器人可接收物理任务。"
        retryable = True
        operator_action = "检查 Robot Adapter 与急停链状态，再重新运行 status。"
    elif not admission_known:
        phase = "blocked"
        reason_code = "resource_admission_state_unknown"
        summary = "Gateway 未提供明确的运动资源准入状态。"
        retryable = True
        operator_action = "检查 Gateway 状态接口和持久化状态；不要提交运动任务。"
    elif robot_online is False:
        phase = "blocked"
        reason_code = "robot_reported_offline"
        summary = "Gateway 可访问，但 Robot Adapter 报告机器人离线。"
        retryable = True
        operator_action = "检查底盘、ROS transport 和急停链，不要提交物理任务。"
    elif robot_online is not True:
        phase = "blocked"
        reason_code = "robot_state_unknown"
        summary = "Gateway 可访问，但 Robot Adapter 在线状态未知。"
        retryable = True
        operator_action = "检查 Robot Adapter 状态来源；确认在线前不要提交物理任务。"
    elif deployment_status in {"not_deployed", "stale", "invalid", "error"}:
        phase = "blocked"
        reason_code = {
            "not_deployed": "deployment_not_deployed",
            "stale": "deployment_stale",
            "invalid": "deployment_invalid",
            "error": _text(deployment.get("code"), "deployment_error"),
        }[deployment_status]
        summary = {
            "not_deployed": "Plugin Runtime 尚未部署。",
            "stale": "当前 Profile 与已安装 Runtime 不一致。",
            "invalid": "已安装 Runtime 的完整性检查失败。",
            "error": "无法完成 Runtime 部署检查。",
        }[deployment_status]
        retryable = True
        operator_action = f"检查后运行 fireclaw deploy apply --profile {profile_path}。"
    elif deployment_status == "installed_not_ready":
        phase = "blocked"
        reason_code = "runtime_not_ready"
        summary = "部署产物有效，但一个或多个 Runtime readiness 检查未通过。"
        retryable = True
        operator_action = "启动或修复对应 Runtime，再重新运行 status。"
    elif deployment_status == "installed":
        phase = "degraded"
        reason_code = "runtime_readiness_not_checked"
        summary = "部署完整性已通过，但本次没有验证 live Runtime readiness。"
        retryable = True
        operator_action = "不带 --no-runtime-check 重新运行 status。"
    elif health is None or health.get("status") != "ok":
        phase = "degraded"
        reason_code = "gateway_health_degraded"
        summary = "Gateway 状态可读取，但 liveness health 没有报告 ok。"
        retryable = True
        operator_action = "运行 doctor 并检查 Gateway 日志。"
    elif health is not None and health.get("dry_run") is True:
        phase = "degraded"
        reason_code = "gateway_dry_run"
        summary = "Gateway 处于 dry-run，不能把结果视为物理机器人 readiness。"
        retryable = False
        operator_action = "如需实机执行，使用经审核的 real Profile 重新部署和启动。"
    elif not isinstance(health.get("dry_run"), bool):
        phase = "degraded"
        reason_code = "gateway_execution_mode_unknown"
        summary = "Gateway 未明确报告 dry-run/real 执行模式。"
        retryable = True
        operator_action = "升级或修复 Gateway；模式明确前不要视为实机 readiness。"
    elif health.get("dry_run") is False and not stop_evidence_providers_known:
        phase = "degraded"
        reason_code = "stop_evidence_provider_state_unknown"
        summary = "实机 Gateway 未明确报告可信停止证据 Provider 状态。"
        retryable = True
        operator_action = "检查 Gateway/Plugin 版本；确认 witness 状态前不要视为完整实机 readiness。"
    elif health.get("dry_run") is False and not real_stop_evidence_providers:
        phase = "degraded"
        reason_code = (
            "stop_evidence_provider_unavailable"
            if not stop_evidence_providers
            else "hardware_stop_evidence_provider_unqualified"
        )
        summary = (
            "实机执行模式缺少停止证据 Provider。"
            if not stop_evidence_providers
            else "现有停止证据 Provider 不具备实机硬件证明资格。"
        )
        retryable = False
        operator_action = "接入硬件 watchdog、驱动/急停状态和全执行器静止 witness 后再执行实机任务。"
    elif deployment_status == "ready":
        phase = "ready"
        reason_code = "ready"
        summary = "Gateway、Runtime 和运动资源准入均可用于接收新任务。"
        retryable = False
        operator_action = "可以提交任务；物理动作仍会逐项经过 Safety Gate。"
    else:
        phase = "degraded"
        reason_code = "readiness_unknown"
        summary = "部分状态可读取，但无法得出完整 readiness 结论。"
        retryable = True
        operator_action = "运行 fireclaw doctor 并检查详细证据。"

    evidence_id = None
    if pending_recovery is not None:
        evidence_id = _optional_text(pending_recovery.get("request_id"))
    if evidence_id is None and admission_closed:
        evidence_id = _optional_text(admission.get("task_id"))
    if evidence_id is None:
        evidence_id = _optional_text(deployment.get("fingerprint"))

    snapshot = {
        "schema_version": OPERATOR_STATUS_SCHEMA_VERSION,
        "kind": "robot_readiness",
        "robot_id": robot_id,
        "phase": phase,
        "safe_state": safe_state,
        "reason_code": reason_code,
        "retryable": retryable,
        "operator_action": operator_action,
        "evidence_id": evidence_id,
        "summary": summary,
        "components": {
            "deployment": {
                "status": deployment_status,
                "capabilities": _capability_availability(deployment),
            },
            "gateway": {
                "liveness": gateway_liveness,
                "access": gateway_access,
                "health_status": (
                    _optional_text(health.get("status")) if health else None
                ),
                "robot_online": robot_online,
                "dry_run": health.get("dry_run") if health else None,
            },
            "emergency_stop": {
                "status": (
                    "active"
                    if emergency_stop_active
                    else "clear"
                    if emergency_stop_known
                    else "unknown"
                ),
                "active": (
                    emergency_stop_value if emergency_stop_known else None
                ),
            },
            "resource_admission": {
                "status": (
                    "frozen"
                    if admission_closed
                    else "open"
                    if admission_known
                    else "unknown"
                ),
                "reason_code": _optional_text(admission.get("reason")),
                "revision": admission.get("revision"),
                "task_id": _optional_text(admission.get("task_id")),
                "pending_recovery": pending_recovery,
                "stop_evidence_provider_count": (
                    len(stop_evidence_providers)
                    if stop_evidence_providers_known
                    else None
                ),
                "qualified_real_stop_evidence_provider_count": (
                    len(real_stop_evidence_providers)
                    if stop_evidence_providers_known
                    else None
                ),
                "stop_evidence_providers": stop_evidence_providers,
            },
            "runtime_storage": {
                "status": runtime_storage_status or "unknown",
                "reason_code": _optional_text(runtime_storage.get("code")),
                "task_admission_allowed": runtime_storage.get(
                    "task_admission_allowed"
                ),
                "occurred_at": _optional_text(
                    runtime_storage.get("occurred_at")
                ),
            },
            "tasks": {
                "active_count": len(active_tasks),
                "active_task_ids": [
                    task_id
                    for item in active_tasks
                    if (task_id := _optional_text(item.get("task_id")))
                    is not None
                ],
            },
            "supervisor": supervisor,
            "managed_service": managed_service,
        },
        "evidence": {
            "profile_path": profile_path,
            "deployment": dict(deployment),
            "gateway_health": health,
            "gateway_state": state,
            "gateway_error": error,
            "supervisor_state": supervisor,
            "managed_service_state": managed_service,
        },
    }
    if fleet_doctor is not None:
        return merge_operator_status_with_doctor(snapshot, fleet_doctor)
    return snapshot


def merge_operator_status_with_doctor(
    status: Mapping[str, Any],
    doctor: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach Fleet Doctor without masking a robot-local safety blocker."""

    result = dict(status)
    components = dict(_mapping(result, "components"))
    evidence = dict(_mapping(result, "evidence"))
    raw_doctor = dict(doctor)
    doctor_components = _mapping(raw_doctor, "components")
    doctor_phase = _optional_text(raw_doctor.get("phase"))
    doctor_valid = (
        raw_doctor.get("kind") == "fleet_doctor"
        and doctor_phase in {"ready", "degraded", "blocked", "offline"}
        and _optional_text(raw_doctor.get("reason_code")) is not None
        and _optional_text(raw_doctor.get("summary")) is not None
        and _optional_text(raw_doctor.get("operator_action")) is not None
    )
    if not doctor_valid:
        doctor_phase = "degraded"
        doctor_reason = "fleet_doctor_snapshot_invalid"
        doctor_summary = "Fleet Doctor 状态快照不完整，不能把本次结果视为 READY。"
        doctor_action = "检查 Mission Gateway 与 CLI 版本后重新运行 status。"
        doctor_retryable = True
    else:
        doctor_reason = _text(
            raw_doctor.get("reason_code"),
            "fleet_doctor_unknown",
        )
        doctor_summary = _text(
            raw_doctor.get("summary"),
            "Fleet Doctor 状态未知。",
        )
        doctor_action = _text(
            raw_doctor.get("operator_action"),
            "检查 Fleet Doctor 详情。",
        )
        doctor_retryable = raw_doctor.get("retryable") is True

    components["fleet_doctor"] = {
        "phase": doctor_phase,
        "reason_code": doctor_reason,
        "summary": doctor_summary,
        "retryable": doctor_retryable,
        "operator_action": doctor_action,
        **doctor_components,
    }
    evidence["fleet_doctor"] = {
        "snapshot": raw_doctor,
        "evidence": _mapping(raw_doctor, "evidence"),
    }
    result["components"] = components
    result["evidence"] = evidence

    local_phase = _text(result.get("phase"), "unknown")
    local_safety_blocker = local_phase in {"blocked", "offline"}
    if not local_safety_blocker and doctor_phase == "blocked":
        result.update(
            phase="blocked",
            reason_code=doctor_reason,
            retryable=doctor_retryable,
            summary=doctor_summary,
            operator_action=doctor_action,
        )
    elif local_phase == "ready" and doctor_phase != "ready":
        # A missing or degraded Mission Gateway must prevent a false READY, but
        # it does not make the robot-local Gateway itself offline.
        result.update(
            phase="degraded",
            reason_code=doctor_reason,
            retryable=doctor_retryable,
            summary=doctor_summary,
            operator_action=doctor_action,
        )
    return result


def build_operator_doctor_snapshot(
    *,
    report: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project Fleet Doctor output into the same actionable envelope."""

    raw = dict(report) if report is not None else None
    failure = dict(error) if error is not None else None
    if raw is None:
        return {
            "schema_version": OPERATOR_STATUS_SCHEMA_VERSION,
            "kind": "fleet_doctor",
            "phase": "offline",
            "safe_state": "unknown",
            "reason_code": _text(
                failure.get("code") if failure else None,
                "mission_gateway_unreachable",
            ),
            "retryable": True,
            "operator_action": "确认 Mission Gateway 已启动且凭据正确，然后重试。",
            "evidence_id": None,
            "summary": "Mission Gateway 不可达，无法执行 Fleet Doctor。",
            "components": {"error": failure},
            "evidence": {"report": None, "error": failure},
        }

    raw_status = _optional_text(raw.get("status"))
    error_count = _nonnegative_int(raw.get("error_count"))
    warning_count = _nonnegative_int(raw.get("warning_count"))
    findings_value = raw.get("findings")
    report_valid = (
        raw_status in {"healthy", "unhealthy"}
        and _is_nonnegative_int(raw.get("error_count"))
        and _is_nonnegative_int(raw.get("warning_count"))
        and _is_nonnegative_int(raw.get("total_count"))
        and isinstance(findings_value, list)
    )
    if not report_valid:
        phase = "degraded"
        reason_code = "fleet_doctor_report_invalid"
        summary = "Fleet Doctor 返回了不完整或无效的诊断报告。"
        action = "检查 Mission Gateway 版本和日志；不要把该结果视为健康。"
    elif raw_status == "unhealthy" or error_count:
        phase = "blocked"
        reason_code = "fleet_doctor_errors"
        summary = (
            f"Fleet Doctor 发现 {error_count} 个错误。"
            if error_count
            else "Fleet Doctor 报告 fleet unhealthy。"
        )
        action = "先处理 error 级 findings，再提交新的物理任务。"
    elif warning_count:
        phase = "degraded"
        reason_code = "fleet_doctor_warnings"
        summary = f"Fleet Doctor 未发现错误，但有 {warning_count} 个警告。"
        action = "核对 warning 级 findings；确认不影响当前任务后再继续。"
    else:
        phase = "ready"
        reason_code = "fleet_healthy"
        summary = "Fleet Doctor 未发现错误或警告。"
        action = "无需诊断操作。"
    return {
        "schema_version": OPERATOR_STATUS_SCHEMA_VERSION,
        "kind": "fleet_doctor",
        "phase": phase,
        "safe_state": "unknown",
        "reason_code": reason_code,
        "retryable": phase != "ready",
        "operator_action": action,
        "evidence_id": None,
        "summary": summary,
        "components": {
            "error_count": error_count,
            "warning_count": warning_count,
            "total_count": _nonnegative_int(raw.get("total_count")),
            "findings": _list_of_mappings(raw.get("findings")),
        },
        "evidence": {"report": raw, "error": None},
    }


def build_operator_recovery_snapshot(
    result: Mapping[str, Any],
    *,
    verified_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a recovery request or decision without treating it as task resume."""

    raw = dict(result)
    status = _text(raw.get("status"), "error")
    verification = dict(verified_state) if verified_state is not None else None
    verified_admission = _mapping(verification, "admission")
    verified_open = bool(verified_admission) and verified_admission.get("closed") is False

    if status == "recovered" and verified_open:
        phase = "ready"
        safe_state = "motion_admitted_idle"
        reason_code = "resource_admission_recovered"
        retryable = False
        summary = "运动资源准入已恢复，未来任务可以重新申请运动资源。"
        action = "旧任务不会恢复；如需运动，请提交一个新任务。"
    elif status == "recovered":
        phase = "blocked"
        safe_state = "unknown"
        reason_code = "recovery_verification_failed"
        retryable = True
        summary = "Gateway 报告已恢复，但后置读取未能确认准入已打开。"
        action = "不要提交运动任务；重新运行 status 并检查审计记录。"
    elif status == "pending_confirmation":
        phase = "blocked"
        safe_state = "motion_blocked"
        reason_code = "operator_confirmation_required"
        retryable = True
        summary = "已取得现场停止证据，仍需操作员提交本次专属确认短语。"
        action = "核对冻结来源、停止证据和过期时间后，再提交完整确认短语。"
    elif status == "not_frozen":
        phase = "ready"
        safe_state = "motion_admitted_idle"
        reason_code = "resource_admission_not_frozen"
        retryable = False
        summary = "运动资源准入当前没有冻结，无需恢复。"
        action = "无需执行恢复操作。"
    elif status == "confirmation_not_submitted":
        phase = "blocked"
        safe_state = "motion_blocked"
        reason_code = "operator_confirmation_not_submitted"
        retryable = True
        summary = "确认短语未提交，资源准入继续保持冻结。"
        action = "在请求过期前核对并提交完整短语，或重新发起恢复请求。"
    elif status == "configuration_required":
        phase = "blocked"
        safe_state = "unknown"
        reason_code = _text(
            raw.get("error_code"),
            "recovery_configuration_required",
        )
        retryable = True
        summary = _text(raw.get("message"), "尚未选择可验证的 Robot Profile。")
        action = _text(raw.get("operator_action"), "先运行 fireclaw setup。")
    else:
        phase = "blocked"
        safe_state = "motion_blocked"
        reason_code = _text(
            raw.get("error_code"),
            f"resource_admission_recovery_{status}",
        )
        retryable = status in {"blocked", "expired", "error"}
        summary = _text(raw.get("message"), "资源准入恢复未完成，继续保持冻结。")
        action = (
            "重新采集现场停止证据并发起新请求。"
            if status == "expired"
            else "根据阻塞项修复现场或 Runtime 状态后重试。"
        )

    return {
        "schema_version": OPERATOR_STATUS_SCHEMA_VERSION,
        "kind": "resource_admission_recovery",
        "robot_id": _optional_text(raw.get("robot_id")),
        "phase": phase,
        "safe_state": safe_state,
        "reason_code": reason_code,
        "retryable": retryable,
        "operator_action": action,
        "evidence_id": _optional_text(raw.get("request_id")),
        "summary": summary,
        "components": {
            "status": status,
            "request_id": _optional_text(raw.get("request_id")),
            "expires_at": _optional_text(raw.get("expires_at")),
            "confirmation_phrase": _optional_text(
                raw.get("confirmation_phrase")
            ),
            "frozen_admission": _mapping_or_none(
                raw.get("frozen_admission")
            ),
            "stop_evidence": _mapping_or_none(raw.get("stop_evidence")),
            "verified_admission": (
                dict(verified_admission) if verified_admission else None
            ),
        },
        "evidence": {"result": raw, "verified_state": verification},
    }


def format_operator_status(snapshot: Mapping[str, Any]) -> str:
    """Render a concise human status while JSON retains all raw evidence."""

    components = _mapping(snapshot, "components")
    deployment = _mapping(components, "deployment")
    gateway = _mapping(components, "gateway")
    admission = _mapping(components, "resource_admission")
    emergency_stop = _mapping(components, "emergency_stop")
    tasks = _mapping(components, "tasks")
    supervisor = _mapping(components, "supervisor")
    managed_service = _mapping(components, "managed_service")
    fleet_doctor = _mapping(components, "fleet_doctor")
    lines = [
        f"FireClaw 状态：{_text(snapshot.get('phase'), 'unknown').upper()}",
        f"机器人：{_text(snapshot.get('robot_id'), 'unknown')}",
        f"摘要：{_text(snapshot.get('summary'), '状态未知')}",
        f"安全状态：{_safe_state_text(snapshot.get('safe_state'))}",
        f"部署：{_text(deployment.get('status'), 'unknown')}",
        "Gateway："
        f"{_text(gateway.get('liveness'), 'unknown')} / "
        f"{_text(gateway.get('access'), 'unknown')}",
        "Robot Adapter："
        + (
            "online"
            if gateway.get("robot_online") is True
            else "offline"
            if gateway.get("robot_online") is False
            else "unknown"
        ),
        "Gateway 执行模式："
        + (
            "dry-run"
            if gateway.get("dry_run") is True
            else "real"
            if gateway.get("dry_run") is False
            else "unknown"
        ),
        f"急停：{_text(emergency_stop.get('status'), 'unknown')}",
        "运动资源准入："
        f"{_text(admission.get('status'), 'unknown')}"
        + (
            f"（{admission['reason_code']}）"
            if admission.get("reason_code")
            else ""
        ),
        "停止证据 Provider："
        + (
            str(admission["stop_evidence_provider_count"])
            if isinstance(admission.get("stop_evidence_provider_count"), int)
            else "unknown"
        ),
        f"Gateway 活动任务：{_nonnegative_int(tasks.get('active_count'))}",
    ]
    if fleet_doctor:
        doctor_counts: list[str] = []
        for key, label in (
            ("error_count", "errors"),
            ("warning_count", "warnings"),
        ):
            value = fleet_doctor.get(key)
            if _is_nonnegative_int(value):
                doctor_counts.append(f"{label}={value}")
        count_suffix = (
            f"（{', '.join(doctor_counts)}）" if doctor_counts else ""
        )
        lines.append(
            "Fleet Doctor："
            f"{_text(fleet_doctor.get('phase'), 'unknown')}"
            f"{count_suffix}"
        )
        findings = fleet_doctor.get("findings")
        if isinstance(findings, list):
            for finding in findings:
                if not isinstance(finding, Mapping):
                    continue
                severity = _text(finding.get("severity"), "info").upper()
                robot = _optional_text(finding.get("robot_id"))
                prefix = f"  - [{severity}]"
                if robot:
                    prefix += f" {robot}"
                lines.append(
                    f"{prefix}：{_text(finding.get('message'), '无说明')}"
                )
    if supervisor:
        lines.append(
            "Supervisor 最近状态："
            f"{_text(supervisor.get('status'), 'unknown')}"
            + (
                f"（{supervisor['reason_code']}）"
                if supervisor.get("reason_code")
                else ""
            )
        )
        if supervisor.get("log_dir"):
            lines.append(f"Supervisor 日志：{supervisor['log_dir']}")
    if managed_service:
        lines.append(
            "systemd 用户服务："
            f"{_text(managed_service.get('status'), 'unknown')}"
            + (
                f"（{managed_service['unit_name']}）"
                if managed_service.get("unit_name")
                else ""
            )
        )
    capabilities = deployment.get("capabilities")
    if isinstance(capabilities, list) and capabilities:
        lines.append("能力 readiness：")
        for item in capabilities:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"  - {_text(item.get('plugin_id'), 'unknown')}: "
                f"{_text(item.get('status'), 'unknown')}"
            )
    lines.append(f"下一步：{_text(snapshot.get('operator_action'), '运行 doctor。')}")
    if snapshot.get("evidence_id"):
        lines.append(f"证据 ID：{snapshot['evidence_id']}")
    return "\n".join(lines)


def format_operator_doctor(snapshot: Mapping[str, Any]) -> str:
    components = _mapping(snapshot, "components")
    lines = [
        f"FireClaw Doctor：{_text(snapshot.get('phase'), 'unknown').upper()}",
        f"摘要：{_text(snapshot.get('summary'), '诊断状态未知')}",
    ]
    findings = components.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            severity = _text(finding.get("severity"), "info").upper()
            robot = _optional_text(finding.get("robot_id"))
            prefix = f"[{severity}]"
            if robot:
                prefix += f" {robot}"
            lines.append(f"{prefix}：{_text(finding.get('message'), '无说明')}")
    lines.append(f"下一步：{_text(snapshot.get('operator_action'), '检查诊断详情。')}")
    return "\n".join(lines)


def format_operator_recovery(snapshot: Mapping[str, Any]) -> str:
    components = _mapping(snapshot, "components")
    lines = [
        f"冻结恢复：{_text(snapshot.get('phase'), 'unknown').upper()}",
        f"摘要：{_text(snapshot.get('summary'), '恢复状态未知')}",
        f"安全状态：{_safe_state_text(snapshot.get('safe_state'))}",
    ]
    if components.get("request_id"):
        lines.append(f"请求 ID：{components['request_id']}")
    if components.get("expires_at"):
        lines.append(f"过期时间：{components['expires_at']}")
    evidence = components.get("stop_evidence")
    if isinstance(evidence, Mapping):
        lines.append(
            "现场停止证据："
            f"{_text(evidence.get('status'), 'unknown')}"
        )
        blockers = evidence.get("blockers")
        if isinstance(blockers, list) and blockers:
            lines.append("阻塞项：" + "、".join(str(item) for item in blockers))
    if components.get("confirmation_phrase"):
        lines.append(f"完整确认短语：{components['confirmation_phrase']}")
    lines.append(f"下一步：{_text(snapshot.get('operator_action'), '保持冻结。')}")
    return "\n".join(lines)


def _capability_availability(
    deployment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    runtime = _mapping(deployment, "runtime_checks")
    checks = _list_of_mappings(runtime.get("checks"))
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for check in checks:
        grouped[_text(check.get("plugin_id"), "unknown")].append(check)
    result: list[dict[str, Any]] = []
    for plugin_id, items in sorted(grouped.items()):
        failed = [item for item in items if item.get("ok") is not True]
        result.append(
            {
                "plugin_id": plugin_id,
                "status": "ready" if not failed else "unavailable",
                "check_count": len(items),
                "failed_checks": [
                    {
                        "kind": item.get("kind"),
                        "target": item.get("target"),
                        "detail": item.get("detail"),
                    }
                    for item in failed
                ],
            }
        )
    return result


def _safe_state_text(value: Any) -> str:
    return {
        "motion_blocked": "运动资源已阻断",
        "motion_active_or_pending": "存在活动或待完成任务，不能推断现场静止",
        "motion_admitted_idle": "当前无 Gateway 活动任务且准入开放；这不等于现场静止证明",
        "unknown": "未知；不得据此推断机器人已停止",
    }.get(_text(value, "unknown"), "未知；不得据此推断机器人已停止")


def _mapping(value: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    item = value.get(key)
    return dict(item) if isinstance(item, Mapping) else {}


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _text(value: Any, default: str) -> str:
    return _optional_text(value) or default


def _nonnegative_int(value: Any) -> int:
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        else 0
    )


def _is_nonnegative_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


__all__ = [
    "OPERATOR_STATUS_SCHEMA_VERSION",
    "build_operator_doctor_snapshot",
    "build_operator_recovery_snapshot",
    "build_operator_status_snapshot",
    "format_operator_doctor",
    "format_operator_recovery",
    "format_operator_status",
]
