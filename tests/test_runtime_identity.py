from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fireclaw_core.mission.runtime_identity import (
    GatewayRuntimeIdentity,
    build_gateway_runtime_identity,
    evidence_envelope,
)


def _write_profile(tmp_path: Path, *, mode: str = "simulation") -> Path:
    profile = tmp_path / "active-profile.toml"
    profile.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
data_dir = "robot-data"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[capability_skill_chains]
navigation = ["navigate_to_point"]

[deployment]
id = "robot-1-deployment"
mode = "{mode}"
output_root = "{tmp_path / 'deployments'}"

[deployment.ros1]
distro = "noetic"
setup_files = ["/opt/ros/noetic/setup.bash"]

[plugins]
paths = ["{tmp_path / 'extensions'}"]
selected = ["fireclaw.navigation.move-base"]
""",
        encoding="utf-8",
    )
    return profile


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_receipt(profile: Path, *, fingerprint: str = "b" * 64) -> Path:
    receipt = profile.parent / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "installed",
                "deployment_id": "robot-1-deployment",
                "fingerprint": fingerprint,
                "profile_path": str(profile.resolve()),
                "profile_sha256": _sha256(profile),
            }
        ),
        encoding="utf-8",
    )
    return receipt


def test_build_gateway_runtime_identity_from_profile_and_receipt(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path)
    receipt = _write_receipt(profile)

    identity = build_gateway_runtime_identity(
        profile,
        configured_runtime_mode="simulation",
        deployment_receipt_path=receipt,
        observed_at="2026-08-20T00:00:00+00:00",
    )
    evidence = identity.to_evidence()

    assert evidence["value"] == {
        "schema_version": 1,
        "runtime_mode": "simulation",
        "active_profile_path": str(profile.resolve()),
        "profile_sha256": _sha256(profile),
        "profile_revision": f"sha256:{_sha256(profile)}",
        "robot_id": "robot-1",
        "deployment_fingerprint": "b" * 64,
    }
    assert evidence["source"] == "gateway_startup_profile"
    assert evidence["observed_at"] == "2026-08-20T00:00:00+00:00"
    assert evidence["freshness"] == "fresh"
    assert evidence["evidence_id"].startswith("sha256:")


def test_runtime_identity_is_frozen_after_profile_changes(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path)
    identity = build_gateway_runtime_identity(
        profile,
        observed_at="2026-08-20T00:00:00+00:00",
    )
    original = identity.to_evidence()

    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            'mode = "simulation"',
            'mode = "real"',
        ),
        encoding="utf-8",
    )

    assert identity.to_evidence() == original


def test_generic_gateway_config_is_not_mislabeled_as_active_profile(
    tmp_path: Path,
) -> None:
    config = tmp_path / "fireclaw.toml"
    config.write_text('[server]\nhost = "127.0.0.1"\n', encoding="utf-8")

    identity = build_gateway_runtime_identity(
        config,
        configured_runtime_mode="simulation",
        observed_at="2026-08-20T00:00:00+00:00",
    )

    assert identity == GatewayRuntimeIdentity.unknown(
        source="gateway_startup_config_not_robot_profile",
        observed_at="2026-08-20T00:00:00+00:00",
    )
    assert identity.to_evidence()["freshness"] == "unknown"
    assert identity.to_evidence()["value"]["runtime_mode"] is None


def test_declared_but_invalid_robot_profile_fails_instead_of_becoming_unknown(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "invalid-profile.toml"
    profile.write_text(
        "[robot]\nid = 'robot-1'\n[deployment]\nmode = 'simulation'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Robot Profile is invalid"):
        build_gateway_runtime_identity(profile)


def test_runtime_identity_rejects_mode_or_explicit_receipt_mismatch(
    tmp_path: Path,
) -> None:
    profile = _write_profile(tmp_path)
    with pytest.raises(ValueError, match="runtime mode"):
        build_gateway_runtime_identity(
            profile,
            configured_runtime_mode="real",
        )

    receipt = _write_receipt(profile, fingerprint="not-a-sha")
    with pytest.raises(ValueError, match="fingerprint"):
        build_gateway_runtime_identity(
            profile,
            deployment_receipt_path=receipt,
        )


def test_evidence_envelope_is_deterministic_and_requires_freshness() -> None:
    first = evidence_envelope(
        {"status": "online"},
        source="robot_gateway_state_probe",
        observed_at="2026-08-20T00:00:00+00:00",
        freshness="fresh",
    )
    second = evidence_envelope(
        {"status": "online"},
        source="robot_gateway_state_probe",
        observed_at="2026-08-20T00:00:00+00:00",
        freshness="fresh",
    )
    assert first == second
    assert first["evidence_id"].startswith("sha256:")
    with pytest.raises(ValueError, match="freshness"):
        evidence_envelope(
            None,
            source="test",
            observed_at="2026-08-20T00:00:00+00:00",
            freshness="forever",
        )
