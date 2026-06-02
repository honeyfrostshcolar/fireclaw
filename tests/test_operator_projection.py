from fireclaw_core.operator_projection import OperatorEventProjector


def test_projector_translates_rescue_progress_events():
    projector = OperatorEventProjector()
    events = [
        {"type": "task.received", "payload": {"command": "去二楼救人"}},
        {"type": "task.planned", "payload": {"intent": "rescue_victim"}},
        {"type": "safety.decided", "payload": {"status": "allow", "reasons": []}},
        {
            "type": "skill.started",
            "payload": {"skill_name": "navigate_to_floor", "inputs": {"floor": 2}},
        },
        {
            "type": "skill.started",
            "payload": {"skill_name": "search_for_victims", "inputs": {"floor": 2}},
        },
        {"type": "skill.started", "payload": {"skill_name": "assess_victim", "inputs": {}}},
        {"type": "skill.started", "payload": {"skill_name": "report_status", "inputs": {}}},
        {"type": "skill.started", "payload": {"skill_name": "return_to_safe_zone", "inputs": {}}},
        {"type": "task.completed", "payload": {"message": "FireClaw dry-run rescue plan completed."}},
    ]

    messages = [projector.project(event) for event in events]

    assert messages == [
        "已接收任务：去二楼救人。",
        "正在规划救援任务。",
        "安全检查通过。",
        "正在前往2楼。",
        "正在搜索2楼被困人员。",
        "正在评估被困人员状态。",
        "正在向操作员报告现场状态。",
        "正在返回安全区域。",
        "任务完成：FireClaw dry-run rescue plan completed.",
    ]


def test_projector_translates_safety_and_confirmation_events():
    projector = OperatorEventProjector()

    assert (
        projector.project(
            {
                "type": "safety.decided",
                "payload": {"status": "require_confirmation", "reasons": ["高风险技能"]},
            }
        )
        == "该任务需要人工确认：高风险技能"
    )
    assert (
        projector.project({"type": "confirmation.pending", "payload": {"status": "pending"}})
        == "等待人工确认后继续执行。"
    )
    assert (
        projector.project({"type": "confirmation.confirmed", "payload": {"status": "confirmed"}})
        == "人工确认已收到，继续执行。"
    )


def test_projector_translates_failed_attempts_and_terminal_failure():
    projector = OperatorEventProjector()

    assert (
        projector.project(
            {
                "type": "skill.attempted",
                "payload": {
                    "skill_name": "search_for_victims",
                    "attempt_number": 2,
                    "status": "failed",
                    "error": "sensor timeout",
                },
            }
        )
        == "技能 search_for_victims 第 2 次尝试失败：sensor timeout"
    )
    assert (
        projector.project(
            {
                "type": "skill.failed",
                "payload": {"skill_name": "search_for_victims", "error": "sensor timeout"},
            }
        )
        == "技能 search_for_victims 执行失败：sensor timeout"
    )
    assert projector.project({"type": "skill.attempted", "payload": {"status": "succeeded"}}) is None
