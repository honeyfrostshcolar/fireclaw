from __future__ import annotations

import pytest

from fireclaw_core.planner.planner import PlannerContext, RuleBasedPlanner


@pytest.mark.parametrize("command", ["去二楼救人", "去2楼救人"])
def test_floor_only_command_requires_single_floor_map_coordinates(
    command: str,
) -> None:
    result = RuleBasedPlanner().plan(command)

    assert result.status == "clarify"
    assert result.plan is None
    assert result.target_floor is None
    assert "map" in result.message
    assert "目标点" in result.message


def test_rule_based_planner_accepts_context_while_requesting_coordinates() -> None:
    context = PlannerContext(
        session_id="session-a",
        turn_index=2,
        recent_records=[],
        skills=[],
    )

    result = RuleBasedPlanner().plan("去二楼救人", context=context)

    assert result.status == "clarify"
    assert result.plan is None


def test_unknown_command_requests_clarification() -> None:
    result = RuleBasedPlanner().plan("随便看看")

    assert result.status == "clarify"
    assert result.plan is None


def test_single_floor_command_generates_only_point_navigation() -> None:
    result = RuleBasedPlanner().plan("去坐标 (2.0, 1.5) 救人")

    assert result.status == "planned"
    assert result.intent == "point_navigation"
    assert result.target_floor is None
    assert result.target_pose == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_point"
    ]
    assert result.plan.steps[0].inputs == result.target_pose


@pytest.mark.parametrize("keyword", ["运行", "调用"])
def test_direct_tool_invocation_generates_one_step_plan(keyword: str) -> None:
    result = RuleBasedPlanner().plan(f"{keyword} echo_policy")

    assert result.status == "planned"
    assert result.intent == "direct_skill_invocation"
    assert len(result.plan.steps) == 1
    assert result.plan.steps[0].skill_name == "echo_policy"
    assert result.plan.steps[0].inputs == {}


def test_direct_tool_invocation_passes_text_payload() -> None:
    result = RuleBasedPlanner().plan("运行 echo_policy 处理 sector-a")

    assert result.status == "planned"
    assert result.plan.steps[0].inputs == {"text": "sector-a"}


def test_direct_tool_invocation_allows_namespaced_name_characters() -> None:
    result = RuleBasedPlanner().plan("执行 nav.policy-v1 输入 sector-a")

    assert result.status == "planned"
    assert result.plan.steps[0].skill_name == "nav.policy-v1"
    assert result.plan.steps[0].inputs == {"text": "sector-a"}


def test_policy_phrase_does_not_synthesize_unregistered_workflow_steps() -> None:
    result = RuleBasedPlanner().plan(
        "去坐标 (2.0, 1.5) 使用 echo_policy"
    )

    assert result.status == "planned"
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_point"
    ]
