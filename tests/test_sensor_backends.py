import pytest

from fireclaw_core.sensors.backends import (
    StaticDeclaredDiscoveryBackend,
    ensure_real_mode_backend_allowed,
)


def test_static_declared_backend_labels_dry_run_source_and_verified_sensors() -> None:
    backend = StaticDeclaredDiscoveryBackend(
        sensors=("rgb_camera", "lidar"),
        source="dry_run",
        allow_real_mode=False,
    )

    report = backend.discover()

    assert report.source == "dry_run"
    assert report.verified_sensors() == ["rgb_camera", "lidar"]
    assert all(finding.status == "verified" for finding in report.findings)
    assert all(finding.health_status == "healthy" for finding in report.findings)


def test_static_declared_backend_is_rejected_for_real_mode_by_default() -> None:
    backend = StaticDeclaredDiscoveryBackend(
        sensors=("rgb_camera",),
        source="dry_run",
        allow_real_mode=False,
    )

    with pytest.raises(ValueError, match="Static sensor discovery backend is not allowed for real mode"):
        ensure_real_mode_backend_allowed(backend, mode="ros1", dry_run=False)


def test_static_declared_backend_is_allowed_for_dry_run() -> None:
    backend = StaticDeclaredDiscoveryBackend(
        sensors=("rgb_camera",),
        source="dry_run",
        allow_real_mode=False,
    )

    ensure_real_mode_backend_allowed(backend, mode="dry_run", dry_run=True)
