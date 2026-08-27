from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.plan_artifact import (
    PlanArtifactError,
    PlanArtifactStore,
    bind_relative_target_yaws,
    build_readiness_binding,
    mission_plan_from_dict,
    require_dispatchable_readiness,
    validate_plan_2d,
    validate_plan_target_binding,
)


PROFILE_REVISION = "sha256:" + "1" * 64
READINESS_REVISION = "sha256:" + "2" * 64


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 20, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def _plan() -> MissionPlan:
    return MissionPlan(
        intent="search",
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
        subtasks=[
            MissionSubtask(
                robot_id="robot-1",
                command="在当前地图坐标 (2.0, 1.5) 搜索受困人员",
                floor=None,
                capability_required="victim_search",
                execution_group=0,
                target={
                    "frame_id": "map",
                    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
                },
            )
        ],
    )


def _issue(store: PlanArtifactStore):
    return store.issue(
        _plan(),
        operator_id="operator-1",
        session_id="plan-session-1",
        robot_ids=["robot-1"],
        runtime_epoch="runtime-1",
        runtime_mode="simulation",
        profile_revision=PROFILE_REVISION,
        readiness_revision=READINESS_REVISION,
        risk_level="medium",
        required_approvals=[
            {
                "kind": "operator_plan_confirmation",
                "scope": "plan_once",
                "status": "required",
            }
        ],
        ttl_seconds=30,
    )


def _consume(store: PlanArtifactStore, issued, **overrides):
    values = {
        "artifact_id": issued.record.artifact_id,
        "plan_digest": issued.record.plan_digest,
        "status_version": 1,
        "operator_id": "operator-1",
        "session_id": "plan-session-1",
        "robot_ids": ["robot-1"],
        "runtime_epoch": "runtime-1",
        "mission_id": "mission-1",
    }
    values.update(overrides)
    return store.consume(issued.token, **values)


def test_plan_artifact_is_one_use_and_never_persists_raw_token(tmp_path):
    path = tmp_path / "plan-artifacts.jsonl"
    store = PlanArtifactStore(path)
    issued = _issue(store)

    consumed = _consume(store, issued)

    assert consumed.status == "consumed"
    assert consumed.status_version == 2
    assert consumed.plan().to_dict() == _plan().to_dict()
    assert issued.token not in path.read_text(encoding="utf-8")
    with pytest.raises(PlanArtifactError, match="already consumed") as replay:
        _consume(store, issued)
    assert replay.value.code == "plan_token_replayed"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"plan_digest": "sha256:" + "9" * 64}, "plan_digest_mismatch"),
        ({"status_version": 2}, "plan_status_version_mismatch"),
        ({"operator_id": "operator-2"}, "plan_operator_mismatch"),
        ({"session_id": "plan-session-2"}, "plan_session_mismatch"),
        ({"robot_ids": ["robot-2"]}, "plan_robot_mismatch"),
        ({"runtime_epoch": "runtime-2"}, "plan_runtime_drift"),
    ],
)
def test_plan_artifact_binding_mismatch_fails_closed(overrides, code):
    store = PlanArtifactStore()
    issued = _issue(store)

    with pytest.raises(PlanArtifactError) as caught:
        _consume(store, issued, **overrides)

    assert caught.value.code == code
    assert store.get(issued.record.artifact_id).status == "pending"


def test_plan_token_tamper_and_expiry_fail_closed():
    clock = _Clock()
    store = PlanArtifactStore(clock=clock)
    issued = _issue(store)
    tampered = issued.token[:-1] + ("A" if issued.token[-1] != "A" else "B")

    with pytest.raises(PlanArtifactError) as caught:
        store.validate_pending(
            tampered,
            artifact_id=issued.record.artifact_id,
            plan_digest=issued.record.plan_digest,
            status_version=1,
            operator_id="operator-1",
            session_id="plan-session-1",
            robot_ids=["robot-1"],
            runtime_epoch="runtime-1",
        )
    assert caught.value.code == "plan_token_invalid"

    clock.now += timedelta(seconds=31)
    with pytest.raises(PlanArtifactError) as expired:
        _consume(store, issued)
    assert expired.value.code == "plan_token_expired"
    assert store.get(issued.record.artifact_id).status == "expired"


def test_consumed_state_survives_restart_and_rejects_replay(tmp_path):
    path = tmp_path / "plan-artifacts.jsonl"
    issued = _issue(PlanArtifactStore(path))
    first = PlanArtifactStore(path)
    _consume(first, issued)

    restarted = PlanArtifactStore(path)
    with pytest.raises(PlanArtifactError) as caught:
        _consume(restarted, issued)
    assert caught.value.code == "plan_token_replayed"


def test_store_rejects_plan_payload_tamper(tmp_path):
    path = tmp_path / "plan-artifacts.jsonl"
    issued = _issue(PlanArtifactStore(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["plan"]["subtasks"][0]["command"] = "tampered command"
    path.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(PlanArtifactError) as caught:
        PlanArtifactStore(path)
    assert caught.value.code == "plan_artifact_store_corrupt"
    assert issued.record.plan_digest in json.dumps(raw)


def _readiness(
    *,
    status="online",
    source="robot_gateway_state_probe",
    freshness="fresh",
    admission_phase="ready",
    admission_freshness="fresh",
    admission_safe_state="unknown",
    safe_state_freshness="fresh",
    reason_code="ready",
):
    return {
        "observations": {
            "admission_phase": {
                "value": admission_phase,
                "source": "fleet_doctor_operator_projection",
                "freshness": admission_freshness,
                "observed_at": "2026-08-20T00:00:00+00:00",
                "evidence_id": "sha256:volatile-a",
            },
            "admission_safe_state": {
                "value": admission_safe_state,
                "source": "fleet_doctor_operator_projection",
                "freshness": safe_state_freshness,
                "observed_at": "2026-08-20T00:00:00+00:00",
                "evidence_id": "sha256:volatile-b",
            },
            "reason_code": {
                "value": reason_code,
                "source": "fleet_doctor_operator_projection",
                "freshness": "fresh",
                "observed_at": "2026-08-20T00:00:00+00:00",
                "evidence_id": "sha256:volatile-c",
            },
        },
        "robot_readiness": [
            {
                "robot_id": "robot-1",
                "readiness": {
                    "value": {
                        "status": status,
                        "last_seen_at": "2026-08-20T00:00:00+00:00",
                        "declared_capabilities": ["victim_search"],
                    },
                    "source": source,
                    "freshness": freshness,
                    "observed_at": "2026-08-20T00:00:00+00:00",
                    "evidence_id": "sha256:volatile-r",
                },
            }
        ],
    }


def test_readiness_revision_ignores_timestamps_but_detects_semantic_drift():
    first = _readiness()
    second = _readiness()
    second["observations"]["admission_phase"]["observed_at"] = "later"
    second["observations"]["admission_phase"]["evidence_id"] = "sha256:new"
    second["robot_readiness"][0]["readiness"]["value"]["last_seen_at"] = "later"

    first_binding = build_readiness_binding(first, robot_ids=["robot-1"])
    second_binding = build_readiness_binding(second, robot_ids=["robot-1"])
    drifted = build_readiness_binding(
        _readiness(status="offline"),
        robot_ids=["robot-1"],
    )

    assert first_binding.revision == second_binding.revision
    assert first_binding.revision != drifted.revision
    require_dispatchable_readiness(first_binding)
    with pytest.raises(PlanArtifactError) as caught:
        require_dispatchable_readiness(drifted)
    assert caught.value.code == "robot_not_ready"


@pytest.mark.parametrize(
    ("readiness", "expected_reason"),
    [
        (
            _readiness(
                admission_phase="blocked",
                reason_code="runtime_storage_unavailable",
            ),
            "runtime_storage_unavailable",
        ),
        (_readiness(admission_freshness="stale"), None),
        (
            _readiness(
                admission_phase="degraded",
                reason_code="fleet_doctor_report_invalid",
            ),
            "fleet_doctor_report_invalid",
        ),
    ],
)
def test_dispatch_rejects_blocked_or_stale_fleet_admission(
    readiness,
    expected_reason,
):
    binding = build_readiness_binding(readiness, robot_ids=["robot-1"])

    with pytest.raises(PlanArtifactError) as caught:
        require_dispatchable_readiness(binding)

    assert caught.value.code == "admission_not_ready"
    if expected_reason is not None:
        assert expected_reason in str(caught.value)


def test_dispatch_allows_fresh_non_blocking_fleet_warnings():
    binding = build_readiness_binding(
        _readiness(
            admission_phase="degraded",
            reason_code="fleet_doctor_warnings",
        ),
        robot_ids=["robot-1"],
    )

    require_dispatchable_readiness(binding)


def test_preview_can_bind_blocked_admission_without_dispatching():
    binding = build_readiness_binding(
        _readiness(admission_phase="blocked"),
        robot_ids=["robot-1"],
    )

    require_dispatchable_readiness(binding, require_admission=False)


def test_mission_plan_parser_rejects_unknown_fields():
    raw = _plan().to_dict()
    raw["client_override"] = True

    with pytest.raises(PlanArtifactError) as caught:
        mission_plan_from_dict(raw)

    assert caught.value.code == "plan_payload_invalid"


def test_current_sealed_plan_contract_rejects_floor_only_targets():
    floor_plan = MissionPlan(
        intent="search",
        command="legacy floor command",
        subtasks=[
            MissionSubtask(
                robot_id="robot-1",
                command="legacy floor subtask",
                floor=2,
                capability_required="victim_search",
            )
        ],
    )

    errors = validate_plan_2d(floor_plan)

    assert any("only 2D map poses are supported" in error for error in errors)
    assert any("target frame must be 'map'" in error for error in errors)


def test_target_binding_accepts_explicit_coordinate_and_yaw():
    assert validate_plan_target_binding(_plan()) == []

    base = _plan()
    explicit_yaw_plan = replace(
        base,
        command="前往地图坐标 (2.0, 1.5)，保持朝向 yaw=-2.34 弧度",
        subtasks=[replace(
            base.subtasks[0],
            target={
                "frame_id": "map",
                "pose": {"x": 2.0, "y": 1.5, "yaw": -2.34},
            },
        )],
    )

    assert validate_plan_target_binding(explicit_yaw_plan) == []


def test_target_binding_rejects_llm_invented_coordinate():
    base = _plan()
    plan = replace(
        base,
        command="随便往前走到一个没有障碍物的地方",
        subtasks=[replace(
            base.subtasks[0],
            target={
                "frame_id": "map",
                "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            },
        )],
    )

    errors = validate_plan_target_binding(plan)

    assert any("not bound to an explicit coordinate" in error for error in errors)


def test_target_binding_rejects_changed_coordinate_or_unstated_heading():
    base = _plan()
    plan = replace(
        base,
        subtasks=[replace(
            base.subtasks[0],
            target={
                "frame_id": "map",
                "pose": {"x": 9.0, "y": 9.0, "yaw": 1.0},
            },
        )],
    )

    errors = validate_plan_target_binding(plan)

    assert any("not bound to an explicit coordinate" in error for error in errors)
    assert any("without an explicit operator yaw" in error for error in errors)


def test_relative_pose_binding_is_robot_scoped_and_matches_live_yaw():
    base = _plan()
    plan = replace(
        base,
        command="返回当前点",
        subtasks=[replace(
            base.subtasks[0],
            target={
                "frame_id": "map",
                "pose": {"x": 1.0, "y": 2.0, "yaw": 0.3},
            },
        )],
    )
    other_robot_state = {
        "robots": [{
            "robot_id": "robot-2",
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.3, "frame_id": "map"},
        }]
    }
    wrong_yaw_state = {
        "robots": [{
            "robot_id": base.subtasks[0].robot_id,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.4, "frame_id": "map"},
        }]
    }
    matching_state = {
        "robots": [{
            "robot_id": base.subtasks[0].robot_id,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.3, "frame_id": "map"},
        }]
    }

    assert any(
        "not bound to an explicit coordinate" in error
        for error in validate_plan_target_binding(plan, other_robot_state)
    )
    assert any(
        "does not match live pose yaw" in error
        for error in validate_plan_target_binding(plan, wrong_yaw_state)
    )
    assert validate_plan_target_binding(plan, matching_state) == []


def test_relative_pose_default_yaw_binds_to_live_heading():
    base = _plan()
    plan = replace(
        base,
        command="返回现在的位置",
        subtasks=[replace(
            base.subtasks[0],
            target={
                "frame_id": "map",
                "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            },
        )],
    )
    state = {
        "robots": [{
            "robot_id": base.subtasks[0].robot_id,
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.4, "frame_id": "map"},
        }]
    }

    bound = bind_relative_target_yaws(plan, state)

    assert bound.subtasks[0].target["pose"]["yaw"] == 0.4
    assert validate_plan_target_binding(bound, state) == []
