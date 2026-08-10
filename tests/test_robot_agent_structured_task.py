from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.task.task_contract import StructuredRobotTask


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def test_robot_agent_executes_plugin_owned_structured_task_without_nl_planner(
    tmp_path: Path,
) -> None:
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(
        robot=robot,
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    )
    task = StructuredRobotTask(
        task_id="task-structured",
        task_type="navigate",
        target={
            "frame_id": "map",
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
        },
        required_skills=["navigate_to_point"],
        mission_id="mission-1",
        robot_id="robot-1",
        command="human readable text is not used for task decomposition",
    )

    result = agent.run_structured_task(task)

    assert result["status"] == "succeeded"
    assert result["structured_task"]["task_id"] == "task-structured"
    assert [
        step["skill_name"] for step in result["execution"]["steps"]
    ] == ["navigate_to_point"]
    assert not hasattr(robot, "navigate_to_point")
    contribution = agent.plugin_host.get(
        "physical_capability",
        "navigate_to_point",
    )
    assert contribution is not None
    assert contribution.owner_plugin_id == "fireclaw.navigation.move-base"


def test_robot_agent_rejects_invalid_structured_task() -> None:
    agent = FireClawAgent()
    task = StructuredRobotTask(
        task_id="task-invalid",
        task_type="navigate",
        target={
            "frame_id": "map",
            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
        },
        required_skills=[],
    )

    result = agent.run_structured_task(task)

    assert result["status"] in {"clarify", "blocked"}
    assert "required_skills must not be empty" in result["message"]
