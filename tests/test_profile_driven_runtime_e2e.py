"""End-to-end regression for the profile-driven flow.

Covers: profile -> gateway -> mission -> dispatch chain.
Proves that capability_skill_chains from a TOML profile propagate all the
way through to the structured task produced by MissionAgent.submit_subtask.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.mission.mission_planner import MissionSubtask
from fireclaw_core.task.task_contract import structured_task_from_mission_subtask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class CapturingSubagentClient:
    """Minimal subagent client that captures the last structured_task."""

    def __init__(self):
        self.last_structured_task = None

    def submit_task(self, entry, **kwargs):
        self.last_structured_task = kwargs.get("structured_task")
        return {"task_id": "e2e-task-1", "status": "accepted"}

    def get_task_trace(self, entry, task_id):
        return {"status": "succeeded"}

    def cancel_task(self, entry, task_id, **kwargs):
        return {"status": "cancel_requested"}

    def get_events(self, entry, task_id=None, limit=100):
        return []

    def check_presence(self, entry):
        return {"status": "online"}


def _write_profile(path: Path, data_dir: Path, *, skill_chain: bool = True) -> None:
    """Write a minimal valid robot profile TOML."""
    chain_section = ""
    if skill_chain:
        chain_section = """

[capability_skill_chains]
navigation = ["navigate_to_point"]
"""
    path.write_text(
        f"""
[robot]
id = "e2e-robot"
base_url = "http://127.0.0.1:0"
adapter = "simulator"
data_dir = "{data_dir}"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]
{chain_section}""".strip(),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_profile_driven_flow_end_to_end(tmp_path: Path) -> None:
    """Profile -> Mission -> Dispatch uses profile skill chains, not defaults."""
    profile_path = tmp_path / "robot.toml"
    data_dir = tmp_path / "robots" / "e2e-robot"
    _write_profile(profile_path, data_dir)

    # Build mission agent from the profile (same path the CLI/runtime uses)
    paths = MissionRuntimePaths(
        robot_registry=tmp_path / "unused.json",
        mission_registry=tmp_path / "missions.jsonl",
        robot_profiles=(profile_path,),
    )
    client = CapturingSubagentClient()
    agent = build_mission_agent_from_paths(paths, operator_id="e2e-test", role="operator")
    # Inject our capturing client so we can inspect the structured_task
    agent.subagent_client = client

    # Registry was built from profile
    entry = agent.registry.get("e2e-robot")
    assert entry is not None, "robot should be registered from profile"
    assert entry.base_url == "http://127.0.0.1:0"

    # Profile skill chains were loaded into the agent
    assert "e2e-robot" in agent.profile_skill_chains_by_robot
    chains = agent.profile_skill_chains_by_robot["e2e-robot"]
    assert "navigation" in chains

    # Submit a subtask with capability_required
    subtask = MissionSubtask(
        robot_id="e2e-robot",
        command="去坐标 (2.0, 1.5)",
        floor=None,
        capability_required="navigation",
        execution_group=0,
        task_type="navigate",
        target={
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
            "frame_id": "map",
        },
    )
    result = agent.submit_subtask(
        "e2e-robot",
        "去坐标 (2.0, 1.5)",
        session_id="e2e-mission",
        mission_subtask=subtask,
    )
    assert result["status"] == "accepted"

    # The structured task should use the profile's skill chain
    captured = client.last_structured_task
    assert captured is not None, "structured_task should be captured"
    assert captured["required_skills"] == ["navigate_to_point"]
    assert captured["robot_id"] == "e2e-robot"
    assert captured["task_type"] == "navigate"


def test_profile_driven_flow_without_chain_falls_back_to_default(tmp_path: Path) -> None:
    """Without capability_skill_chains, dispatch falls back to default mapping."""
    profile_path = tmp_path / "robot.toml"
    data_dir = tmp_path / "robots" / "e2e-robot"
    _write_profile(profile_path, data_dir, skill_chain=False)

    paths = MissionRuntimePaths(
        robot_registry=tmp_path / "unused.json",
        mission_registry=tmp_path / "missions.jsonl",
        robot_profiles=(profile_path,),
    )
    client = CapturingSubagentClient()
    agent = build_mission_agent_from_paths(paths, operator_id="e2e-test", role="operator")
    agent.subagent_client = client

    subtask = MissionSubtask(
        robot_id="e2e-robot",
        command="去坐标 (2.0, 1.5)",
        floor=None,
        capability_required="navigation",
        execution_group=0,
        task_type="navigate",
        target={
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
            "frame_id": "map",
        },
    )
    result = agent.submit_subtask(
        "e2e-robot",
        "去坐标 (2.0, 1.5)",
        session_id="e2e-fallback",
        mission_subtask=subtask,
    )
    assert result["status"] == "accepted"

    captured = client.last_structured_task
    assert captured is not None
    # Without a profile chain, core does not invent a domain workflow.
    assert captured["required_skills"] == ["navigation"]


def test_structured_task_from_subtask_uses_profile_chains(tmp_path: Path) -> None:
    """Direct structured_task_from_mission_subtask respects capability_skill_chains."""
    subtask = MissionSubtask(
        robot_id="e2e-robot",
        command="去坐标 (2.0, 1.5)",
        floor=None,
        capability_required="navigation",
        execution_group=0,
        task_type="navigate",
        target={
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
            "frame_id": "map",
        },
    )
    chains = {"navigation": ["navigate_to_point"]}
    task = structured_task_from_mission_subtask(
        mission_id="e2e-test",
        subtask=subtask,
        capability_skill_chains=chains,
    )
    assert task.required_skills == ["navigate_to_point"]
    assert task.robot_id == "e2e-robot"
    assert task.task_type == "navigate"


def test_gateway_rejects_mismatched_profile_at_startup(tmp_path: Path) -> None:
    """Gateway with an unregistered enabled_skill fails fast."""
    profile_path = tmp_path / "bad-robot.toml"
    profile_path.write_text(
        """
[robot]
id = "bad-robot"
base_url = "http://127.0.0.1:0"
adapter = "simulator"
data_dir = "data/robots/bad-robot"
capabilities = ["navigation"]
enabled_skills = ["nonexistent_skill"]
llm_exposed_skills = ["nonexistent_skill"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="enabled skill 'nonexistent_skill' is not registered"):
        FireClawGateway(GatewayConfig(
            port=0,
            robot_profile_path=str(profile_path),
        ))


def test_gateway_starts_with_valid_profile(tmp_path: Path) -> None:
    """Gateway starts successfully with a valid robot profile."""
    profile_path = tmp_path / "robot.toml"
    data_dir = tmp_path / "robots" / "valid-robot"
    _write_profile(profile_path, data_dir)

    gateway = FireClawGateway(GatewayConfig(
        port=0,
        robot_profile_path=str(profile_path),
    ))
    gateway.start()
    try:
        assert gateway.robot_profile is not None
        assert gateway.robot_profile.robot_id == "e2e-robot"
        assert "navigation" in gateway.robot_profile.capability_skill_chains
    finally:
        gateway.stop()
