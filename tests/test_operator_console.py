from io import StringIO

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.infra.operator_console import run_operator_command


def test_operator_console_prints_human_readable_progress(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="simulator",
            robot_id="operator-test",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    out = StringIO()

    result = run_operator_command(
        "去坐标 (2.0, 1.5) 救人",
        gateway=gateway,
        session_id="operator-a",
        out=out,
        poll_interval_seconds=0.001,
    )

    text = out.getvalue()
    assert result["status"] == "succeeded"
    assert "已接收任务：去坐标 (2.0, 1.5) 救人。" in text
    assert "正在规划救援任务。" in text
    assert "安全检查通过。" in text
    assert "正在前往 map 坐标系中的目标点 (2.0, 1.5)。" in text
    assert "正在搜索当前目标区域的被困人员。" in text
    assert "任务完成：FireClaw dry-run rescue plan completed." in text
    assert '"status"' not in text
