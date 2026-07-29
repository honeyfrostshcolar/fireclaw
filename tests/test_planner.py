from fireclaw_core.planner.planner import PlannerContext, RuleBasedPlanner


def test_rescue_command_with_chinese_floor_generates_five_step_plan():
    result = RuleBasedPlanner().plan("去二楼救人")

    assert result.status == "planned"
    assert result.intent == "rescue_victim"
    assert result.target_floor == 2
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_floor",
        "search_for_victims",
        "assess_victim",
        "report_status",
        "return_to_safe_zone",
    ]
    assert result.plan.steps[0].inputs == {"floor": 2}


def test_rule_based_planner_accepts_planner_context():
    context = PlannerContext(
        session_id="session-a",
        turn_index=2,
        recent_records=[],
        skills=[],
    )

    result = RuleBasedPlanner().plan("去二楼救人", context=context)

    assert result.status == "planned"
    assert result.target_floor == 2


def test_rescue_command_with_digit_floor_generates_plan():
    result = RuleBasedPlanner().plan("去2楼救人")

    assert result.status == "planned"
    assert result.target_floor == 2


def test_unknown_command_requests_clarification():
    result = RuleBasedPlanner().plan("随便看看")

    assert result.status == "clarify"
    assert result.plan is None
    assert "目标点" in result.message


def test_single_floor_rescue_command_generates_point_navigation_plan():
    result = RuleBasedPlanner().plan("去坐标 (2.0, 1.5) 救人")

    assert result.status == "planned"
    assert result.target_floor is None
    assert result.target_pose == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_point",
        "search_for_victims",
        "assess_victim",
        "report_status",
        "return_to_safe_zone",
    ]
    assert result.plan.steps[0].inputs == result.target_pose
    assert result.plan.steps[1].inputs == {}


def test_direct_skill_invocation_with_run_keyword_generates_one_step_plan():
    result = RuleBasedPlanner().plan("运行 echo_policy")

    assert result.status == "planned"
    assert result.intent == "direct_skill_invocation"
    assert result.plan.intent == "direct_skill_invocation"
    assert len(result.plan.steps) == 1
    assert result.plan.steps[0].skill_name == "echo_policy"
    assert result.plan.steps[0].inputs == {}


def test_direct_skill_invocation_with_call_keyword_generates_one_step_plan():
    result = RuleBasedPlanner().plan("调用 echo_policy")

    assert result.status == "planned"
    assert result.intent == "direct_skill_invocation"
    assert result.plan.steps[0].skill_name == "echo_policy"


def test_direct_skill_invocation_passes_text_payload():
    result = RuleBasedPlanner().plan("运行 echo_policy 处理 二楼")

    assert result.status == "planned"
    assert result.intent == "direct_skill_invocation"
    assert result.plan.steps[0].skill_name == "echo_policy"
    assert result.plan.steps[0].inputs == {"text": "二楼"}


def test_direct_skill_invocation_allows_dots_and_dashes_in_skill_name():
    result = RuleBasedPlanner().plan("执行 nav.policy-v1 输入 sector-a")

    assert result.status == "planned"
    assert result.plan.steps[0].skill_name == "nav.policy-v1"
    assert result.plan.steps[0].inputs == {"text": "sector-a"}


def test_rescue_command_with_policy_skill_inserts_policy_before_rescue_steps():
    command = "去二楼救人 使用 echo_policy"
    result = RuleBasedPlanner().plan(command)

    assert result.status == "planned"
    assert result.intent == "rescue_victim"
    assert result.target_floor == 2
    assert [step.skill_name for step in result.plan.steps] == [
        "echo_policy",
        "navigate_to_floor",
        "search_for_victims",
        "assess_victim",
        "report_status",
        "return_to_safe_zone",
    ]
    assert result.plan.steps[0].inputs == {"floor": 2, "command": command}


def test_rescue_command_with_navigation_policy_phrase_inserts_policy():
    command = "去二楼救人 导航策略用 echo_policy"
    result = RuleBasedPlanner().plan(command)

    assert result.status == "planned"
    assert result.target_floor == 2
    assert result.plan.steps[0].skill_name == "echo_policy"
    assert result.plan.steps[0].inputs == {"floor": 2, "command": command}


def test_rescue_command_with_digit_floor_and_short_policy_phrase_inserts_policy():
    command = "去2楼救人 用 echo_policy"
    result = RuleBasedPlanner().plan(command)

    assert result.status == "planned"
    assert result.target_floor == 2
    assert result.plan.steps[0].skill_name == "echo_policy"
