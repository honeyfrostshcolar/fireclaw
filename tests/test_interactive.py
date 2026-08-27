from __future__ import annotations

from fireclaw_core.mission.interactive import (
    _display_readiness,
    _display_plan_preview,
    _submit_and_follow,
    display_event,
    parse_builtin_command,
)
from fireclaw_core.mission.mission_gateway_client import (
    MissionGatewayRequestError,
)


def test_parse_builtin_command_help():
    action, arg = parse_builtin_command("help")
    assert action == "help"
    assert arg is None


def test_parse_builtin_command_cancel():
    action, arg = parse_builtin_command("cancel mission-123")
    assert action == "cancel"
    assert arg == "mission-123"


def test_parse_builtin_command_submit():
    action, arg = parse_builtin_command("前往坐标 (2.0, 1.5) 搜索")
    assert action == "submit"
    assert arg == "前往坐标 (2.0, 1.5) 搜索"


def test_display_event_completed(capsys):
    display_event({"event_type": "task.completed", "robot_id": "robot-1", "task_id": "task-1"})
    captured = capsys.readouterr()
    assert "已完成" in captured.out
    assert "robot-1" in captured.out


def test_display_readiness_prefers_authoritative_robot_probe(capsys):
    _display_readiness({
        "runtime_mode": "simulation",
        "phase": "ready",
        "safe_state": "motion_admitted_idle",
        "robot_readiness": [{
            "robot_id": "gazebo_turtlebot3",
            "readiness": {
                "value": {
                    "status": "online",
                    "declared_capabilities": ["navigation", "patrol"],
                },
                "source": "robot_gateway_state_probe",
                "freshness": "fresh",
            },
        }],
        "fleet_state": {
            "entries": [{
                "robot_id": "gazebo_turtlebot3",
                "is_online": False,
            }],
        },
    })

    output = capsys.readouterr().out
    assert "simulation; ready/motion_admitted_idle" in output
    assert "gazebo_turtlebot3: online; navigation, patrol" in output
    assert "offline" not in output


def test_display_readiness_omits_unknown_safe_state(capsys):
    _display_readiness({
        "runtime_mode": "simulation",
        "phase": "ready",
        "safe_state": "unknown",
        "robot_readiness": [],
        "fleet_state": {"entries": []},
    })

    output = capsys.readouterr().out
    assert "Runtime：simulation; ready\n" in output
    assert "/unknown" not in output


def test_display_plan_preview_includes_structured_target(capsys):
    _display_plan_preview({
        "plan_digest": "sha256:digest",
        "robot_ids": ["gazebo_turtlebot3"],
        "risk_level": "medium",
        "steps": [{
            "index": 1,
            "robot_id": "gazebo_turtlebot3",
            "command": "Navigate to target position",
            "target": {
                "frame_id": "map",
                "pose": {"x": 1.8, "y": -0.1, "yaw": -2.34},
            },
        }],
    })

    output = capsys.readouterr().out
    assert "Navigate to target position" in output
    assert "目标：map (x=1.8, y=-0.1, yaw=-2.34)" in output


def test_display_plan_preview_keeps_xy_target_visible_when_yaw_is_unspecified(capsys):
    _display_plan_preview({
        "plan_digest": "sha256:digest",
        "robot_ids": ["gazebo_turtlebot3"],
        "risk_level": "medium",
        "steps": [{
            "index": 1,
            "robot_id": "gazebo_turtlebot3",
            "command": "前往第一个巡检点",
            "target": {
                "frame_id": "map",
                "pose": {"x": 0.56, "y": 1.8},
            },
        }],
    })

    output = capsys.readouterr().out
    assert "目标：map (x=0.56, y=1.8, yaw=未指定)" in output


def test_submit_and_follow_reconnects_with_cursor_without_duplicate_events(capsys):
    class FakeClient:
        def __init__(self):
            self.stream_cursors = []
            self.stream_count = 0

        def preview_mission(self, command):
            return {
                "status": "preview_ready",
                "artifact_id": "artifact-1",
                "plan_token": "token-1",
                "plan_digest": "sha256:digest-1",
                "status_version": 1,
                "session_id": "plan-session-1",
                "robot_ids": ["robot-1"],
                "risk_level": "medium",
                "steps": [{
                    "index": 1,
                    "robot_id": "robot-1",
                    "command": command,
                }],
            }

        def confirm_plan(self, preview, *, operator_confirmed):
            assert operator_confirmed is True
            return {"status": "accepted", "mission_id": "mission-1"}

        def stream_mission_events(self, mission_id, *, after_sequence):
            self.stream_cursors.append(after_sequence)
            self.stream_count += 1
            if self.stream_count == 1:
                yield {
                    "event_id": "event-1",
                    "sequence": 1,
                    "event_type": "mission.planning",
                    "mission_id": mission_id,
                    "payload": {},
                }
                return
            # A replayed duplicate must be suppressed before the terminal event.
            yield {
                "event_id": "event-1",
                "sequence": 1,
                "event_type": "mission.planning",
                "mission_id": mission_id,
                "payload": {},
            }
            yield {
                "event_id": "event-2",
                "sequence": 2,
                "event_type": "mission.completed",
                "mission_id": mission_id,
                "payload": {},
            }

        def get_mission_trace(self, mission_id):
            return {"status": "running"}

    client = FakeClient()
    sleeps = []

    _submit_and_follow(
        client,
        "前往坐标 (2.0, 1.5) 搜索",
        confirm_fn=lambda preview: True,
        sleep_fn=sleeps.append,
        reconnect_initial_seconds=0.1,
        reconnect_max_seconds=0.2,
        max_reconnect_attempts=3,
    )

    captured = capsys.readouterr()
    assert client.stream_cursors == [0, 1]
    assert captured.out.count("正在规划任务") == 1
    assert "事件流已恢复" in captured.out
    assert "[完成] 已完成（succeeded）" in captured.out
    assert sleeps == [0.1]


def test_submit_and_follow_reports_precise_lost_run_status(capsys):
    class FakeClient:
        def preview_mission(self, command):
            return {
                "status": "preview_ready",
                "artifact_id": "artifact-lost",
                "plan_token": "token-lost",
                "plan_digest": "sha256:digest-lost",
                "status_version": 1,
                "session_id": "plan-session-lost",
                "robot_ids": ["robot-1"],
                "risk_level": "medium",
                "steps": [],
            }

        def confirm_plan(self, preview, *, operator_confirmed):
            assert operator_confirmed is True
            return {"status": "accepted", "mission_id": "mission-lost"}

        def stream_mission_events(self, mission_id, *, after_sequence):
            if False:
                yield None

        def get_mission_run_status(self, mission_id):
            return {"status": "failed", "run_status": "lost"}

        def get_mission_trace(self, mission_id):
            raise AssertionError("bounded run status should avoid the full trace")

    _submit_and_follow(
        FakeClient(),
        "前往坐标 (2.0, 1.5)",
        confirm_fn=lambda preview: True,
        sleep_fn=lambda _: None,
        max_reconnect_attempts=1,
    )

    assert "[完成] 状态失联（lost）" in capsys.readouterr().out


def test_submit_and_follow_does_not_dispatch_without_explicit_confirmation(capsys):
    class FakeClient:
        confirm_calls = 0

        def preview_mission(self, command):
            return {
                "status": "preview_ready",
                "artifact_id": "artifact-1",
                "plan_token": "token-1",
                "plan_digest": "sha256:digest-1",
                "status_version": 1,
                "session_id": "plan-session-1",
                "robot_ids": ["robot-1"],
                "risk_level": "medium",
                "steps": [],
            }

        def confirm_plan(self, preview, *, operator_confirmed):
            self.confirm_calls += 1
            return {"status": "accepted", "mission_id": "unexpected"}

    client = FakeClient()
    _submit_and_follow(
        client,
        "前往坐标 (2.0, 1.5) 搜索",
        confirm_fn=lambda preview: False,
    )

    assert client.confirm_calls == 0
    assert "任务没有下发" in capsys.readouterr().out


def test_submit_and_follow_answers_agent_clarification_before_preview(capsys):
    class FakeClient:
        def __init__(self):
            self.answers = []
            self.confirm_calls = 0

        def preview_mission(self, command):
            return {
                "status": "clarification_required",
                "preview_created": False,
                "planning_session_id": "planning-1",
                "question": "请提供 map 坐标和 yaw。",
                "clarification_round": 1,
                "max_clarification_rounds": 3,
            }

        def answer_planning_clarification(self, session_id, answer):
            self.answers.append((session_id, answer))
            return {
                "status": "preview_ready",
                "artifact_id": "artifact-1",
                "plan_token": "token-1",
                "plan_digest": "sha256:digest-1",
                "status_version": 1,
                "session_id": session_id,
                "robot_ids": ["robot-1"],
                "risk_level": "medium",
                "steps": [],
            }

        def confirm_plan(self, preview, *, operator_confirmed):
            self.confirm_calls += 1
            return {"status": "accepted", "mission_id": "unexpected"}

    client = FakeClient()
    _submit_and_follow(
        client,
        "随便往前走到一个没有障碍物的地方",
        clarify_fn=lambda question: "map 坐标 (1.8, -0.1)，yaw=-2.34",
        confirm_fn=lambda preview: False,
    )

    output = capsys.readouterr().out
    assert "Mission Agent · 追问 1/3" in output
    assert "请提供 map 坐标和 yaw。" in output
    assert client.answers == [(
        "planning-1",
        "map 坐标 (1.8, -0.1)，yaw=-2.34",
    )]
    assert client.confirm_calls == 0
    assert "未确认；任务没有下发" in output


def test_submit_and_follow_streams_planning_elapsed_actions_and_summary(capsys):
    class StreamingClient:
        def stream_preview_mission(self, command):
            yield {
                "event_type": "planning.started",
                "elapsed_seconds": 0.0,
                "payload": {},
            }
            yield {
                "event_type": "mission_agent.turn.started",
                "elapsed_seconds": 0.1,
                "payload": {
                    "iteration": 1,
                    "max_iterations": 4,
                },
            }
            yield {
                "event_type": "mission_agent.stage.completed",
                "elapsed_seconds": 0.2,
                "payload": {
                    "stage": "provider_request",
                    "iteration": 1,
                    "duration_ms": 5100.0,
                    "model": "test-model",
                },
            }
            yield {
                "event_type": "planning.heartbeat",
                "elapsed_seconds": 5.2,
                "payload": {
                    "iteration": 1,
                    "max_iterations": 4,
                },
            }
            yield {
                "event_type": "mission_agent.operation.started",
                "elapsed_seconds": 6.0,
                "payload": {
                    "iteration": 1,
                    "max_iterations": 4,
                    "operation": "inspect_state",
                    "decision_duration_ms": 5900.0,
                },
            }
            yield {
                "event_type": "mission_agent.attempt.completed",
                "elapsed_seconds": 6.1,
                "payload": {
                    "iteration": 1,
                    "max_iterations": 4,
                    "operation": "inspect_state",
                    "outcome": "observed",
                    "duration_ms": 6000.0,
                    "validation_errors": [],
                    "observation": {
                        "kind": "robot_state",
                        "data": {
                            "robot": {
                                "robot_id": "gazebo_turtlebot3",
                                "pose": {"x": 0.65, "y": 0.50, "yaw": -0.08},
                            },
                        },
                    },
                },
            }
            yield {
                "event_type": "planning.result",
                "elapsed_seconds": 6.2,
                "payload": {
                    "status": "preview_ready",
                    "planning_timing": {
                        "by_stage_ms": {
                            "readiness": 3800.0,
                            "provider_request[1]": 5100.0,
                        }
                    },
                    "artifact_id": "artifact-stream",
                    "plan_token": "token-stream",
                    "plan_digest": "sha256:stream",
                    "status_version": 1,
                    "session_id": "planning-stream",
                    "robot_ids": ["gazebo_turtlebot3"],
                    "risk_level": "medium",
                    "steps": [{
                        "index": 1,
                        "robot_id": "gazebo_turtlebot3",
                        "command": command,
                    }],
                    "dropped_progress_events": 0,
                },
            }

    _submit_and_follow(
        StreamingClient(),
        "前往坐标 (0.63, 0.54) 巡检",
        confirm_fn=lambda preview: False,
        show_details=True,
    )

    output = capsys.readouterr().out
    assert "Mission Agent · 规划已启动" in output
    assert "已用时 0.0 秒" in output
    assert "总用时 5.2 秒" in output
    assert "LLM 请求 · 回合 1" in output
    assert "5.100 秒" in output
    assert "就绪检查" in output
    assert "探查机器人状态" in output
    assert "获取到 gazebo_turtlebot3 实测位姿" in output
    assert "封存计划预览已生成" in output
    assert "• Called inspect_state" in output
    assert "• Explored inspect_state" in output
    assert "Mission Agent 思考轨迹" not in output


def test_submit_and_follow_keeps_cli_safe_on_preview_http_error(capsys):
    class FakeClient:
        def preview_mission(self, command):
            raise MissionGatewayRequestError(
                url="http://127.0.0.1:8766/plan-mission",
                status_code=422,
                reason="Unprocessable Entity",
                headers={},
                body=b"",
                payload={
                    "status": "error",
                    "message": "LLM 未返回工具调用。",
                    "preview_created": False,
                },
            )

        def confirm_plan(self, preview, *, operator_confirmed):
            raise AssertionError("an unsealed plan must never be confirmed")

    _submit_and_follow(
        FakeClient(),
        "随便往前走到一个没有障碍物的地方",
        confirm_fn=lambda preview: True,
    )

    output = capsys.readouterr().out
    assert "无法生成任务预览（HTTP 422）：LLM 未返回工具调用。" in output
    assert "封存计划未生成，任务没有下发" in output


def test_display_thinking_trail(capsys):
    from fireclaw_core.mission.interactive import _display_thinking_trail, _Style

    data = {
        "deliberation_attempts": [
            {
                "iteration": 1,
                "operation": "inspect_state",
                "duration_ms": 150.0,
                "outcome": "observed",
                "reason_code": None,
            },
            {
                "iteration": 2,
                "operation": "request_clarification",
                "duration_ms": 200.0,
                "outcome": "completed",
                "reason_code": "target_unbound",
            },
        ],
        "deliberation_observations": [
            {
                "iteration": 1,
                "kind": "robot_state",
                "data": {
                    "robot": {
                        "robot_id": "gazebo_turtlebot3",
                        "pose": {"x": -2.0, "y": -0.5, "yaw": 0.0},
                    }
                },
            }
        ],
    }

    _display_thinking_trail(data, style=_Style(enabled=False))
    output = capsys.readouterr().out

    assert "Mission Agent 思考轨迹" in output
    assert "探查状态" in output
    assert "获取到 gazebo_turtlebot3 实测位姿 (x=-2.0, y=-0.5, yaw=0.0)" in output
    assert "发起操作员澄清" in output
    assert "target_unbound" in output


def test_live_execution_monitor_handle_event(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(style=_Style(enabled=False))
    monitor.handle_event({
        "event_type": "task.received",
        "robot_id": "gazebo_turtlebot3",
        "task_id": "task-1",
    })
    monitor.handle_event({
        "event_type": "action.feedback",
        "robot_id": "gazebo_turtlebot3",
        "payload": {
            "message": "正在前往目标点 (0.63, 0.54)",
            "progress": 0.5,
        },
    })
    monitor.handle_event({
        "event_type": "task.completed",
        "robot_id": "gazebo_turtlebot3",
        "task_id": "task-1",
    })

    output = capsys.readouterr().out
    assert "机器人已接收子任务" not in output
    assert "正在前往目标点 (0.63, 0.54)" in output
    assert "机器人子任务已完成" in output
    assert monitor.message == "[gazebo_turtlebot3] 正在前往目标点 (0.63, 0.54)"


def test_live_monitor_separates_plan_step_agent_round_and_ros_log(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(style=_Style(enabled=False))
    feedback = {
        "event_type": "action.feedback",
        "payload": {
            "robot_id": "gazebo_turtlebot3",
            "task_id": "task-navigation-2",
            "node_id": "task-2",
            "plan_step_index": 2,
            "plan_step_total": 4,
            "plan_step_command": "前往第二个巡检点 (1.0, 0.0)",
            "robot_agent_iteration": 1,
            "tool_name": "navigate_to_point",
            "source_event_type": "robot_agent.decision",
            "message": "规划并尝试前往目标点",
        },
    }
    monitor.handle_event(feedback)
    # Presentation-level coalescing removes only an immediately repeated row;
    # the underlying event ledger remains complete.
    monitor.handle_event(feedback)
    monitor.handle_event({
        "event_type": "action.feedback",
        "payload": {
            **feedback["payload"],
            "source_event_type": "skill.started",
            "message": "正在前往 map 坐标系中的目标点",
        },
    })
    ros_event = {
        "event_type": "ros.log",
        "payload": {
            "robot_id": "gazebo_turtlebot3",
            "task_id": "task-navigation-2",
            "severity": "ERROR",
            "node": "/move_base",
            "message": (
                "\x1b[31mAborting because a valid plan could not be found. "
                "Even after executing all recovery behaviors\x1b[0m"
            ),
        },
    }
    monitor.handle_event(ros_event)
    monitor.handle_event(ros_event)

    output = capsys.readouterr().out
    assert output.count("规划并尝试前往目标点") == 1
    assert output.count("• Called navigate_to_point") == 1
    assert "正在前往 map 坐标系中的目标点" not in output
    assert "• Called navigate_to_point" in output
    assert "• Explored ros.log" in output
    assert "计划步骤 2/4 · 前往第二个巡检点 (1.0, 0.0)" in output
    assert "Agent 回合 1 · Tool navigate_to_point" not in output
    assert "ROS ERROR · /move_base" in output
    assert output.count("Aborting because a valid plan could not be found") == 1
    assert "\x1b[31m" not in output
    assert "\r" not in output

    verbose = LiveExecutionMonitor(
        style=_Style(enabled=False),
        show_details=True,
    )
    verbose.handle_event(feedback)
    verbose_output = capsys.readouterr().out
    assert "Agent 回合 1 · Tool navigate_to_point" in verbose_output


def test_live_monitor_renders_authorization_resume_evidence_and_phase_timing(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(style=_Style(enabled=False))
    monitor.handle_event({
        "event_type": "authorization.requested",
        "timestamp": "2026-08-26T10:00:00+00:00",
        "robot_id": "robot-1",
        "task_id": "task-authorization-1",
        "payload": {
            "authorization": {"request_id": "auth-request-123456"},
        },
    })
    monitor.handle_event({
        "event_type": "task.awaiting_confirmation",
        "timestamp": "2026-08-26T10:00:03+00:00",
        "robot_id": "robot-1",
        "task_id": "task-authorization-1",
        "payload": {"request_id": "auth-request-123456"},
    })
    monitor.handle_event({
        "event_type": "authorization.approved",
        "timestamp": "2026-08-26T10:00:10+00:00",
        "robot_id": "robot-1",
        "task_id": "task-authorization-1",
        "payload": {
            "request_id": "auth-request-123456",
            "authorization_id": "exec-auth-7890",
        },
    })
    monitor.handle_event({
        "event_type": "task.authorized_plan_resumed",
        "timestamp": "2026-08-26T10:00:11+00:00",
        "robot_id": "robot-1",
        "task_id": "task-authorization-1",
        "payload": {
            "operation_id": "operation-1",
            "snapshot_reused": True,
            "robot_agent_reinvoked": False,
            "llm_reinvoked": False,
        },
    })
    monitor.handle_event({
        "event_type": "mission.completed",
        "timestamp": "2026-08-26T10:00:21+00:00",
        "mission_id": "mission-1",
        "payload": {},
    })
    monitor.render_phase_summary()

    output = capsys.readouterr().out
    assert "等待执行授权" in output
    assert "授权请求：auth-request-123456" not in output
    assert "执行授权：exec-auth-7890" not in output
    assert "执行授权通过" in output
    assert "复用原快照；Robot Agent 未重跑；LLM 未重跑" in output
    assert "子任务等待执行授权" not in output
    assert "执行授权已批准" not in output
    assert "阶段耗时" in output
    assert "执行授权 10.0 秒" in output
    assert "各项可能重叠" in output
    assert "事件链路总时长：21.0 秒" in output
    assert "计时来源：网关事件时间戳" in output


def test_live_monitor_verbose_keeps_full_authorization_audit(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(
        style=_Style(enabled=False),
        show_details=True,
    )
    monitor.handle_event({
        "event_type": "authorization.requested",
        "timestamp": "2026-08-26T10:00:00+00:00",
        "robot_id": "robot-1",
        "task_id": "task-auth-verbose",
        "payload": {
            "authorization": {"request_id": "auth-request-verbose"},
        },
    })
    monitor.handle_event({
        "event_type": "authorization.approved",
        "timestamp": "2026-08-26T10:00:02+00:00",
        "robot_id": "robot-1",
        "task_id": "task-auth-verbose",
        "payload": {
            "request_id": "auth-request-verbose",
            "authorization_id": "exec-auth-verbose",
        },
    })
    monitor.handle_event({
        "event_type": "task.resume_scheduled",
        "timestamp": "2026-08-26T10:00:02.1+00:00",
        "robot_id": "robot-1",
        "task_id": "task-auth-verbose",
        "payload": {},
    })
    monitor.handle_event({
        "event_type": "task.resume_started",
        "timestamp": "2026-08-26T10:00:02.2+00:00",
        "robot_id": "robot-1",
        "task_id": "task-auth-verbose",
        "payload": {},
    })
    monitor.handle_event({
        "event_type": "task.authorized_plan_resumed",
        "timestamp": "2026-08-26T10:00:03+00:00",
        "robot_id": "robot-1",
        "task_id": "task-auth-verbose",
        "payload": {
            "snapshot_reused": True,
            "robot_agent_reinvoked": False,
            "llm_reinvoked": False,
        },
    })

    output = capsys.readouterr().out
    assert "授权请求：auth-request-verbose" in output
    assert "执行授权：exec-auth-verbose" in output
    assert "执行授权已批准" in output
    assert "已排入授权恢复队列" in output
    assert "已开始恢复授权任务" in output
    assert "恢复证据：复用快照；Robot Agent 未重跑；LLM 未重跑" in output


def test_live_monitor_does_not_cache_event_facts_across_task_events(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(
        style=_Style(enabled=False),
        show_details=True,
    )
    monitor.handle_event({
        "event_id": "mission-event-1",
        "event_type": "action.feedback",
        "timestamp": "2026-08-26T10:00:05+00:00",
        "task_id": "task-context-1",
        "payload": {
            "task_id": "task-context-1",
            "robot_id": "robot-1",
            "node_id": "node-1",
            "plan_step_index": 1,
            "plan_step_total": 2,
            "plan_step_command": "前往巡检点",
            "tool_name": "navigate_to_point",
            "message": "规划并尝试前往目标点",
            "source_event_type": "robot_agent.decision",
            "source_event_id": "robot-event-1",
            "source_timestamp": "2026-08-26T10:00:01+00:00",
            "reason_code": "safety_gate_terminal",
            "decision_duration_ms": 1000.0,
        },
    })
    monitor.handle_event({
        "event_id": "mission-event-2",
        "event_type": "mission.subtask_dispatched",
        "timestamp": "2026-08-26T10:00:10+00:00",
        "task_id": "task-context-1",
        "payload": {
            "task_id": "task-context-1",
            "status": "accepted",
        },
    })

    assert set(monitor._task_contexts["task-context-1"]) == {
        "robot_id",
        "task_id",
        "node_id",
        "plan_step_index",
        "plan_step_total",
        "plan_step_command",
    }
    output = capsys.readouterr().out
    assert output.count("事件来源：robot_agent.decision") == 1
    assert output.count("Agent 决策耗时：1.0 秒") == 1
    assert "safety_gate_terminal" not in output


def test_live_monitor_renders_deterministic_navigation_success_evidence(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(style=_Style(enabled=False))
    monitor.handle_event({
        "event_type": "skill.succeeded",
        "timestamp": "2026-08-26T10:00:20+00:00",
        "robot_id": "gazebo_turtlebot3",
        "task_id": "task-nav-success",
        "payload": {
            "robot_id": "gazebo_turtlebot3",
            "task_id": "task-nav-success",
            "plan_step_index": 2,
            "plan_step_total": 4,
            "plan_step_command": "前往第二个巡检点",
            "skill_name": "navigate_to_point",
            "tool_name": "navigate_to_point",
            "status": "succeeded",
            "source_event_type": "skill.succeeded",
            "target_pose": {"frame_id": "map", "x": 1.48, "y": -1.58},
            "final_pose": {
                "frame_id": "map",
                "x": 1.513906,
                "y": -1.554588,
                "yaw": -0.068,
            },
            "elapsed_seconds": 25.3,
            "goal_reached": True,
        },
    })
    monitor.handle_event({
        "event_type": "task.completed",
        "timestamp": "2026-08-26T10:00:20.1+00:00",
        "robot_id": "gazebo_turtlebot3",
        "task_id": "task-nav-success",
        "payload": {"status": "completed", "message": "任务已完成。"},
    })

    output = capsys.readouterr().out
    assert "• Ran navigate_to_point" in output
    assert "导航完成，已到达目标" in output
    assert "最终位姿：map (x=1.514, y=-1.555, yaw=-0.068)" in output
    assert "平面误差：0.042 m" in output
    assert "物理动作耗时：25.3 秒" in output
    assert "机器人子任务已完成" in output


def test_live_monitor_phase_summary_does_not_sum_overlapping_spans():
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(style=_Style(enabled=False))
    events = [
        ("authorization.requested", 0.0, {}),
        ("task.resume_scheduled", 1.0, {}),
        ("authorization.approved", 10.0, {}),
        ("task.authorized_plan_resumed", 11.0, {}),
        ("skill.started", 12.0, {"skill_name": "navigate_to_point"}),
        (
            "skill.succeeded",
            32.0,
            {"skill_name": "navigate_to_point", "elapsed_seconds": 20.0},
        ),
        ("mission.completed", 35.0, {}),
    ]
    for event_type, second, payload in events:
        monitor.handle_event({
            "event_type": event_type,
            "timestamp": f"2026-08-26T10:00:{second:06.3f}+00:00",
            "task_id": "task-timing" if not event_type.startswith("mission.") else None,
            "payload": payload,
        })

    lines = monitor.phase_summary_lines()
    rendered = "\n".join(lines)
    assert "执行授权 10.0 秒" in rendered
    assert "恢复/重试 10.0 秒" in rendered
    assert "物理执行 20.0 秒" in rendered
    assert "事件链路总时长：35.0 秒" in rendered
    assert "40.0 秒" not in rendered


def test_terminal_renderer_serializes_concurrent_complete_blocks():
    import io
    import threading

    from fireclaw_core.mission.interactive import MissionTerminalRenderer, _Style

    stream = io.StringIO()
    renderer = MissionTerminalRenderer(
        style=_Style(enabled=False),
        stream=stream,
    )
    barrier = threading.Barrier(3)

    def write_blocks(marker: str) -> None:
        barrier.wait()
        for index in range(20):
            renderer.block(
                f"{marker}-header-{index}",
                [f"{marker}-line-a-{index}", f"{marker}-line-b-{index}"],
            )

    first = threading.Thread(target=write_blocks, args=("A",))
    second = threading.Thread(target=write_blocks, args=("B",))
    first.start()
    second.start()
    barrier.wait()
    first.join(timeout=2)
    second.join(timeout=2)

    blocks = [block for block in stream.getvalue().split("\n\n") if block]
    assert len(blocks) == 40
    for block in blocks:
        marker = "A" if "A-header" in block else "B"
        assert f"{marker}-line-a" in block
        assert f"{marker}-line-b" in block
        assert ("B-line" if marker == "A" else "A-line") not in block


def test_activity_rendering_uses_real_message_and_terminal_cell_width():
    from fireclaw_core.mission.interactive import (
        _Style,
        _display_width,
        _render_activity_line,
        _truncate_to_display_width,
    )

    truncated = _truncate_to_display_width("机器人正在执行 navigation", 9)
    assert _display_width(truncated) <= 9
    assert truncated.endswith("…")

    line = _render_activity_line(
        "⠋",
        "等待网关真实进度事件",
        99.0,
        style=_Style(enabled=False),
        columns=32,
    )
    assert "等待网关真实进度事件" in line
    assert "安全门禁" not in line


def test_planning_monitor_hides_internal_timing_by_default():
    import io

    from fireclaw_core.mission.interactive import (
        MissionPlanningMonitor,
        MissionTerminalRenderer,
        _Style,
    )

    stream = io.StringIO()
    monitor = MissionPlanningMonitor(
        "正在解析任务意图与规划动作...",
        style=_Style(enabled=False),
        renderer=MissionTerminalRenderer(
            style=_Style(enabled=False),
            stream=stream,
        ),
    )
    monitor.start()
    monitor.handle_event({
        "event_type": "mission_agent.turn.started",
        "elapsed_seconds": 0.1,
        "payload": {"iteration": 1, "max_iterations": 4},
    })
    monitor.handle_event({
        "event_type": "mission_agent.stage.completed",
        "elapsed_seconds": 0.2,
        "payload": {
            "stage": "provider_request",
            "iteration": 1,
            "duration_ms": 5100.0,
        },
    })
    monitor.handle_event({
        "event_type": "planning.heartbeat",
        "elapsed_seconds": 5.2,
        "payload": {"iteration": 1, "max_iterations": 4},
    })
    monitor.handle_event({
        "event_type": "planning.result",
        "elapsed_seconds": 5.3,
        "payload": {
            "status": "blocked",
            "planning_timing": {
                "by_stage_ms": {
                    "provider_request[1]": 5100.0,
                    "readiness": 3800.0,
                },
            },
        },
    })

    output = stream.getvalue()
    assert "规划阶段完成" in output
    assert "主要耗时：" in output
    assert "阶段计时" not in output
    assert "仍在生成本回合" not in output


def test_planning_monitor_verbose_restores_muted_details():
    import io

    from fireclaw_core.mission.interactive import (
        MissionPlanningMonitor,
        MissionTerminalRenderer,
        _Style,
    )

    stream = io.StringIO()
    style = _Style(enabled=True)
    monitor = MissionPlanningMonitor(
        "正在解析任务意图与规划动作...",
        style=style,
        renderer=MissionTerminalRenderer(style=style, stream=stream),
        show_details=True,
    )
    monitor.start()
    monitor.handle_event({
        "event_type": "mission_agent.stage.completed",
        "elapsed_seconds": 0.2,
        "payload": {
            "stage": "provider_request",
            "iteration": 1,
            "duration_ms": 5100.0,
        },
    })
    monitor.handle_event({
        "event_type": "planning.result",
        "elapsed_seconds": 5.3,
        "payload": {
            "status": "preview_ready",
            "planning_timing": {
                "by_stage_ms": {"provider_request[1]": 5100.0},
            },
        },
    })

    output = stream.getvalue()
    assert "阶段计时" in output
    assert "阶段汇总" in output
    assert "\033[2m" in output


def test_live_execution_monitor_hides_robot_agent_internals_by_default(capsys):
    from fireclaw_core.mission.interactive import LiveExecutionMonitor, _Style

    monitor = LiveExecutionMonitor(style=_Style(enabled=False))
    monitor.handle_event({
        "event_type": "robot_agent.decision",
        "robot_id": "gazebo_turtlebot3",
        "payload": {"message": "内部模型决策细节"},
    })
    output = capsys.readouterr().out
    assert "内部模型决策细节" not in output

def test_parse_builtin_command_supports_follow_and_cancel_variants():
    assert parse_builtin_command("follow") == ("follow", None)
    assert parse_builtin_command("follow mission-123") == ("follow", "mission-123")
    assert parse_builtin_command("attach mission-456") == ("follow", "mission-456")
    assert parse_builtin_command("cancel") == ("cancel", None)
    assert parse_builtin_command("cancel mission-789") == ("cancel", "mission-789")


def test_follow_mission_stream_renders_events_and_succeeds(capsys):
    from fireclaw_core.mission.interactive import _follow_mission_stream, _Style

    class FakeClient:
        def stream_mission_events(self, mission_id, *, after_sequence=0):
            yield {
                "event_type": "mission.sealed_execution_started",
                "sequence": 1,
            }
            yield {
                "event_type": "action.feedback",
                "payload": {"message": "正在前往巡检点 1 (0.63, 0.54)"},
                "sequence": 2,
            }
            yield {
                "event_type": "mission.completed",
                "status": "succeeded",
                "sequence": 3,
            }

        def get_mission_run_status(self, mission_id):
            return {"status": "succeeded"}

    client = FakeClient()
    _follow_mission_stream(client, "mission-test-follow", style=_Style(enabled=False))
    out = capsys.readouterr().out
    assert "开始执行已确认的封存计划" in out
    assert "正在前往巡检点 1 (0.63, 0.54)" in out
    assert "[完成] 已完成（succeeded）" in out
