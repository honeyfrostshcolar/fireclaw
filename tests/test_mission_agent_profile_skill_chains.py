from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.robot_profile import RobotCapabilityProfile
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry, robot_registry_from_profiles
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_planner import MissionSubtask


class CapturingSubagentClient:
    """Minimal subagent client that captures the last structured_task."""
    def __init__(self):
        self.last_structured_task = None

    def submit_task(self, entry, **kwargs):
        self.last_structured_task = kwargs.get("structured_task")
        return {"task_id": "test-task-1", "status": "accepted"}

    def get_task_trace(self, entry, task_id):
        return {"status": "succeeded"}

    def cancel_task(self, entry, task_id, **kwargs):
        return {"status": "cancel_requested"}

    def get_events(self, entry, task_id=None, limit=100):
        return []

    def check_presence(self, entry):
        return {"status": "online"}


def test_mission_agent_uses_selected_robot_profile_skill_chain(tmp_path: Path) -> None:
    profile = RobotCapabilityProfile(
        robot_id="profile-robot",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=tmp_path / "profile-robot",
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "report_status"),
        capability_skill_chains={"search_for_victims": ("navigate_to_floor", "report_status")},
    )
    client = CapturingSubagentClient()
    agent = MissionAgent(
        registry=robot_registry_from_profiles([profile]),
        subagent_client=client,
        profile_skill_chains_by_robot={"profile-robot": profile.capability_skill_chains},
    )

    subtask = MissionSubtask(
        robot_id="profile-robot",
        command="去二楼搜索",
        floor=2,
        capability_required="search_for_victims",
        execution_group=0,
    )
    result = agent.submit_subtask(
        "profile-robot",
        "去二楼搜索",
        session_id="m1",
        mission_subtask=subtask,
    )

    assert result["status"] == "accepted"
    assert client.last_structured_task is not None
    assert client.last_structured_task["required_skills"] == ["navigate_to_floor", "report_status"]


def test_mission_agent_falls_back_to_default_when_no_chains(tmp_path: Path) -> None:
    profile = RobotCapabilityProfile(
        robot_id="profile-robot",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=tmp_path / "profile-robot",
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "report_status"),
        capability_skill_chains={"search_for_victims": ("navigate_to_floor", "report_status")},
    )
    client = CapturingSubagentClient()
    agent = MissionAgent(
        registry=robot_registry_from_profiles([profile]),
        subagent_client=client,
        # No profile_skill_chains_by_robot passed
    )

    subtask = MissionSubtask(
        robot_id="profile-robot",
        command="去二楼搜索",
        floor=2,
        capability_required="search_for_victims",
        execution_group=0,
    )
    result = agent.submit_subtask(
        "profile-robot",
        "去二楼搜索",
        session_id="m2",
        mission_subtask=subtask,
    )

    assert result["status"] == "accepted"
    assert client.last_structured_task is not None
    # Without chains, should fall back to default _skills_from_capability
    assert client.last_structured_task["required_skills"] == ["navigate_to_floor", "search_for_victims", "report_status"]


def test_mission_agent_generated_subtask_uses_chains(tmp_path: Path) -> None:
    """Test that the generated subtask path (no mission_subtask arg) also uses chains."""
    profile = RobotCapabilityProfile(
        robot_id="profile-robot",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=tmp_path / "profile-robot",
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "report_status"),
        capability_skill_chains={"search_for_victims": ("navigate_to_floor", "report_status")},
    )
    client = CapturingSubagentClient()
    agent = MissionAgent(
        registry=robot_registry_from_profiles([profile]),
        subagent_client=client,
        profile_skill_chains_by_robot={"profile-robot": profile.capability_skill_chains},
    )

    # Call without mission_subtask - triggers generated subtask path
    result = agent.submit_subtask(
        "profile-robot",
        "去二楼搜索",
        session_id="m3",
    )

    assert result["status"] == "accepted"
    assert client.last_structured_task is not None
    assert client.last_structured_task["required_skills"] == ["navigate_to_floor", "report_status"]
