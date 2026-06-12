from __future__ import annotations

import subprocess


def test_gateway_cli_exposes_robot_agent_flags():
    completed = subprocess.run(
        [".venv/bin/python", "-m", "fireclaw_core", "serve", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout
    assert "--robot-agent-provider-base-url" in completed.stdout
    assert "--robot-agent-provider-api-key" in completed.stdout
    assert "--robot-agent-model" in completed.stdout
