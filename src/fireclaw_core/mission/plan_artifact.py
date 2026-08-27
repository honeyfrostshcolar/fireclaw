"""Server-owned sealed mission plans and one-use confirmation tokens."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask


PLAN_ARTIFACT_SCHEMA_VERSION = 1
PLAN_TOKEN_VERSION = "pa1"
DEFAULT_PLAN_TTL_SECONDS = 120.0
MAX_PLAN_TTL_SECONDS = 3600.0
MAX_PLAN_ARTIFACT_RECORDS = 4096
PLAN_ARTIFACT_STATUSES = frozenset(
    {"pending", "consumed", "expired", "invalidated", "execution_failed"}
)
_SHA256_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TOKEN_RE = re.compile(r"^pa1\.([0-9a-f]{32})\.([A-Za-z0-9_-]{43})$")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_COMMAND_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_COMMAND_COORDINATE_RE = re.compile(
    rf"[（(]\s*(?:x\s*(?:=|:|：|为)\s*)?({_COMMAND_NUMBER_PATTERN})\s*[,，\s]\s*(?:y\s*(?:=|:|：|为)\s*)?({_COMMAND_NUMBER_PATTERN})\s*[)）]",
    re.IGNORECASE,
)
_COMMAND_EXPLICIT_AXIS_RE = re.compile(
    rf"(?:x|横坐标)\s*(?:=|:|：|为)\s*({_COMMAND_NUMBER_PATTERN})[,\s，;；]+(?:y|纵坐标)\s*(?:=|:|：|为)\s*({_COMMAND_NUMBER_PATTERN})",
    re.IGNORECASE,
)
_COMMAND_YAW_RE = re.compile(
    rf"(?:\byaw\b|朝向)\s*(?:为|=|:|：)?\s*({_COMMAND_NUMBER_PATTERN})",
    re.IGNORECASE,
)


class PlanArtifactError(RuntimeError):
    """A fail-closed artifact error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReadinessBinding:
    revision: str
    snapshot_json: str

    def snapshot(self) -> dict[str, Any]:
        value = json.loads(self.snapshot_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise PlanArtifactError("readiness_binding_corrupt", "Readiness binding is corrupt.")
        return value


@dataclass(frozen=True)
class PlanArtifactRecord:
    artifact_id: str
    token_sha256: str
    plan_digest: str
    binding_digest: str
    plan_json: str
    operator_id: str
    session_id: str
    robot_ids: tuple[str, ...]
    runtime_epoch: str
    runtime_mode: str
    profile_revision: str
    readiness_revision: str
    risk_level: str
    approvals_json: str
    created_at: str
    expires_at: str
    expires_at_epoch: float
    status: str = "pending"
    status_version: int = 1
    terminal_reason: str | None = None
    consumed_at: str | None = None
    consumed_by: str | None = None
    mission_id: str | None = None
    execution_status: str | None = None

    def plan_payload(self) -> dict[str, Any]:
        value = json.loads(self.plan_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise PlanArtifactError("plan_artifact_corrupt", "Sealed plan payload is corrupt.")
        return value

    def plan(self) -> MissionPlan:
        return mission_plan_from_dict(self.plan_payload())

    def required_approvals(self) -> list[dict[str, Any]]:
        value = json.loads(self.approvals_json)
        if not isinstance(value, list):  # pragma: no cover - constructor invariant
            raise PlanArtifactError("plan_artifact_corrupt", "Approval binding is corrupt.")
        return [dict(item) for item in value if isinstance(item, dict)]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PLAN_ARTIFACT_SCHEMA_VERSION,
            "kind": "sealed_plan_artifact",
            "artifact_id": self.artifact_id,
            "status": self.status,
            "status_version": self.status_version,
            "plan_digest": self.plan_digest,
            "binding_digest": self.binding_digest,
            "plan": self.plan_payload(),
            "operator_id": self.operator_id,
            "session_id": self.session_id,
            "robot_ids": list(self.robot_ids),
            "runtime_mode": self.runtime_mode,
            "profile_revision": self.profile_revision,
            "readiness_revision": self.readiness_revision,
            "risk_level": self.risk_level,
            "required_approvals": self.required_approvals(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "terminal_reason": self.terminal_reason,
            "consumed_at": self.consumed_at,
            "consumed_by": self.consumed_by,
            "mission_id": self.mission_id,
            "execution_status": self.execution_status,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            **self.to_public_dict(),
            "token_sha256": self.token_sha256,
            "runtime_epoch": self.runtime_epoch,
            "expires_at_epoch": self.expires_at_epoch,
        }

    @classmethod
    def from_storage_dict(cls, raw: Mapping[str, Any]) -> "PlanArtifactRecord":
        try:
            record = cls(
                artifact_id=_required_string(raw, "artifact_id"),
                token_sha256=_required_string(raw, "token_sha256"),
                plan_digest=_required_string(raw, "plan_digest"),
                binding_digest=_required_string(raw, "binding_digest"),
                plan_json=_canonical_json(raw["plan"]),
                operator_id=_required_string(raw, "operator_id"),
                session_id=_required_string(raw, "session_id"),
                robot_ids=tuple(_required_string_list(raw, "robot_ids")),
                runtime_epoch=_required_string(raw, "runtime_epoch"),
                runtime_mode=_required_string(raw, "runtime_mode"),
                profile_revision=_required_string(raw, "profile_revision"),
                readiness_revision=_required_string(raw, "readiness_revision"),
                risk_level=_required_string(raw, "risk_level"),
                approvals_json=_canonical_json(raw["required_approvals"]),
                created_at=_required_string(raw, "created_at"),
                expires_at=_required_string(raw, "expires_at"),
                expires_at_epoch=_required_number(raw, "expires_at_epoch"),
                status=_required_string(raw, "status"),
                status_version=_required_int(raw, "status_version"),
                terminal_reason=_optional_storage_string(raw.get("terminal_reason")),
                consumed_at=_optional_storage_string(raw.get("consumed_at")),
                consumed_by=_optional_storage_string(raw.get("consumed_by")),
                mission_id=_optional_storage_string(raw.get("mission_id")),
                execution_status=_optional_storage_string(raw.get("execution_status")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanArtifactError(
                "plan_artifact_store_corrupt",
                "Plan artifact store contains an invalid record.",
            ) from exc
        _validate_record(record)
        return record


@dataclass(frozen=True)
class IssuedPlanArtifact:
    record: PlanArtifactRecord
    token: str

    def to_public_dict(self) -> dict[str, Any]:
        return {**self.record.to_public_dict(), "plan_token": self.token}


class PlanArtifactStore:
    """Append-only artifact state with atomic process-local one-use consumption."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        max_records: int = MAX_PLAN_ARTIFACT_RECORDS,
    ) -> None:
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        self.path = Path(path).expanduser() if path is not None else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_records = max_records
        self._lock = threading.RLock()
        self._records: dict[str, PlanArtifactRecord] = {}
        if self.path is not None:
            self._load()

    def issue(
        self,
        plan: MissionPlan,
        *,
        operator_id: str,
        session_id: str,
        robot_ids: Sequence[str],
        runtime_epoch: str,
        runtime_mode: str,
        profile_revision: str,
        readiness_revision: str,
        risk_level: str,
        required_approvals: Sequence[Mapping[str, Any]],
        ttl_seconds: float = DEFAULT_PLAN_TTL_SECONDS,
    ) -> IssuedPlanArtifact:
        normalized_ttl = float(ttl_seconds)
        if not 1.0 <= normalized_ttl <= MAX_PLAN_TTL_SECONDS:
            raise PlanArtifactError(
                "plan_ttl_invalid",
                f"Plan TTL must be between 1 and {int(MAX_PLAN_TTL_SECONDS)} seconds.",
            )
        normalized_robots = _normalize_robot_ids(robot_ids)
        if runtime_mode not in {"simulation", "real"}:
            raise PlanArtifactError("runtime_identity_unknown", "Runtime mode is not authoritative.")
        if not _SHA256_REVISION_RE.fullmatch(profile_revision):
            raise PlanArtifactError("profile_revision_unknown", "Profile revision is not authoritative.")
        if not _SHA256_REVISION_RE.fullmatch(readiness_revision):
            raise PlanArtifactError("readiness_revision_invalid", "Readiness revision is invalid.")
        if risk_level not in _RISK_ORDER:
            raise PlanArtifactError("plan_risk_invalid", "Plan risk level is invalid.")
        normalized_operator = _nonempty(operator_id, "operator_id")
        normalized_session = _nonempty(session_id, "session_id")
        normalized_epoch = _nonempty(runtime_epoch, "runtime_epoch")
        plan_payload = plan.to_dict()
        # Strict round-trip rejects unsupported planner output before sealing.
        mission_plan_from_dict(plan_payload)
        plan_json = _canonical_json(plan_payload)
        plan_digest = "sha256:" + _sha256_text(plan_json)
        approvals = [dict(item) for item in required_approvals]
        approvals_json = _canonical_json(approvals)
        artifact_id = uuid4().hex
        now = _aware_utc(self._clock())
        expires_at = now + timedelta(seconds=normalized_ttl)
        binding_payload = {
            "schema_version": PLAN_ARTIFACT_SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "plan_digest": plan_digest,
            "operator_id": normalized_operator,
            "session_id": normalized_session,
            "robot_ids": list(normalized_robots),
            "runtime_epoch": normalized_epoch,
            "runtime_mode": runtime_mode,
            "profile_revision": profile_revision,
            "readiness_revision": readiness_revision,
            "risk_level": risk_level,
            "required_approvals": approvals,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        binding_digest = "sha256:" + _sha256_text(_canonical_json(binding_payload))
        token = f"{PLAN_TOKEN_VERSION}.{artifact_id}.{secrets.token_urlsafe(32)}"
        record = PlanArtifactRecord(
            artifact_id=artifact_id,
            token_sha256=_sha256_text(token),
            plan_digest=plan_digest,
            binding_digest=binding_digest,
            plan_json=plan_json,
            operator_id=normalized_operator,
            session_id=normalized_session,
            robot_ids=normalized_robots,
            runtime_epoch=normalized_epoch,
            runtime_mode=runtime_mode,
            profile_revision=profile_revision,
            readiness_revision=readiness_revision,
            risk_level=risk_level,
            approvals_json=approvals_json,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            expires_at_epoch=expires_at.timestamp(),
        )
        with self._lock:
            if len(self._records) >= self._max_records:
                self._prune_terminal_locked()
            if len(self._records) >= self._max_records:
                raise PlanArtifactError(
                    "plan_artifact_capacity_exhausted",
                    "Plan artifact capacity is exhausted; wait for pending previews to expire.",
                )
            self._append_locked(record)
            self._records[artifact_id] = record
        return IssuedPlanArtifact(record=record, token=token)

    def validate_pending(
        self,
        token: str,
        *,
        artifact_id: str,
        plan_digest: str,
        status_version: int,
        operator_id: str,
        session_id: str,
        robot_ids: Sequence[str],
        runtime_epoch: str,
    ) -> PlanArtifactRecord:
        with self._lock:
            record = self._record_for_token_locked(token, artifact_id)
            record = self._expire_if_needed_locked(record)
            self._require_pending(record)
            self._validate_expected_bindings(
                record,
                plan_digest=plan_digest,
                status_version=status_version,
                operator_id=operator_id,
                session_id=session_id,
                robot_ids=robot_ids,
                runtime_epoch=runtime_epoch,
            )
            return record

    def consume(
        self,
        token: str,
        *,
        artifact_id: str,
        plan_digest: str,
        status_version: int,
        operator_id: str,
        session_id: str,
        robot_ids: Sequence[str],
        runtime_epoch: str,
        mission_id: str,
    ) -> PlanArtifactRecord:
        with self._lock:
            record = self._record_for_token_locked(token, artifact_id)
            record = self._expire_if_needed_locked(record)
            self._require_pending(record)
            self._validate_expected_bindings(
                record,
                plan_digest=plan_digest,
                status_version=status_version,
                operator_id=operator_id,
                session_id=session_id,
                robot_ids=robot_ids,
                runtime_epoch=runtime_epoch,
            )
            now = _aware_utc(self._clock()).isoformat()
            consumed = replace(
                record,
                status="consumed",
                status_version=record.status_version + 1,
                terminal_reason="operator_confirmed_once",
                consumed_at=now,
                consumed_by=operator_id,
                mission_id=_nonempty(mission_id, "mission_id"),
                execution_status="queued",
            )
            self._append_locked(consumed)
            self._records[record.artifact_id] = consumed
            return consumed

    def invalidate_pending(self, artifact_id: str, *, reason: str) -> PlanArtifactRecord:
        with self._lock:
            record = self._records.get(artifact_id)
            if record is None:
                raise PlanArtifactError("plan_token_invalid", "Plan artifact does not exist.")
            self._require_pending(record)
            invalidated = replace(
                record,
                status="invalidated",
                status_version=record.status_version + 1,
                terminal_reason=_nonempty(reason, "reason"),
            )
            self._append_locked(invalidated)
            self._records[artifact_id] = invalidated
            return invalidated

    def record_execution(
        self,
        artifact_id: str,
        *,
        execution_status: str,
        failed: bool = False,
        reason: str | None = None,
    ) -> PlanArtifactRecord:
        with self._lock:
            record = self._records.get(artifact_id)
            if record is None:
                raise PlanArtifactError("plan_token_invalid", "Plan artifact does not exist.")
            if record.status not in {"consumed", "execution_failed"}:
                raise PlanArtifactError(
                    "plan_artifact_state_conflict",
                    "Only a consumed plan can receive an execution result.",
                )
            updated = replace(
                record,
                status="execution_failed" if failed else "consumed",
                status_version=record.status_version + 1,
                terminal_reason=(reason or record.terminal_reason),
                execution_status=_nonempty(execution_status, "execution_status"),
            )
            self._append_locked(updated)
            self._records[artifact_id] = updated
            return updated

    def get(self, artifact_id: str) -> PlanArtifactRecord | None:
        with self._lock:
            return self._records.get(artifact_id)

    def _record_for_token_locked(self, token: str, artifact_id: str) -> PlanArtifactRecord:
        if not isinstance(token, str):
            raise PlanArtifactError("plan_token_invalid", "Plan token is invalid.")
        match = _TOKEN_RE.fullmatch(token)
        if match is None or match.group(1) != artifact_id or not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise PlanArtifactError("plan_token_invalid", "Plan token is invalid.")
        record = self._records.get(artifact_id)
        if record is None or not hmac.compare_digest(record.token_sha256, _sha256_text(token)):
            raise PlanArtifactError("plan_token_invalid", "Plan token is invalid.")
        return record

    def _expire_if_needed_locked(self, record: PlanArtifactRecord) -> PlanArtifactRecord:
        if (
            record.status != "pending"
            or _aware_utc(self._clock()).timestamp() < record.expires_at_epoch
        ):
            return record
        expired = replace(
            record,
            status="expired",
            status_version=record.status_version + 1,
            terminal_reason="ttl_expired",
        )
        self._append_locked(expired)
        self._records[record.artifact_id] = expired
        return expired

    @staticmethod
    def _require_pending(record: PlanArtifactRecord) -> None:
        if record.status == "expired":
            raise PlanArtifactError("plan_token_expired", "Plan token has expired.")
        if record.status in {"consumed", "execution_failed"}:
            raise PlanArtifactError("plan_token_replayed", "Plan token was already consumed.")
        if record.status == "invalidated":
            raise PlanArtifactError(
                "plan_artifact_invalidated",
                "Plan artifact was invalidated and cannot be executed.",
            )
        if record.status != "pending":
            raise PlanArtifactError("plan_artifact_state_conflict", "Plan artifact is not pending.")

    @staticmethod
    def _validate_expected_bindings(
        record: PlanArtifactRecord,
        *,
        plan_digest: str,
        status_version: int,
        operator_id: str,
        session_id: str,
        robot_ids: Sequence[str],
        runtime_epoch: str,
    ) -> None:
        if not hmac.compare_digest(record.plan_digest, str(plan_digest)):
            raise PlanArtifactError("plan_digest_mismatch", "Confirmed plan digest does not match.")
        if status_version != record.status_version:
            raise PlanArtifactError("plan_status_version_mismatch", "Plan status version changed.")
        if record.operator_id != operator_id:
            raise PlanArtifactError("plan_operator_mismatch", "Plan belongs to another operator.")
        if record.session_id != session_id:
            raise PlanArtifactError("plan_session_mismatch", "Plan belongs to another session.")
        if record.robot_ids != _normalize_robot_ids(robot_ids):
            raise PlanArtifactError("plan_robot_mismatch", "Confirmed robot binding does not match.")
        if record.runtime_epoch != runtime_epoch:
            raise PlanArtifactError("plan_runtime_drift", "Gateway runtime changed after preview.")

    def _append_locked(self, record: PlanArtifactRecord) -> None:
        if self.path is None:
            return
        if self.path.is_symlink():
            raise PlanArtifactError(
                "plan_artifact_store_unsafe",
                "Plan artifact store must not be a symbolic link.",
            )
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        line = _canonical_json(record.to_storage_dict()) + "\n"
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                self.path.chmod(0o600)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise PlanArtifactError(
                "plan_artifact_store_unavailable",
                "Plan artifact state could not be persisted; execution was not authorized.",
            ) from exc

    def _load(self) -> None:
        assert self.path is not None
        if self.path.is_symlink():
            raise PlanArtifactError(
                "plan_artifact_store_unsafe",
                "Plan artifact store must not be a symbolic link.",
            )
        if not self.path.exists():
            return
        if not self.path.is_file():
            raise PlanArtifactError(
                "plan_artifact_store_unsafe",
                "Plan artifact store path is not a regular file.",
            )
        latest: dict[str, PlanArtifactRecord] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise PlanArtifactError(
                "plan_artifact_store_unavailable",
                "Plan artifact store could not be read.",
            ) from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise PlanArtifactError(
                    "plan_artifact_store_corrupt",
                    f"Plan artifact store has an empty record at line {line_number}.",
                )
            try:
                raw = json.loads(line, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError) as exc:
                raise PlanArtifactError(
                    "plan_artifact_store_corrupt",
                    f"Plan artifact store is corrupt at line {line_number}.",
                ) from exc
            if not isinstance(raw, dict):
                raise PlanArtifactError(
                    "plan_artifact_store_corrupt",
                    f"Plan artifact store record {line_number} is not an object.",
                )
            if (
                raw.get("schema_version") != PLAN_ARTIFACT_SCHEMA_VERSION
                or raw.get("kind") != "sealed_plan_artifact"
            ):
                raise PlanArtifactError(
                    "plan_artifact_store_corrupt",
                    f"Plan artifact store record {line_number} has an unsupported schema.",
                )
            record = PlanArtifactRecord.from_storage_dict(raw)
            previous = latest.get(record.artifact_id)
            if previous is not None and record.status_version != previous.status_version + 1:
                raise PlanArtifactError(
                    "plan_artifact_store_corrupt",
                    f"Plan artifact {record.artifact_id} has a non-monotonic status version.",
                )
            latest[record.artifact_id] = record
        if len(latest) > self._max_records:
            raise PlanArtifactError(
                "plan_artifact_store_capacity_exceeded",
                "Plan artifact store exceeds its configured record capacity.",
            )
        self._records = latest

    def _prune_terminal_locked(self) -> None:
        terminal = sorted(
            (record for record in self._records.values() if record.status != "pending"),
            key=lambda item: item.created_at,
        )
        while terminal and len(self._records) >= self._max_records:
            record = terminal.pop(0)
            self._records.pop(record.artifact_id, None)


def build_readiness_binding(
    snapshot: Mapping[str, Any],
    *,
    robot_ids: Sequence[str],
) -> ReadinessBinding:
    """Build a semantic revision without volatile timestamps or heartbeat IDs."""

    normalized_robots = _normalize_robot_ids(robot_ids)
    observations = snapshot.get("observations")
    robot_readiness = snapshot.get("robot_readiness")
    if not isinstance(observations, Mapping) or not isinstance(robot_readiness, list):
        raise PlanArtifactError(
            "readiness_unavailable",
            "Gateway readiness evidence is unavailable.",
        )
    bound_observations: dict[str, Any] = {}
    for key in ("admission_phase", "admission_safe_state", "reason_code"):
        bound_observations[key] = _semantic_evidence(observations.get(key), label=key)
    by_robot: dict[str, Mapping[str, Any]] = {}
    for item in robot_readiness:
        if isinstance(item, Mapping) and isinstance(item.get("robot_id"), str):
            by_robot[str(item["robot_id"])] = item
    bound_robots: list[dict[str, Any]] = []
    for robot_id in normalized_robots:
        item = by_robot.get(robot_id)
        if item is None:
            raise PlanArtifactError(
                "robot_readiness_unavailable",
                f"Readiness evidence is unavailable for robot {robot_id}.",
            )
        semantic = _semantic_evidence(item.get("readiness"), label=f"robot:{robot_id}")
        value = semantic.get("value")
        if not isinstance(value, Mapping):
            raise PlanArtifactError(
                "robot_readiness_unavailable",
                f"Readiness value is invalid for robot {robot_id}.",
            )
        sanitized = {
            "status": str(value.get("status") or "unknown"),
            "declared_capabilities": sorted(
                str(cap) for cap in value.get("declared_capabilities", [])
            ),
        }
        bound_robots.append({
            "robot_id": robot_id,
            "source": semantic.get("source"),
            "freshness": semantic.get("freshness"),
            "value": sanitized,
        })
    payload = {
        "schema_version": 1,
        "observations": bound_observations,
        "robots": bound_robots,
    }
    snapshot_json = _canonical_json(payload)
    return ReadinessBinding(
        revision="sha256:" + _sha256_text(snapshot_json),
        snapshot_json=snapshot_json,
    )


def require_dispatchable_readiness(
    binding: ReadinessBinding,
    *,
    require_admission: bool = True,
) -> None:
    snapshot = binding.snapshot()
    observations = snapshot.get("observations")
    admission = (
        observations.get("admission_phase")
        if isinstance(observations, dict)
        else None
    )
    reason = (
        observations.get("reason_code", {}).get("value")
        if isinstance(observations, dict)
        and isinstance(observations.get("reason_code"), dict)
        else None
    )
    admission_value = admission.get("value") if isinstance(admission, dict) else None
    admission_is_dispatchable = admission_value == "ready" or (
        admission_value == "degraded" and reason == "fleet_doctor_warnings"
    )
    if require_admission and (
        not isinstance(admission, dict)
        or admission.get("freshness") != "fresh"
        or not admission_is_dispatchable
    ):
        suffix = f" Reason: {reason}." if isinstance(reason, str) and reason else ""
        raise PlanArtifactError(
            "admission_not_ready",
            "Fleet admission lacks fresh dispatchable evidence." + suffix,
        )
    for robot in snapshot.get("robots", []):
        value = robot.get("value") if isinstance(robot, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("status") != "online"
            or robot.get("freshness") != "fresh"
        ):
            robot_id = robot.get("robot_id", "unknown") if isinstance(robot, dict) else "unknown"
            raise PlanArtifactError(
                "robot_not_ready",
                f"Robot {robot_id} lacks fresh online readiness evidence.",
            )


def assess_plan_risk(plan: MissionPlan, *, runtime_mode: str) -> str:
    risk = "low"
    high_capabilities = {"firefight", "fire_suppression", "victim_rescue", "manipulation"}
    medium_capabilities = {
        "victim_search",
        "hazard_survey",
        "recon",
        "navigation",
        "move_base",
        "patrol",
        "transport",
    }
    for subtask in plan.subtasks:
        declared = subtask.completion_contract.get("risk_level")
        if isinstance(declared, str) and declared in _RISK_ORDER:
            risk = _max_risk(risk, declared)
        if subtask.capability_required in high_capabilities:
            risk = _max_risk(risk, "high")
        elif subtask.capability_required in medium_capabilities:
            risk = _max_risk(risk, "medium")
    if re.search(r"灭火|扑灭|压制火势|救人|营救", plan.command):
        risk = _max_risk(risk, "high")
    if runtime_mode == "real":
        risk = _max_risk(risk, "high")
    return risk


def required_plan_approvals(risk_level: str, *, runtime_mode: str) -> list[dict[str, Any]]:
    approvals: list[dict[str, Any]] = [
        {
            "kind": "operator_plan_confirmation",
            "scope": "plan_once",
            "status": "required",
        }
    ]
    if risk_level in {"high", "critical"}:
        approvals.append(
            {
                "kind": "runtime_safety_gate",
                "scope": "each_physical_tool",
                "status": "required_at_dispatch",
            }
        )
    if runtime_mode == "real":
        approvals.append(
            {
                "kind": "real_stop_recovery_contract",
                "scope": "mission",
                "status": "unavailable_until_stage_5",
            }
        )
    return approvals


def mission_plan_from_dict(raw: Mapping[str, Any]) -> MissionPlan:
    if not isinstance(raw, Mapping):
        raise PlanArtifactError("plan_payload_invalid", "Sealed plan must be an object.")
    allowed_plan_keys = {"intent", "command", "execution_groups", "subtasks", "knowledge_refs"}
    if set(raw) - allowed_plan_keys:
        raise PlanArtifactError("plan_payload_invalid", "Sealed plan contains unknown fields.")
    intent = _required_string(raw, "intent")
    command = _required_string(raw, "command")
    raw_subtasks = raw.get("subtasks")
    if not isinstance(raw_subtasks, list) or not raw_subtasks or len(raw_subtasks) > 256:
        raise PlanArtifactError("plan_payload_invalid", "Sealed plan subtasks are invalid.")
    subtasks = [_mission_subtask_from_dict(item) for item in raw_subtasks]
    knowledge_refs = _required_string_list(raw, "knowledge_refs")
    plan = MissionPlan(
        intent=intent,
        command=command,
        subtasks=subtasks,
        knowledge_refs=knowledge_refs,
    )
    execution_groups = raw.get("execution_groups")
    if execution_groups is not None and (
        not isinstance(execution_groups, int)
        or isinstance(execution_groups, bool)
        or execution_groups != plan.execution_groups
    ):
        raise PlanArtifactError("plan_payload_invalid", "Sealed plan execution_groups changed.")
    return plan


def validate_plan_2d(plan: MissionPlan) -> list[str]:
    """Validate the current product boundary: one flat ``map`` frame only."""

    errors: list[str] = []
    for index, subtask in enumerate(plan.subtasks, start=1):
        label = subtask.node_id or f"task-{index}"
        if subtask.floor is not None:
            errors.append(
                f"Subtask {label} uses a floor target; only 2D map poses are supported."
            )
        target = subtask.target
        pose = target.get("pose") if isinstance(target, Mapping) else None
        if not isinstance(target, Mapping) or target.get("frame_id") != "map":
            errors.append(f"Subtask {label} target frame must be 'map'.")
            continue
        if not isinstance(pose, Mapping):
            errors.append(f"Subtask {label} target must contain a 2D pose.")
            continue
        for axis in ("x", "y"):
            value = pose.get(axis)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                errors.append(f"Subtask {label} pose.{axis} must be finite.")
        yaw = pose.get("yaw", 0.0)
        if (
            not isinstance(yaw, (int, float))
            or isinstance(yaw, bool)
            or not math.isfinite(float(yaw))
        ):
            errors.append(f"Subtask {label} pose.yaw must be finite when provided.")
    return list(dict.fromkeys(errors))


_RELATIVE_POSE_KEYWORDS: tuple[str, ...] = (
    "现在的位置",
    "目前的位置",
    "当前位置",
    "起点",
    "原位",
    "原路返回",
    "刚才的位置",
    "返回现在",
    "回到现在",
    "原来位置",
    "当前点",
)


def command_has_relative_pose_reference(command: str) -> bool:
    """Return whether an operator command refers to an evidence-bound pose."""

    return isinstance(command, str) and any(
        keyword in command for keyword in _RELATIVE_POSE_KEYWORDS
    )


def _snapshot_pose_evidence(
    state_snapshot: Any | None,
) -> dict[str, list[tuple[float, float, float]]]:
    evidence_poses: dict[str, list[tuple[float, float, float]]] = {}
    if state_snapshot is None:
        return evidence_poses
    robots = getattr(state_snapshot, "robots", None)
    if robots is None and isinstance(state_snapshot, dict):
        robots = state_snapshot.get("robots")
    if not isinstance(robots, (list, tuple)):
        return evidence_poses
    for robot in robots:
        if isinstance(robot, Mapping):
            robot_id = robot.get("robot_id")
            pose_evidence = robot.get("pose")
        else:
            robot_id = getattr(robot, "robot_id", None)
            pose_evidence = getattr(robot, "pose", None)
        if (
            isinstance(robot_id, str)
            and robot_id
            and isinstance(pose_evidence, Mapping)
            and pose_evidence.get("frame_id") == "map"
            and _finite_number(pose_evidence.get("x"))
            and _finite_number(pose_evidence.get("y"))
            and _finite_number(pose_evidence.get("yaw"))
        ):
            evidence_poses.setdefault(robot_id, []).append(
                (
                    float(pose_evidence["x"]),
                    float(pose_evidence["y"]),
                    float(pose_evidence["yaw"]),
                )
            )
    return evidence_poses


def bind_relative_target_yaws(
    plan: MissionPlan,
    state_snapshot: Any | None = None,
) -> MissionPlan:
    """Bind implicit relative-target headings to frozen live pose evidence.

    A relative operator command supplies the target position from authoritative
    state, but it does not supply a new heading.  A planner-generated ``yaw=0``
    is therefore treated as the schema default, not as operator intent.  The
    host replaces that default with the matching robot's frozen map yaw before
    sealing the artifact.  Non-default planner headings remain untouched and
    are checked by :func:`validate_plan_target_binding` so the planner cannot
    smuggle in an unstated orientation.
    """

    if (
        not command_has_relative_pose_reference(plan.command)
        or _COMMAND_YAW_RE.search(plan.command) is not None
    ):
        return plan
    evidence_poses = _snapshot_pose_evidence(state_snapshot)
    if not evidence_poses:
        return plan

    changed = False
    subtasks: list[MissionSubtask] = []
    for subtask in plan.subtasks:
        target = subtask.target
        pose = target.get("pose") if isinstance(target, Mapping) else None
        if (
            not isinstance(target, Mapping)
            or target.get("frame_id") != "map"
            or not isinstance(pose, Mapping)
            or not _finite_number(pose.get("x"))
            or not _finite_number(pose.get("y"))
        ):
            subtasks.append(subtask)
            continue
        grounded_evidence_pose = next(
            (
                evidence_pose
                for evidence_pose in evidence_poses.get(subtask.robot_id, [])
                if _same_bound_number(
                    float(pose["x"]), evidence_pose[0]
                )
                and _same_bound_number(
                    float(pose["y"]), evidence_pose[1]
                )
            ),
            None,
        )
        raw_yaw = pose.get("yaw")
        implicit_yaw = raw_yaw is None or (
            _finite_number(raw_yaw)
            and _same_bound_number(float(raw_yaw), 0.0)
        )
        if grounded_evidence_pose is None or not implicit_yaw:
            subtasks.append(subtask)
            continue
        bound_pose = {
            **dict(pose),
            "yaw": grounded_evidence_pose[2],
        }
        bound_target = {
            **dict(target),
            "pose": bound_pose,
        }
        subtasks.append(replace(subtask, target=bound_target))
        changed = True
    return replace(plan, subtasks=subtasks) if changed else plan


def validate_plan_target_binding(
    plan: MissionPlan,
    state_snapshot: Any | None = None,
) -> list[str]:
    """Reject map poses that were not explicitly grounded by the operator or live evidence.

    The LLM remains the planner. This deterministic boundary prevents it from
    inventing physical coordinates or a non-default heading that the operator
    did not provide and that no live robot TF evidence supports.
    """

    command_points: list[tuple[float, float]] = [
        (float(match.group(1)), float(match.group(2)))
        for match in _COMMAND_EXPLICIT_AXIS_RE.finditer(plan.command)
    ]
    for match in _COMMAND_COORDINATE_RE.finditer(plan.command):
        pt = (float(match.group(1)), float(match.group(2)))
        if pt not in command_points:
            command_points.append(pt)

    command_yaws = [
        float(match.group(1))
        for match in _COMMAND_YAW_RE.finditer(plan.command)
    ]
    unmatched_points = list(command_points)

    evidence_poses = _snapshot_pose_evidence(state_snapshot)

    has_relative_keyword = command_has_relative_pose_reference(plan.command)

    errors: list[str] = []
    for index, subtask in enumerate(plan.subtasks, start=1):
        label = subtask.node_id or f"task-{index}"
        target = subtask.target
        pose = target.get("pose") if isinstance(target, Mapping) else None
        if not isinstance(pose, Mapping):
            continue
        x = pose.get("x")
        y = pose.get("y")
        yaw = pose.get("yaw", 0.0)
        if not _finite_number(x) or not _finite_number(y):
            continue

        grounded_evidence_pose: tuple[float, float, float] | None = None
        matching_point = next(
            (
                point_index
                for point_index, (command_x, command_y) in enumerate(
                    unmatched_points
                )
                if _same_bound_number(float(x), command_x)
                and _same_bound_number(float(y), command_y)
            ),
            None,
        )
        if matching_point is not None:
            unmatched_points.pop(matching_point)
        elif has_relative_keyword:
            grounded_evidence_pose = next(
                (
                    evidence_pose
                    for evidence_pose in evidence_poses.get(subtask.robot_id, [])
                    if _same_bound_number(float(x), evidence_pose[0])
                    and _same_bound_number(float(y), evidence_pose[1])
                ),
                None,
            )
            if grounded_evidence_pose is None:
                errors.append(
                    f"Subtask {label} map pose ({float(x):g}, {float(y):g}) "
                    "is not bound to an explicit coordinate in the operator command."
                )
        else:
            errors.append(
                f"Subtask {label} map pose ({float(x):g}, {float(y):g}) "
                "is not bound to an explicit coordinate in the operator command."
            )

        if not _finite_number(yaw):
            continue
        numeric_yaw = float(yaw)
        if command_yaws:
            if not any(
                _same_bound_number(numeric_yaw, command_yaw)
                for command_yaw in command_yaws
            ):
                errors.append(
                    f"Subtask {label} pose.yaw={numeric_yaw:g} is not bound "
                    "to an explicit yaw in the operator command."
                )
        elif grounded_evidence_pose is not None:
            implicit_relative_yaw = (
                has_relative_keyword
                and not command_yaws
                and _same_bound_number(numeric_yaw, 0.0)
            )
            if implicit_relative_yaw:
                # ``yaw=0`` is the planner/schema default when the operator
                # only asked to return to the current position.  The host
                # binds it to the live pose before sealing the artifact.
                continue
            if not _same_bound_number(numeric_yaw, grounded_evidence_pose[2]):
                errors.append(
                    f"Subtask {label} pose.yaw={numeric_yaw:g} does not match "
                    f"live pose yaw={grounded_evidence_pose[2]:g} for robot "
                    f"{subtask.robot_id}."
                )
        elif not _same_bound_number(numeric_yaw, 0.0):
            errors.append(
                f"Subtask {label} uses non-default pose.yaw={numeric_yaw:g} "
                "without an explicit operator yaw."
            )
    return list(dict.fromkeys(errors))


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _same_bound_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)


def _mission_subtask_from_dict(raw: Any) -> MissionSubtask:
    if not isinstance(raw, Mapping):
        raise PlanArtifactError("plan_payload_invalid", "Sealed subtask must be an object.")
    allowed = {
        "robot_id",
        "command",
        "floor",
        "capability_required",
        "execution_group",
        "node_id",
        "task_type",
        "target",
        "completion_goal",
        "completion_contract",
        "belief_requirements",
    }
    if set(raw) - allowed:
        raise PlanArtifactError("plan_payload_invalid", "Sealed subtask contains unknown fields.")
    floor = raw.get("floor")
    if floor is not None and (not isinstance(floor, int) or isinstance(floor, bool)):
        raise PlanArtifactError("plan_payload_invalid", "Sealed subtask floor is invalid.")
    group = raw.get("execution_group", 0)
    if not isinstance(group, int) or isinstance(group, bool):
        raise PlanArtifactError("plan_payload_invalid", "Sealed execution_group is invalid.")
    target = raw.get("target", {})
    contract = raw.get("completion_contract", {})
    beliefs = raw.get("belief_requirements", [])
    if not isinstance(target, Mapping) or not isinstance(contract, Mapping) or not isinstance(beliefs, list):
        raise PlanArtifactError("plan_payload_invalid", "Sealed subtask metadata is invalid.")
    if any(not isinstance(item, Mapping) for item in beliefs):
        raise PlanArtifactError("plan_payload_invalid", "Sealed belief requirements are invalid.")
    return MissionSubtask(
        robot_id=_required_string(raw, "robot_id"),
        command=_required_string(raw, "command"),
        floor=floor,
        capability_required=_required_string(raw, "capability_required"),
        execution_group=group,
        node_id=_optional_storage_string(raw.get("node_id")),
        task_type=_optional_storage_string(raw.get("task_type")),
        target=_json_object_copy(target),
        completion_goal=_optional_storage_string(raw.get("completion_goal")),
        completion_contract=_json_object_copy(contract),
        belief_requirements=[_json_object_copy(item) for item in beliefs],
    )


def _semantic_evidence(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PlanArtifactError("readiness_unavailable", f"Readiness evidence {label} is missing.")
    source = raw.get("source")
    freshness = raw.get("freshness")
    if not isinstance(source, str) or not source.strip() or freshness not in {"fresh", "stale", "unknown"}:
        raise PlanArtifactError("readiness_unavailable", f"Readiness evidence {label} is invalid.")
    return {
        "value": _json_value_copy(raw.get("value")),
        "source": source,
        "freshness": freshness,
    }


def _validate_record(record: PlanArtifactRecord) -> None:
    if not _ARTIFACT_ID_RE.fullmatch(record.artifact_id):
        raise PlanArtifactError("plan_artifact_store_corrupt", "Plan artifact id is invalid.")
    for digest in (record.plan_digest, record.binding_digest, record.profile_revision, record.readiness_revision):
        if not _SHA256_REVISION_RE.fullmatch(digest):
            raise PlanArtifactError("plan_artifact_store_corrupt", "Plan artifact digest is invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", record.token_sha256):
        raise PlanArtifactError("plan_artifact_store_corrupt", "Plan token digest is invalid.")
    if record.status not in PLAN_ARTIFACT_STATUSES or record.status_version < 1:
        raise PlanArtifactError("plan_artifact_store_corrupt", "Plan artifact status is invalid.")
    if record.runtime_mode not in {"simulation", "real"} or record.risk_level not in _RISK_ORDER:
        raise PlanArtifactError("plan_artifact_store_corrupt", "Plan artifact binding is invalid.")
    _normalize_robot_ids(record.robot_ids)
    mission_plan_from_dict(record.plan_payload())
    expected_plan_digest = "sha256:" + _sha256_text(record.plan_json)
    if not hmac.compare_digest(record.plan_digest, expected_plan_digest):
        raise PlanArtifactError(
            "plan_artifact_store_corrupt",
            "Sealed plan digest does not match its payload.",
        )
    approvals = record.required_approvals()
    if not approvals:
        raise PlanArtifactError("plan_artifact_store_corrupt", "Plan approvals are missing.")
    try:
        created_at = datetime.fromisoformat(record.created_at)
        expires_at = datetime.fromisoformat(record.expires_at)
    except ValueError as exc:
        raise PlanArtifactError(
            "plan_artifact_store_corrupt",
            "Plan artifact timestamps are invalid.",
        ) from exc
    created_at = _aware_utc(created_at)
    expires_at = _aware_utc(expires_at)
    if expires_at <= created_at or abs(expires_at.timestamp() - record.expires_at_epoch) > 1e-6:
        raise PlanArtifactError(
            "plan_artifact_store_corrupt",
            "Plan artifact expiry binding is invalid.",
        )
    expected_binding_payload = {
        "schema_version": PLAN_ARTIFACT_SCHEMA_VERSION,
        "artifact_id": record.artifact_id,
        "plan_digest": record.plan_digest,
        "operator_id": record.operator_id,
        "session_id": record.session_id,
        "robot_ids": list(record.robot_ids),
        "runtime_epoch": record.runtime_epoch,
        "runtime_mode": record.runtime_mode,
        "profile_revision": record.profile_revision,
        "readiness_revision": record.readiness_revision,
        "risk_level": record.risk_level,
        "required_approvals": approvals,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
    }
    expected_binding_digest = "sha256:" + _sha256_text(
        _canonical_json(expected_binding_payload)
    )
    if not hmac.compare_digest(record.binding_digest, expected_binding_digest):
        raise PlanArtifactError(
            "plan_artifact_store_corrupt",
            "Plan artifact binding digest does not match its payload.",
        )


def _normalize_robot_ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PlanArtifactError("plan_robot_mismatch", "Robot binding must be a list.")
    normalized = tuple(sorted({_nonempty(value, "robot_id") for value in values}))
    if not normalized:
        raise PlanArtifactError("plan_robot_mismatch", "Plan must bind at least one robot.")
    return normalized


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _required_string_list(raw: Mapping[str, Any], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must be a string list")
    return [item.strip() for item in value]


def _required_int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_number(raw: Mapping[str, Any], key: str) -> float:
    value = raw.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _optional_storage_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional value must be a non-empty string")
    return value.strip()


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanArtifactError("plan_binding_invalid", f"{label} must be non-empty.")
    return value.strip()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PlanArtifactError("plan_clock_invalid", "Plan clock must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PlanArtifactError("plan_payload_invalid", "Plan payload is not canonical JSON.") from exc


def _json_value_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value), parse_constant=_reject_json_constant)


def _json_object_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = _json_value_copy(dict(value))
    if not isinstance(copied, dict):  # pragma: no cover - caller invariant
        raise PlanArtifactError("plan_payload_invalid", "Expected a JSON object.")
    return copied


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"Non-finite JSON constant is forbidden: {value}")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _max_risk(left: str, right: str) -> str:
    return left if _RISK_ORDER[left] >= _RISK_ORDER[right] else right


__all__ = [
    "DEFAULT_PLAN_TTL_SECONDS",
    "IssuedPlanArtifact",
    "PLAN_ARTIFACT_SCHEMA_VERSION",
    "PlanArtifactError",
    "PlanArtifactRecord",
    "PlanArtifactStore",
    "ReadinessBinding",
    "assess_plan_risk",
    "build_readiness_binding",
    "mission_plan_from_dict",
    "require_dispatchable_readiness",
    "required_plan_approvals",
    "validate_plan_2d",
]
