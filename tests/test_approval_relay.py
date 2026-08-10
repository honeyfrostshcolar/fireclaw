from __future__ import annotations

from datetime import datetime, timezone

from fireclaw_core.approval.approval_relay import (
    ApprovalRelay,
    InMemoryApprovalRelay,
    RelayDeliveryRecord,
)
from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.approval.approval_runtime import ApprovalRuntime
from fireclaw_core.gateway.control import OperatorContext
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeSubagentClient:
    def __init__(self):
        self.calls: list[tuple] = []

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {"status": "accepted", "task_id": f"task-{entry.robot_id}", "robot_id": entry.robot_id}


class FakePlanner:
    def plan(self, command: str, *, context: MissionPlannerContext) -> MissionPlanningResult:
        available_ids = [e.robot_id for e in context.available_robots]
        if not available_ids:
            return MissionPlanningResult(
                status="no_robots", message="No online robots available.",
                intent=None, plan=None,
            )
        return MissionPlanningResult(
            status="planned", message="Plan created.", intent="search",
            plan=MissionPlan(
                intent="search", command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=available_ids[0], command=command,
                        floor=2, capability_required="victim_search",
                        execution_group=0,
                    )
                ],
            ),
        )


def _make_registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1", base_url="http://robot-1.local:8765",
            capabilities=("victim_search",),
        ),
    ])


def _make_gateway(
    *,
    mission_agent: MissionAgent,
    registry: RobotRegistry | None = None,
    approval_runtime: ApprovalRuntime | None = None,
    approval_relay: ApprovalRelay | None = None,
) -> MissionGateway:
    config = MissionGatewayConfig(port=0)
    return MissionGateway(
        config,
        mission_agent=mission_agent,
        registry=registry or _make_registry(),
        subagent_client=FakeSubagentClient(),
        approval_runtime=approval_runtime,
        approval_relay=approval_relay,
    )


# ---------------------------------------------------------------------------
# Tests: InMemoryApprovalRelay
# ---------------------------------------------------------------------------


def test_in_memory_relay_delivers_pending_approval():
    """Relay receives a pending approval and returns a success record."""
    relay = InMemoryApprovalRelay()
    record = relay.deliver_pending_approval(
        request_id="req-1",
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        channel="console",
        operator_id="op-1",
    )

    assert isinstance(record, RelayDeliveryRecord)
    assert record.request_id == "req-1"
    assert record.channel == "console"
    assert record.operator_id == "op-1"
    assert record.success is True
    assert record.error is None
    assert record.delivered_at is not None
    assert record.dedup_key == "req-1:console"

    # Delivery is stored
    assert relay.get_delivery("req-1:console") is not None
    assert len(relay.deliveries) == 1


def test_in_memory_relay_is_idempotent_by_request_id():
    """Delivering the same request+channel twice returns the same record (idempotent)."""
    relay = InMemoryApprovalRelay()

    record1 = relay.deliver_pending_approval(
        request_id="req-1",
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        channel="console",
        operator_id="op-1",
    )
    record2 = relay.deliver_pending_approval(
        request_id="req-1",
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        channel="console",
        operator_id="op-1",
    )

    # Same dedup_key, same record, no duplicate
    assert record1.dedup_key == record2.dedup_key
    assert record1.delivered_at == record2.delivered_at
    assert len(relay.deliveries) == 1


def test_in_memory_relay_different_channels_are_separate():
    """Same request_id but different channel creates a separate delivery."""
    relay = InMemoryApprovalRelay()

    relay.deliver_pending_approval(
        request_id="req-1",
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        channel="console",
        operator_id="op-1",
    )
    relay.deliver_pending_approval(
        request_id="req-1",
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        channel="webhook",
        operator_id="op-2",
    )

    assert len(relay.deliveries) == 2
    assert relay.get_delivery("req-1:console") is not None
    assert relay.get_delivery("req-1:webhook") is not None


def test_relay_does_not_deliver_resolved_approval():
    """Relay returns None when asked about a non-existent dedup key."""
    relay = InMemoryApprovalRelay()
    assert relay.get_delivery("nonexistent:console") is None


def test_relay_delivery_record_is_frozen():
    """RelayDeliveryRecord is immutable."""
    record = RelayDeliveryRecord(
        request_id="req-1",
        channel="console",
        operator_id="op-1",
        delivered_at=datetime.now(timezone.utc).isoformat(),
        success=True,
        error=None,
        dedup_key="req-1:console",
    )
    try:
        record.request_id = "other"  # type: ignore[misc]
        assert False, "Should have raised"
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Tests: Gateway wiring
# ---------------------------------------------------------------------------


def test_mission_gateway_delivers_to_relay_when_configured(tmp_path):
    """Gateway calls relay.deliver_pending_approval after action=request returns pending."""
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    approval_runtime = ApprovalRuntime(approval_store, token_ttl_seconds=60)
    relay = InMemoryApprovalRelay()
    agent = MissionAgent(
        registry=registry, subagent_client=client,
        approval_store=approval_store,
    )
    gw = _make_gateway(
        mission_agent=agent,
        registry=registry,
        approval_runtime=approval_runtime,
        approval_relay=relay,
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
        "relay": {"channel": "console", "operator_id": "op-1"},
    })

    assert result["status"] == "pending"
    request_id = result["request"]["request_id"]

    # Relay should have received the delivery
    assert len(relay.deliveries) == 1
    delivery = relay.get_delivery(f"{request_id}:console")
    assert delivery is not None
    assert delivery.request_id == request_id
    assert delivery.success is True


def test_mission_gateway_relay_failure_does_not_block_approval(tmp_path):
    """If relay raises, approval remains pending and error is recorded."""
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    approval_runtime = ApprovalRuntime(approval_store, token_ttl_seconds=60)

    class BrokenRelay:
        def deliver_pending_approval(self, **kwargs):
            raise ConnectionError("network down")

    relay = BrokenRelay()
    agent = MissionAgent(
        registry=registry, subagent_client=client,
        approval_store=approval_store,
    )
    gw = _make_gateway(
        mission_agent=agent,
        registry=registry,
        approval_runtime=approval_runtime,
        approval_relay=relay,  # type: ignore[arg-type]
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
        "relay": {"channel": "console", "operator_id": "op-1"},
    })

    # Approval must still be pending despite relay failure
    assert result["status"] == "pending"
    assert result["request"]["request_id"] is not None

    # Relay error is recorded in the response
    assert "relay_error" in result
    assert "network down" in result["relay_error"]


def test_mission_gateway_no_relay_when_no_relay_configured(tmp_path):
    """Without a relay adapter, approval proceeds without relay delivery."""
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    approval_runtime = ApprovalRuntime(approval_store, token_ttl_seconds=60)
    agent = MissionAgent(
        registry=registry, subagent_client=client,
        approval_store=approval_store,
    )
    gw = _make_gateway(
        mission_agent=agent,
        registry=registry,
        approval_runtime=approval_runtime,
        approval_relay=None,
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
        "relay": {"channel": "console", "operator_id": "op-1"},
    })

    assert result["status"] == "pending"
    assert "relay_error" not in result


def test_mission_gateway_no_relay_when_no_relay_metadata(tmp_path):
    """Without relay metadata in payload, relay is not called even if configured."""
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    approval_runtime = ApprovalRuntime(approval_store, token_ttl_seconds=60)
    relay = InMemoryApprovalRelay()
    agent = MissionAgent(
        registry=registry, subagent_client=client,
        approval_store=approval_store,
    )
    gw = _make_gateway(
        mission_agent=agent,
        registry=registry,
        approval_runtime=approval_runtime,
        approval_relay=relay,
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
        # No "relay" key
    })

    assert result["status"] == "pending"
    assert len(relay.deliveries) == 0


def test_mission_gateway_accepts_approval_relay_param():
    """MissionGateway accepts an optional approval_relay parameter."""
    registry = _make_registry()
    agent = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=JsonlApprovalStore("/dev/null"),
    )
    relay = InMemoryApprovalRelay()
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=FakeSubagentClient(),
        approval_relay=relay,
    )
    assert gw.approval_relay is relay


def test_mission_gateway_default_relay_is_none():
    """MissionGateway defaults to no relay."""
    registry = _make_registry()
    agent = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=JsonlApprovalStore("/dev/null"),
    )
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=FakeSubagentClient(),
    )
    assert gw.approval_relay is None
