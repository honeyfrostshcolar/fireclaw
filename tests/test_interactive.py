from __future__ import annotations

from fireclaw_core.interactive import display_event, parse_builtin_command


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
    assert "completed" in captured.out
    assert "robot-1" in captured.out
