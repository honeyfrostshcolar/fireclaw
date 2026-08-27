from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fireclaw_core.mission.planning_dialogue import (
    PlanningDialogueError,
    PlanningDialogueStore,
)


def test_dialogue_binds_answer_but_excludes_agent_question_from_command():
    store = PlanningDialogueStore()
    started = store.begin(
        operator_id="operator-1",
        command="随便往前走到一个没有障碍物的地方",
        target_robot="robot-1",
    )
    pending = store.record_question(
        started.session_id,
        operator_id="operator-1",
        question="请提供坐标，例如 (9.0, 9.0)。",
        reason_code="navigation_target_required",
    )
    answered = store.answer(
        pending.session_id,
        operator_id="operator-1",
        answer="使用 map 坐标 (1.8, -0.1)，yaw=-2.34。",
    )

    command = answered.canonical_operator_command()
    assert "(1.8, -0.1)" in command
    assert "(9.0, 9.0)" not in command
    assert answered.clarification_context()[0]["answer_source"] == (
        "authenticated_operator"
    )


def test_dialogue_rejects_answer_from_different_operator():
    store = PlanningDialogueStore()
    started = store.begin(
        operator_id="operator-1",
        command="去安全位置",
        target_robot=None,
    )
    store.record_question(
        started.session_id,
        operator_id="operator-1",
        question="具体去哪里？",
        reason_code=None,
    )

    with pytest.raises(PlanningDialogueError) as caught:
        store.answer(
            started.session_id,
            operator_id="operator-2",
            answer="去 (1.0, 1.0)",
        )

    assert caught.value.code == "planning_session_operator_mismatch"


def test_new_command_supersedes_prior_pending_dialogue():
    store = PlanningDialogueStore()
    first = store.begin(
        operator_id="operator-1",
        command="第一个任务",
        target_robot=None,
    )
    second = store.begin(
        operator_id="operator-1",
        command="第二个任务",
        target_robot=None,
    )

    with pytest.raises(PlanningDialogueError) as caught:
        store.get(first.session_id, operator_id="operator-1")

    assert caught.value.code == "planning_session_not_found"
    assert store.get(second.session_id, operator_id="operator-1") == second


def test_dialogue_expires_fail_closed():
    now = [datetime(2026, 8, 22, tzinfo=timezone.utc)]
    store = PlanningDialogueStore(
        ttl_seconds=30,
        clock=lambda: now[0],
    )
    started = store.begin(
        operator_id="operator-1",
        command="去目标点",
        target_robot=None,
    )
    now[0] += timedelta(seconds=31)

    with pytest.raises(PlanningDialogueError) as caught:
        store.get(started.session_id, operator_id="operator-1")

    assert caught.value.code == "planning_session_expired"


def test_dialogue_limits_clarification_rounds():
    store = PlanningDialogueStore(max_rounds=1)
    started = store.begin(
        operator_id="operator-1",
        command="去安全位置",
        target_robot=None,
    )
    store.record_question(
        started.session_id,
        operator_id="operator-1",
        question="请提供目标。",
        reason_code=None,
    )
    store.answer(
        started.session_id,
        operator_id="operator-1",
        answer="你来决定。",
    )

    with pytest.raises(PlanningDialogueError) as caught:
        store.record_question(
            started.session_id,
            operator_id="operator-1",
            question="仍然缺少目标。",
            reason_code=None,
        )

    assert caught.value.code == "clarification_round_limit"
