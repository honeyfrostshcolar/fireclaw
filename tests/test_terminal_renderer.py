from __future__ import annotations

import io

from fireclaw_core.mission.terminal_renderer import TerminalAgentRenderer


def test_terminal_agent_renderer_separates_semantic_activity_levels():
    stream = io.StringIO()
    renderer = TerminalAgentRenderer(
        stream=stream,
        force_terminal=False,
        width=120,
    )

    renderer.render_thought(
        "正在组装上下文",
        details=["输入 token：4105"],
    )
    renderer.render_tool_call(
        "inspect_state",
        phase="called",
        arguments={"kind": "robot_state"},
    )
    renderer.render_tool_call(
        "inspect_state",
        phase="explored",
        summary="已获得机器人位姿",
    )
    renderer.render_tool_call(
        "propose_task_graph",
        phase="ran",
        output={"status": "accepted"},
    )
    renderer.render_final_response(
        "封存计划预览已生成",
        title="Mission Agent · 规划阶段完成",
        tone="success",
    )

    output = stream.getvalue()
    assert "• Thought" in output
    assert "• Called inspect_state" in output
    assert "• Explored inspect_state" in output
    assert "• Ran propose_task_graph" in output
    assert "• Mission Agent · 规划阶段完成" in output
    assert "封存计划预览已生成" in output


def test_terminal_agent_renderer_uses_dim_and_bold_ansi_styles():
    stream = io.StringIO()
    renderer = TerminalAgentRenderer(
        stream=stream,
        force_terminal=True,
        width=120,
    )

    renderer.render_thought("secondary trace")
    renderer.render_tool_call("navigate_to_point", summary="tool output")
    renderer.render_final_response("primary response", tone="success")

    output = stream.getvalue()
    assert "\x1b[2m" in output
    assert "\x1b[1;" in output or "\x1b[1m" in output


def test_terminal_agent_renderer_strips_remote_terminal_controls():
    stream = io.StringIO()
    renderer = TerminalAgentRenderer(stream=stream, force_terminal=False)

    renderer.render_tool_call(
        "ros.log",
        phase="explored",
        output="\x1b[31mremote error\x1b[0m\rforged",
        failed=True,
    )

    output = stream.getvalue()
    assert "remote error forged" in output
    assert "\x1b[31m" not in output
    assert "\r" not in output


def test_terminal_agent_renderer_uses_hanging_indent_for_wrapped_details():
    stream = io.StringIO()
    renderer = TerminalAgentRenderer(
        stream=stream,
        force_terminal=False,
        width=32,
    )

    renderer.render_tool_call(
        "navigate_to_point",
        summary="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )

    lines = stream.getvalue().splitlines()
    wrapped = [line for line in lines if "123456789" in line]
    assert wrapped == ["     123456789ABCDEFGHIJKLMNOPQR"]
