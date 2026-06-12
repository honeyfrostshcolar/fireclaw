from fireclaw_core.approval.approval_store import ApprovalRequest, JsonlApprovalStore


def test_approval_store_create_and_get(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")

    request = store.create(
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:00+00:00",
    )

    assert request.status == "pending"
    assert request.mission_id == "mission-1"
    assert request.is_terminal is False

    loaded = store.get(request.request_id)
    assert loaded == request


def test_approval_store_approve(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")

    request = store.create(
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:00+00:00",
    )

    approved = store.approve(
        request.request_id,
        decided_by="commander-1",
        decided_at="2026-06-08T01:00:05+00:00",
    )

    assert approved.status == "approved"
    assert approved.decided_by == "commander-1"
    assert approved.decided_at == "2026-06-08T01:00:05+00:00"
    assert approved.is_terminal is True

    loaded = store.get(request.request_id)
    assert loaded.status == "approved"


def test_approval_store_deny(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")

    request = store.create(
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:00+00:00",
    )

    denied = store.deny(
        request.request_id,
        decided_by="commander-1",
        reason="结构不安全",
        decided_at="2026-06-08T01:00:05+00:00",
    )

    assert denied.status == "denied"
    assert denied.decided_by == "commander-1"
    assert denied.reason == "结构不安全"
    assert denied.is_terminal is True

    loaded = store.get(request.request_id)
    assert loaded.status == "denied"


def test_approval_store_pending_requests(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")

    r1 = store.create(
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:00+00:00",
    )
    r2 = store.create(
        mission_id="mission-1",
        action="use_ladder",
        risk_level="medium",
        command="架梯上三楼",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:01+00:00",
    )
    store.approve(
        r1.request_id,
        decided_by="commander-1",
        decided_at="2026-06-08T01:00:05+00:00",
    )

    pending = store.pending_requests()
    assert len(pending) == 1
    assert pending[0].request_id == r2.request_id

    pending_mission = store.pending_requests(mission_id="mission-1")
    assert len(pending_mission) == 1

    pending_other = store.pending_requests(mission_id="mission-999")
    assert len(pending_other) == 0


def test_approval_store_list_filters_by_mission_and_status(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")

    store.create(
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:00+00:00",
    )
    r2 = store.create(
        mission_id="mission-2",
        action="use_ladder",
        risk_level="medium",
        command="架梯上三楼",
        requested_by="operator-2",
        created_at="2026-06-08T01:00:01+00:00",
    )
    store.approve(
        r2.request_id,
        decided_by="commander-1",
        decided_at="2026-06-08T01:00:05+00:00",
    )

    all_requests = store.list_requests()
    assert len(all_requests) == 2

    m1 = store.list_requests(mission_id="mission-1")
    assert len(m1) == 1
    assert m1[0].mission_id == "mission-1"

    approved = store.list_requests(status="approved")
    assert len(approved) == 1
    assert approved[0].status == "approved"

    both = store.list_requests(mission_id="mission-2", status="approved")
    assert len(both) == 1

    neither = store.list_requests(mission_id="mission-1", status="approved")
    assert len(neither) == 0


def test_approval_store_approve_terminal_is_noop(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")

    request = store.create(
        mission_id="mission-1",
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at="2026-06-08T01:00:00+00:00",
    )
    store.approve(
        request.request_id,
        decided_by="commander-1",
        decided_at="2026-06-08T01:00:05+00:00",
    )

    result = store.approve(
        request.request_id,
        decided_by="commander-2",
        decided_at="2026-06-08T01:00:10+00:00",
    )

    assert result is None
    loaded = store.get(request.request_id)
    assert loaded.decided_by == "commander-1"


def test_approval_store_empty_for_missing_file(tmp_path):
    store = JsonlApprovalStore(tmp_path / "nonexistent.jsonl")

    assert store.get("anything") is None
    assert store.list_requests() == []
    assert store.pending_requests() == []


def test_approval_store_skips_corrupt_lines(tmp_path):
    path = tmp_path / "approvals.jsonl"
    path.write_text(
        "not valid json\n"
        '{"request_id": "r1", "mission_id": "m1", "action": "enter", '
        '"risk_level": "high", "command": "cmd", "requested_by": "op", '
        '"status": "pending", "created_at": "2026-06-08T01:00:00+00:00"}\n'
        '{"incomplete": true}\n',
        encoding="utf-8",
    )

    store = JsonlApprovalStore(path)

    requests = store.list_requests()
    assert len(requests) == 1
    assert requests[0].request_id == "r1"
