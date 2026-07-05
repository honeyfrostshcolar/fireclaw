from fireclaw_core.sensors.health import (
    DEFAULT_SENSOR_HEALTH_POLICIES,
    SensorObservation,
    evaluate_sensor_health,
)


def test_camera_health_requires_recent_non_empty_payload() -> None:
    result = evaluate_sensor_health(
        "rgb_camera",
        SensorObservation(observed=True, age_seconds=0.5, payload_size=128, frame_id="camera"),
    )

    assert result.status == "healthy"
    assert result.reason is None


def test_camera_health_degrades_empty_payload() -> None:
    result = evaluate_sensor_health(
        "rgb_camera",
        SensorObservation(observed=True, age_seconds=0.5, payload_size=0, frame_id="camera"),
    )

    assert result.status == "invalid"
    assert result.reason == "payload is empty"


def test_gas_detector_health_rejects_non_finite_value() -> None:
    result = evaluate_sensor_health(
        "gas_detector",
        SensorObservation(observed=True, age_seconds=0.2, numeric_value=float("nan")),
    )

    assert result.status == "invalid"
    assert result.reason == "numeric value is not finite"


def test_gas_detector_health_rejects_out_of_range_value() -> None:
    result = evaluate_sensor_health(
        "gas_detector",
        SensorObservation(observed=True, age_seconds=0.2, numeric_value=-1.0),
    )

    assert result.status == "invalid"
    assert result.reason == "numeric value below minimum: -1.0 < 0.0"


def test_lidar_health_requires_finite_ranges() -> None:
    result = evaluate_sensor_health(
        "lidar",
        SensorObservation(observed=True, age_seconds=0.2, finite_range_count=0, frame_id="base_scan"),
    )

    assert result.status == "invalid"
    assert result.reason == "no finite ranges"


def test_unknown_sensor_falls_back_to_freshness_only() -> None:
    result = evaluate_sensor_health(
        "custom_sensor",
        SensorObservation(observed=True, age_seconds=0.2),
    )

    assert result.status == "healthy"
    assert result.reason is None


def test_default_policy_names_cover_initial_sensor_set() -> None:
    assert set(DEFAULT_SENSOR_HEALTH_POLICIES) >= {
        "rgb_camera",
        "thermal_camera",
        "gas_detector",
        "lidar",
        "imu",
    }
