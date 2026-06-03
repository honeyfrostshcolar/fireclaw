from fireclaw_core.demo import run_rescue_demo


def test_run_rescue_demo_returns_gateway_trace_with_mock_ros1_action_state(tmp_path):
    result = run_rescue_demo(
        memory_path=str(tmp_path / "demo-memory.jsonl"),
        event_path=str(tmp_path / "demo-events.jsonl"),
        robot_id="demo-ros1",
        session_id="demo-session",
        operator_id="operator-a",
        operator_role="operator",
    )

    assert result["status"] == "succeeded"
    assert result["session_id"] == "demo-session"
    assert result["operator"]["operator_id"] == "operator-a"
    assert result["operator"]["role"] == "operator"
    assert result["control"]["status"] == "allow"
    assert result["robot_state"]["mode"] == "mock_ros1"
    assert result["result"]["execution"]["steps"][0]["output"]["ros1_name"] == "/fireclaw/demo-ros1/navigation"
    assert "operator.identified" in result["event_types"]
    assert "control.decision" in result["event_types"]
    assert "action.requested" in result["event_types"]
    assert "action.succeeded" in result["event_types"]
    assert result["state"]["task"]["status"] == "succeeded"
    assert result["state"]["task"]["action_count"] == 5
    assert len(result["state"]["actions"]) == 5
    assert result["action_events"][0]["payload"]["action_type"] == "navigate_to_floor"

