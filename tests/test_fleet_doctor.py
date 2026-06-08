from fireclaw_core.fleet_doctor import FleetDoctor, FleetDoctorFinding
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


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
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("firefight",)),
    ])
    client = FakeSubagentClient()
    doctor = FleetDoctor(registry=registry, subagent_client=client)

    findings = doctor.diagnose()
    summary = doctor.summary(findings)

    assert summary["status"] == "healthy"
    assert summary["error_count"] == 0
    assert summary["warning_count"] == 0


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
    assert summary["warning_count"] == 1
    assert any(f.category == "capabilities" and f.robot_id == "r1" for f in findings)


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
