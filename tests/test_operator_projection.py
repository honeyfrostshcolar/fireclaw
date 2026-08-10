from fireclaw_core.infra.operator_projection import OperatorEventProjector


def test_projector_translates_plugin_owned_point_navigation_progress():
    projector = OperatorEventProjector()
    events = [
        {"type": "task.received", "payload": {"command": "去坐标 (2.0, 1.5)"}},
        {"type": "task.planned", "payload": {"intent": "point_navigation"}},
        {"type": "safety.decided", "payload": {"status": "allow", "reasons": []}},
        {
            "type": "skill.started",
            "payload": {
                "skill_name": "navigate_to_point",
                "inputs": {"x": 2.0, "y": 1.5, "frame_id": "map"},
                "operator_message": "正在前往 map 坐标系中的目标点 (2.0, 1.5)。",
            },
        },
        {"type": "task.completed", "payload": {"message": "FireClaw execution plan completed."}},
    ]

    messages = [projector.project(event) for event in events]

    assert messages == [
        "已接收任务：去坐标 (2.0, 1.5)。",
        "正在规划机器人任务。",
        "安全检查通过。",
        "正在前往 map 坐标系中的目标点 (2.0, 1.5)。",
        "任务完成：FireClaw execution plan completed.",
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
                    "skill_name": "victim_search",
                    "attempt_number": 2,
                    "status": "failed",
                    "error": "sensor timeout",
                },
            }
        )
        == "技能 victim_search 第 2 次尝试失败：sensor timeout"
    )
    assert (
        projector.project(
            {
                "type": "skill.failed",
                "payload": {"skill_name": "victim_search", "error": "sensor timeout"},
            }
        )
        == "技能 victim_search 执行失败：sensor timeout"
    )
    assert projector.project({"type": "skill.attempted", "payload": {"status": "succeeded"}}) is None


def test_projector_translates_cancel_request_and_final_cancel():
    projector = OperatorEventProjector()

    assert (
        projector.project({"type": "task.cancel_requested", "payload": {"task_id": "task-1"}})
        == "已请求取消任务，等待当前步骤结束。"
    )
    assert projector.project({"type": "task.cancelled", "payload": {}}) == "任务已取消。"
