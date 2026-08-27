"""Immutable Gateway runtime identity and evidence envelopes.

The Mission Gateway captures this identity exactly once during process startup.
Readiness callers may observe changing robot reachability, but they must never
reinterpret a generic Gateway config or the first registry entry as the active
Robot Profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment import load_runtime_deployment_profile
from fireclaw_core.infra import tomllib_compat as tomllib


RUNTIME_IDENTITY_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def evidence_envelope(
    value: Any,
    *,
    source: str,
    observed_at: str,
    freshness: str,
) -> dict[str, Any]:
    """Wrap one authoritative observation with stable provenance metadata."""

    if not isinstance(source, str) or not source.strip():
        raise ValueError("evidence source must be a non-empty string")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("evidence observed_at must be a non-empty string")
    if freshness not in {"fresh", "stale", "unknown"}:
        raise ValueError("evidence freshness must be fresh, stale, or unknown")
    identity_payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "value": value,
        "source": source,
        "observed_at": observed_at,
        "freshness": freshness,
    }
    return {
        **identity_payload,
        "evidence_id": "sha256:" + _canonical_json_sha256(identity_payload),
    }


@dataclass(frozen=True)
class GatewayRuntimeIdentity:
    """Profile-derived process identity frozen at Gateway startup."""

    runtime_mode: str | None
    active_profile_path: str | None
    profile_sha256: str | None
    profile_revision: str | None
    robot_id: str | None
    deployment_fingerprint: str | None
    source: str
    observed_at: str
    freshness: str

    @classmethod
    def create(
        cls,
        *,
        runtime_mode: str,
        active_profile_path: str | Path,
        profile_sha256: str,
        robot_id: str,
        deployment_fingerprint: str | None = None,
        source: str = "gateway_startup_profile",
        observed_at: str | None = None,
    ) -> "GatewayRuntimeIdentity":
        if runtime_mode not in {"simulation", "real"}:
            raise ValueError("runtime identity mode must be simulation or real")
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise ValueError("runtime identity robot_id must be non-empty")
        if not _SHA256_RE.fullmatch(profile_sha256):
            raise ValueError("runtime identity profile_sha256 must be lowercase SHA-256")
        if deployment_fingerprint is not None and not _SHA256_RE.fullmatch(
            deployment_fingerprint
        ):
            raise ValueError(
                "runtime identity deployment_fingerprint must be lowercase SHA-256"
            )
        return cls(
            runtime_mode=runtime_mode,
            active_profile_path=str(Path(active_profile_path).resolve(strict=True)),
            profile_sha256=profile_sha256,
            profile_revision=f"sha256:{profile_sha256}",
            robot_id=robot_id.strip(),
            deployment_fingerprint=deployment_fingerprint,
            source=source,
            observed_at=observed_at or utc_now_iso(),
            freshness="fresh",
        )

    @classmethod
    def unknown(
        cls,
        *,
        source: str = "gateway_startup_unconfigured",
        observed_at: str | None = None,
    ) -> "GatewayRuntimeIdentity":
        return cls(
            runtime_mode=None,
            active_profile_path=None,
            profile_sha256=None,
            profile_revision=None,
            robot_id=None,
            deployment_fingerprint=None,
            source=source,
            observed_at=observed_at or utc_now_iso(),
            freshness="unknown",
        )

    def value_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
            "runtime_mode": self.runtime_mode,
            "active_profile_path": self.active_profile_path,
            "profile_sha256": self.profile_sha256,
            "profile_revision": self.profile_revision,
            "robot_id": self.robot_id,
            "deployment_fingerprint": self.deployment_fingerprint,
        }

    def to_evidence(self) -> dict[str, Any]:
        return evidence_envelope(
            self.value_dict(),
            source=self.source,
            observed_at=self.observed_at,
            freshness=self.freshness,
        )


def build_gateway_runtime_identity(
    profile_path: str | Path | None,
    *,
    configured_runtime_mode: str | None = None,
    deployment_receipt_path: str | Path | None = None,
    observed_at: str | None = None,
) -> GatewayRuntimeIdentity:
    """Build a startup identity, returning UNKNOWN for a generic config file.

    A path is authoritative only when both the Robot capability and Runtime
    deployment Profile loaders accept it. An explicitly supplied deployment
    receipt is fail-closed; an absent receipt leaves only the optional
    deployment fingerprint unknown.
    """

    captured_at = observed_at or utc_now_iso()
    if profile_path is None:
        return GatewayRuntimeIdentity.unknown(observed_at=captured_at)
    authored = Path(profile_path).expanduser()
    if authored.is_symlink():
        raise ValueError("active runtime Profile must be a regular non-symlink file")
    try:
        resolved = authored.resolve(strict=True)
    except OSError as exc:
        raise ValueError("active runtime Profile does not exist") from exc
    if not resolved.is_file():
        raise ValueError("active runtime Profile must be a regular non-symlink file")
    try:
        robot = load_robot_capability_profile(resolved)
        deployment = load_runtime_deployment_profile(resolved)
    except (OSError, ValueError) as exc:
        # ``fireclaw serve`` also accepts generic Gateway-only TOML files.
        # Such a file is configuration input, not an active Robot Profile.
        if deployment_receipt_path is not None or _declares_robot_deployment_profile(
            resolved
        ):
            raise ValueError(
                "the declared active runtime Robot Profile is invalid"
            ) from exc
        return GatewayRuntimeIdentity.unknown(
            source="gateway_startup_config_not_robot_profile",
            observed_at=captured_at,
        )
    if configured_runtime_mode is not None and configured_runtime_mode != deployment.mode:
        raise ValueError(
            "configured runtime mode does not match the active deployment Profile"
        )
    if deployment.robot_id is not None and deployment.robot_id != robot.robot_id:
        raise ValueError(
            "deployment robot_id does not match the active Robot capability Profile"
        )

    profile_sha256 = _sha256_file(resolved)
    receipt_path: Path | None
    receipt_is_explicit = deployment_receipt_path is not None
    if receipt_is_explicit:
        receipt_path = Path(deployment_receipt_path).expanduser()
    else:
        candidate = deployment.deployment_root / "deployment-receipt.json"
        receipt_path = candidate if candidate.is_file() and not candidate.is_symlink() else None
    deployment_fingerprint = None
    if receipt_path is not None:
        try:
            deployment_fingerprint = _validated_deployment_fingerprint(
                receipt_path,
                profile_path=resolved,
                profile_sha256=profile_sha256,
                deployment_id=deployment.deployment_id,
            )
        except (OSError, ValueError):
            if receipt_is_explicit:
                raise
            # A stale optional root receipt does not redefine process identity.
            # Profile identity remains authoritative; deployment revision stays
            # unknown until a matching installed receipt exists.
            deployment_fingerprint = None

    return GatewayRuntimeIdentity.create(
        runtime_mode=deployment.mode,
        active_profile_path=resolved,
        profile_sha256=profile_sha256,
        robot_id=robot.robot_id,
        deployment_fingerprint=deployment_fingerprint,
        observed_at=captured_at,
    )


def _validated_deployment_fingerprint(
    receipt_path: str | Path,
    *,
    profile_path: Path,
    profile_sha256: str,
    deployment_id: str,
) -> str:
    authored = Path(receipt_path)
    if authored.is_symlink():
        raise ValueError("deployment receipt must be a regular non-symlink JSON file")
    resolved = authored.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("deployment receipt must be a regular non-symlink JSON file")
    receipt = _read_json_object(resolved)
    if receipt.get("status") != "installed":
        raise ValueError("deployment receipt does not report installed status")
    if receipt.get("deployment_id") != deployment_id:
        raise ValueError("deployment receipt deployment_id does not match Profile")
    if receipt.get("profile_sha256") != profile_sha256:
        raise ValueError("deployment receipt profile hash does not match Profile")
    receipt_profile = receipt.get("profile_path")
    if not isinstance(receipt_profile, str):
        raise ValueError("deployment receipt profile_path is missing")
    if Path(receipt_profile).expanduser().resolve(strict=False) != profile_path:
        raise ValueError("deployment receipt profile_path does not match Profile")
    fingerprint = receipt.get("fingerprint")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise ValueError("deployment receipt fingerprint is invalid")
    return fingerprint


def _read_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    parsed = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(parsed, dict):
        raise ValueError("deployment receipt must be a JSON object")
    return parsed


def _declares_robot_deployment_profile(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, ValueError):
        return False
    return isinstance(value.get("robot"), dict) and isinstance(
        value.get("deployment"),
        dict,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
