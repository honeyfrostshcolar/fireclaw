from __future__ import annotations

from pathlib import Path

import pytest

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.runtime_identity import GatewayRuntimeIdentity


class PresenceClient:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result or {
            "online": True,
            "last_seen_at": "2026-08-20T00:00:00+00:00",
            "state": {},
        }
        self.calls = 0

    def check_presence(self, entry):
        self.calls += 1
        return {"robot_id": entry.robot_id, **self.result}


def _identity(mode: str, *, robot_id: str = "robot-1") -> GatewayRuntimeIdentity:
    return GatewayRuntimeIdentity.create(
        runtime_mode=mode,
        active_profile_path=Path(__file__),
        profile_sha256="a" * 64,
        robot_id=robot_id,
        deployment_fingerprint="b" * 64,
        observed_at="2026-08-20T00:00:00+00:00",
    )


def _gateway(
    *,
    identity: GatewayRuntimeIdentity | None,
    registry: RobotRegistry | None = None,
    client: PresenceClient | None = None,
) -> MissionGateway:
    registry = registry or RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://127.0.0.1:8765",
                capabilities=("navigation",),
            )
        ]
    )
    client = client or PresenceClient()
    agent = MissionAgent(registry=registry, subagent_client=client)
    return MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        runtime_identity=identity,
    )


def _assert_envelope(value: dict[str, object]) -> None:
    assert set(value) == {
        "schema_version",
        "value",
        "source",
        "observed_at",
        "freshness",
        "evidence_id",
    }
    assert value["schema_version"] == 1
    assert isinstance(value["source"], str) and value["source"]
    assert isinstance(value["observed_at"], str) and value["observed_at"]
    assert value["freshness"] in {"fresh", "stale", "unknown"}
    assert isinstance(value["evidence_id"], str)
    assert value["evidence_id"].startswith("sha256:")


@pytest.mark.parametrize("mode", ["simulation", "real"])
def test_readiness_contract_exposes_profile_mode_and_robot_evidence(mode: str) -> None:
    snapshot = _gateway(identity=_identity(mode)).readiness()

    assert snapshot["schema_version"] == 2
    _assert_envelope(snapshot["runtime_identity"])
    identity = snapshot["runtime_identity"]["value"]
    assert identity["runtime_mode"] == mode
    assert identity["robot_id"] == "robot-1"
    assert identity["active_profile_path"] == str(Path(__file__).resolve())
    for observation in snapshot["observations"].values():
        _assert_envelope(observation)
    assert snapshot["observations"]["physical_stop_confirmed"]["value"] is None
    assert snapshot["observations"]["physical_stop_confirmed"]["freshness"] == "unknown"
    readiness = snapshot["robot_readiness"][0]
    assert readiness["robot_id"] == "robot-1"
    _assert_envelope(readiness["readiness"])
    assert readiness["readiness"]["value"]["status"] == "online"


def test_registry_entry_never_becomes_active_profile_identity_by_inference() -> None:
    snapshot = _gateway(identity=None).readiness()

    assert snapshot["active_robot_id"] is None
    assert snapshot["runtime_mode"] is None
    assert snapshot["active_profile_path"] is None
    assert snapshot["runtime_identity"]["freshness"] == "unknown"
    assert snapshot["runtime_identity"]["value"]["robot_id"] is None
    assert snapshot["robot_readiness"][0]["robot_id"] == "robot-1"


def test_stale_robot_readiness_is_not_promoted_to_online() -> None:
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://127.0.0.1:8765",
                capabilities=("navigation",),
            )
        ],
        heartbeat_timeout_seconds=1.0,
    )
    registry.update_presence("robot-1", "2020-01-01T00:00:00+00:00")
    client = PresenceClient()

    snapshot = _gateway(
        identity=_identity("simulation"),
        registry=registry,
        client=client,
    ).readiness()

    assert client.calls == 0
    readiness = snapshot["robot_readiness"][0]["readiness"]
    assert readiness["value"]["status"] == "stale"
    assert readiness["freshness"] == "stale"


def test_offline_probe_remains_fresh_negative_evidence() -> None:
    client = PresenceClient({"online": False, "error": "connection refused"})
    snapshot = _gateway(
        identity=_identity("simulation"),
        client=client,
    ).readiness()

    readiness = snapshot["robot_readiness"][0]["readiness"]
    assert readiness["value"]["status"] == "offline"
    assert readiness["freshness"] == "fresh"
    assert readiness["source"] == "robot_gateway_state_probe"
