from fireclaw_core.safety.sensor_policy import evaluate_sensor_policy


def test_policy_allows_healthy_required_sensor() -> None:
    decision = evaluate_sensor_policy(
        skill_name="victim_search",
        sensor="rgb_camera",
        health_status="healthy",
        mode="ros1",
        dry_run=False,
        verified_sensors={"rgb_camera"},
    )

    assert decision.action == "allow"
    assert decision.reason is None


def test_policy_blocks_victim_search_when_rgb_camera_degraded_without_alternative() -> None:
    decision = evaluate_sensor_policy(
        skill_name="victim_search",
        sensor="rgb_camera",
        health_status="degraded",
        health_reason="payload is empty",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
        safety_class="victim_perception",
        sensor_alternatives={"rgb_camera": ("thermal_camera",)},
    )

    assert decision.action == "block"
    assert decision.reason == "Skill victim_search requires rgb_camera, but health is degraded: payload is empty"


def test_policy_escalates_victim_search_when_rgb_camera_degraded_but_thermal_verified() -> None:
    decision = evaluate_sensor_policy(
        skill_name="victim_search",
        sensor="rgb_camera",
        health_status="degraded",
        health_reason="payload is empty",
        mode="ros1",
        dry_run=False,
        verified_sensors={"thermal_camera"},
        safety_class="victim_perception",
        sensor_alternatives={"rgb_camera": ("thermal_camera",)},
    )

    assert decision.action == "escalate"
    assert decision.reason == "Skill victim_search requires rgb_camera, but thermal_camera is the only verified victim-search sensor."


def test_policy_blocks_gas_detector_failures_for_real_execution() -> None:
    decision = evaluate_sensor_policy(
        skill_name="enter_hazard_zone",
        sensor="gas_detector",
        health_status="stale",
        health_reason="no recent observation",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
        safety_class="motion",
    )

    assert decision.action == "block"
    assert decision.reason == "Skill enter_hazard_zone requires gas_detector, but health is stale: no recent observation"


def test_policy_blocks_lidar_failures_for_real_navigation() -> None:
    decision = evaluate_sensor_policy(
        skill_name="navigate_to_waypoint",
        sensor="lidar",
        health_status="invalid",
        health_reason="no finite ranges",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
        safety_class="motion",
    )

    assert decision.action == "block"
    assert decision.reason == "Skill navigate_to_waypoint requires lidar, but health is invalid: no finite ranges"


def test_policy_warns_for_unknown_sensor_health_in_dry_run() -> None:
    decision = evaluate_sensor_policy(
        skill_name="navigate_to_waypoint",
        sensor="lidar",
        health_status="unknown",
        health_reason="observation age is unknown",
        mode="simulator",
        dry_run=True,
        verified_sensors=set(),
    )

    assert decision.action == "warn"
    assert decision.reason == "Skill navigate_to_waypoint requires lidar, but health is unknown: observation age is unknown"


def test_policy_escalates_unknown_sensor_health_for_real_execution() -> None:
    decision = evaluate_sensor_policy(
        skill_name="navigate_to_waypoint",
        sensor="imu",
        health_status="unknown",
        health_reason="observation age is unknown",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
    )

    assert decision.action == "escalate"
    assert decision.reason == "Skill navigate_to_waypoint requires imu, but health is unknown: observation age is unknown"
