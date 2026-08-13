from __future__ import annotations

from fireclaw_core.mission.interactive import (
    _submit_and_follow,
    display_event,
    parse_builtin_command,
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
    action, arg = parse_builtin_command("去二楼救人")
    assert action == "submit"
    assert arg == "去二楼救人"


def test_display_event_completed(capsys):
    display_event({"event_type": "task.completed", "robot_id": "robot-1", "task_id": "task-1"})
    captured = capsys.readouterr()
    assert "已完成" in captured.out
    assert "robot-1" in captured.out


def test_submit_and_follow_reconnects_with_cursor_without_duplicate_events(capsys):
    class FakeClient:
        def __init__(self):
            self.stream_cursors = []
            self.stream_count = 0

        def submit_mission(self, command):
            return {"status": "planned", "mission_id": "mission-1"}

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
        "去二楼救人",
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
        def submit_mission(self, command):
            return {"status": "planned", "mission_id": "mission-lost"}

        def stream_mission_events(self, mission_id, *, after_sequence):
            if False:
                yield None

        def get_mission_trace(self, mission_id):
            # The compatibility trace status is failed, but run_status retains
            # the safety-relevant canonical reason.
            return {"status": "failed", "run_status": "lost"}

    _submit_and_follow(
        FakeClient(),
        "前往目标点",
        sleep_fn=lambda _: None,
        max_reconnect_attempts=1,
    )

    assert "[完成] 状态失联（lost）" in capsys.readouterr().out
