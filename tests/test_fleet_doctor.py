from fireclaw_core.devtools.fleet_doctor import FleetDoctor, FleetDoctorFinding
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


class FakeSubagentClient:
    def __init__(self):
        self.presence_results = {}

    def check_presence(self, entry):
        if entry.robot_id in self.presence_results:
            return self.presence_results[entry.robot_id]
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-06-08T00:00:00+00:00",
            "state": {},
        }


def test_doctor_healthy_fleet():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims", "emergency_stop")),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("firefight", "emergency_stop")),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    summary = doctor.summary(findings)

    assert summary["status"] == "healthy"
    assert summary["error_count"] == 0
    # Only info-level onboarding findings should remain (enrolled, enabled counts)
    warnings = [f for f in findings if f.severity == "warning"]
    assert len(warnings) == 0


def test_doctor_detects_unreachable_robot():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("firefight",)),
    ])
    client = FakeSubagentClient()
    client.presence_results["r2"] = {
        "robot_id": "r2",
        "online": False,
        "error": "Connection refused",
    }
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    summary = doctor.summary(findings)

    assert summary["status"] == "unhealthy"
    assert summary["error_count"] == 1
    assert any(f.category == "reachability" and f.robot_id == "r2" for f in findings)


def test_doctor_warns_on_empty_capabilities():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    summary = doctor.summary(findings)

    assert summary["status"] == "healthy"
    capabilities_warning = [f for f in findings if f.category == "capabilities" and f.robot_id == "r1"]
    assert len(capabilities_warning) == 1
    # Onboarding also warns about missing emergency_stop
    onboarding_warnings = [f for f in findings if f.category == "onboarding" and f.severity == "warning"]
    assert len(onboarding_warnings) >= 1


def test_doctor_warns_on_empty_registry():
    registry = RobotRegistry([])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    summary = doctor.summary(findings)

    assert summary["warning_count"] == 1
    assert any(f.category == "registry" and "empty" in f.message for f in findings)


def test_doctor_reports_disabled_robots():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",), enabled=False),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()

    assert any(f.category == "registry" and f.robot_id == "r1" and "disabled" in f.message for f in findings)
    # Disabled robots should NOT be pinged
    assert not any(f.category == "reachability" and f.robot_id == "r1" for f in findings)


def test_finding_to_dict():
    finding = FleetDoctorFinding(
        severity="error",
        category="reachability",
        robot_id="r1",
        message="Robot r1 is unreachable.",
        details={"error": "timeout"},
    )
    d = finding.to_dict()
    assert d["severity"] == "error"
    assert d["category"] == "reachability"
    assert d["robot_id"] == "r1"
    assert d["details"] == {"error": "timeout"}


# ---------------------------------------------------------------------------
# Onboarding report tests
# ---------------------------------------------------------------------------


def test_onboarding_reports_enrolled_and_enabled_counts():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("firefight",)),
        RobotRegistryEntry(robot_id="r3", base_url="http://r3:8765", capabilities=("search_for_victims",), enabled=False),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    onboarding = [f for f in findings if f.category == "onboarding"]

    enrolled = next(f for f in onboarding if "enrolled" in f.message.lower())
    enabled = next(f for f in onboarding if "enabled" in f.message.lower() and "enrolled" not in f.message.lower())
    assert enrolled.details["total"] == 3
    assert enabled.details["count"] == 2


def test_onboarding_reports_stale_heartbeats():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("firefight",)),
    ], heartbeat_timeout_seconds=60.0)
    # r1 was seen long ago -> stale
    registry.update_presence("r1", "2020-01-01T00:00:00+00:00")
    # r2 never seen -> also stale
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    onboarding = [f for f in findings if f.category == "onboarding"]
    stale_findings = [f for f in onboarding if "stale" in f.message.lower() and "heartbeat" in f.message.lower()]
    assert len(stale_findings) >= 1
    stale_details = next(f for f in stale_findings if f.robot_id is None or f.robot_id == "")
    assert stale_details.details["stale_robot_ids"] == ["r1", "r2"]


def test_onboarding_warns_missing_emergency_stop_capability():
    """Fleet with no robot declaring emergency_stop capability triggers a warning."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    onboarding = [f for f in findings if f.category == "onboarding"]
    estop_findings = [f for f in onboarding if "emergency" in f.message.lower()]
    assert len(estop_findings) >= 1
    assert estop_findings[0].severity == "warning"
    assert "no enabled robot" in estop_findings[0].message.lower()


def test_onboarding_reports_missing_ros1_remaps():
    """Robets with capabilities but no matching ROS1 remaps are flagged."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims", "spray_water")),
    ])
    client = FakeSubagentClient()
    # Only search_for_victims is remapped
    ros1_config = {"search_for_victims": "nav"}
    doctor = FleetDoctor(registry=registry, subagent_client=client, ros1_skill_remapping=ros1_config)

    findings = doctor.diagnose()
    onboarding = [f for f in findings if f.category == "onboarding"]
    remap_findings = [f for f in onboarding if "remap" in f.message.lower() or "ros1" in f.message.lower()]
    assert len(remap_findings) >= 1
    assert any("spray_water" in f.message for f in remap_findings)


def test_onboarding_reports_unresolved_approval_relay():
    """If approval_relay is configured but no channel metadata exists, warn."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(
        registry=registry,
        subagent_client=client,
        approval_relay_config={"enabled": True, "channel": None},
    )

    findings = doctor.diagnose()
    onboarding = [f for f in findings if f.category == "onboarding"]
    relay_findings = [f for f in onboarding if "approval" in f.message.lower() or "relay" in f.message.lower()]
    assert len(relay_findings) >= 1
    assert relay_findings[0].severity == "warning"


def test_onboarding_clean_fleet_has_no_warnings():
    """A fully configured fleet should produce no onboarding warnings."""
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims", "emergency_stop"),
        ),
    ])
    client = FakeSubagentClient()
    ros1_config = {"search_for_victims": "nav", "emergency_stop": "estop"}
    doctor = FleetDoctor(
        registry=registry,
        subagent_client=client,
        ros1_skill_remapping=ros1_config,
        approval_relay_config={"enabled": False},
    )

    findings = doctor.diagnose()
    onboarding = [f for f in findings if f.category == "onboarding"]
    warnings = [f for f in onboarding if f.severity == "warning"]
    errors = [f for f in onboarding if f.severity == "error"]
    assert len(warnings) == 0
    assert len(errors) == 0


# ---------------------------------------------------------------------------
# Lifecycle maintenance integration tests
# ---------------------------------------------------------------------------


def test_fleet_doctor_reports_lifecycle_maintenance_warnings(tmp_path):
    """Fleet doctor should include lifecycle section when stale tasks and orphaned subagents exist."""
    from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
    from fireclaw_core.task.task_registry import JsonlTaskRegistryStore

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()

    # Set up task and subagent registries with test data
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    # Create one stale active task (created long ago, still active)
    tasks.create(
        task_id="stale-task-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="r1",
        scope_kind="mission",
        command="search",
        created_at="2026-06-10T00:00:00+00:00",
    )

    # Create one orphaned subagent (child_task_id has no matching task)
    subagents.create(
        parent_mission_id="mission-1",
        robot_id="r1",
        child_task_id="ghost-child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    doctor = FleetDoctor(
        registry=registry,
        subagent_client=client,
        task_registry=tasks,
        subagent_registry=subagents,
    )

    # Force a known "now" so stale detection is deterministic
    findings = doctor.diagnose(now="2026-06-10T00:10:00+00:00", stale_threshold_seconds=60)
    report = doctor.summary(findings)

    assert report["lifecycle"]["status"] == "warn"
    assert report["lifecycle"]["stale_tasks"]
    assert report["lifecycle"]["orphaned_subagents"]


def test_fleet_doctor_lifecycle_ok_when_registries_healthy(tmp_path):
    """Fleet doctor lifecycle section should be 'ok' when no stale tasks or orphans exist."""
    from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
    from fireclaw_core.task.task_registry import JsonlTaskRegistryStore

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()

    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    # Healthy: task + matching subagent, both recent
    tasks.create(
        task_id="child-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="r1",
        scope_kind="mission",
        command="search",
        created_at="2026-06-10T00:00:00+00:00",
    )
    subagents.create(
        parent_mission_id="mission-1",
        robot_id="r1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    doctor = FleetDoctor(
        registry=registry,
        subagent_client=client,
        task_registry=tasks,
        subagent_registry=subagents,
    )

    findings = doctor.diagnose(now="2026-06-10T00:01:00+00:00", stale_threshold_seconds=300)
    report = doctor.summary(findings)

    assert report["lifecycle"]["status"] == "ok"
    assert report["lifecycle"]["stale_tasks"] == []
    assert report["lifecycle"]["orphaned_subagents"] == []


def test_fleet_doctor_no_lifecycle_section_without_registries():
    """Fleet doctor should not include lifecycle section when registries not provided."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()

    doctor = FleetDoctor(registry=registry, subagent_client=client)
    findings = doctor.diagnose()
    report = doctor.summary(findings)

    assert "lifecycle" not in report
