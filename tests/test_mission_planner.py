from fireclaw_core.mission.mission_planner import MissionPlanner, MissionPlannerContext
from fireclaw_core.agent.robot_registry import RobotRegistryEntry


def _robots(entries):
    return MissionPlannerContext(available_robots=list(entries))


def test_mission_planner_rejects_multi_floor_command_without_2d_points():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("victim_search",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("victim_search",)),
    ])

    result = planner.plan("去二楼和三楼搜索受困人员", context=robots)

    assert result.status == "clarify"
    assert result.plan is None
    assert "二维 map" in result.message
    assert "目标点" in result.message


def test_mission_planner_rejects_single_floor_command_without_2d_point():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("victim_search",)),
    ])

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "clarify"
    assert result.plan is None
    assert "二维 map" in result.message


def test_mission_planner_clarifies_when_no_floor_specified():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("victim_search",)),
    ])

    result = planner.plan("搜索整栋楼", context=robots)

    assert result.status == "clarify"
    assert "目标点" in result.message


def test_mission_planner_uses_2d_map_pose_target():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("victim_search",),
        ),
    ])

    result = planner.plan("去坐标 (2.0, 1.5) 搜索受困人员", context=robots)

    assert result.status == "planned"
    assert result.plan.subtasks[0].floor is None
    assert result.plan.subtasks[0].target == {
        "frame_id": "map",
        "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
    }


def test_mission_planner_parses_patrol_command():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("patrol",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("patrol",)),
    ])

    result = planner.plan("巡逻坐标 (1.0, 1.0) 和坐标 (2.0, 2.0)", context=robots)

    assert result.status == "planned"
    assert result.intent == "patrol"
    assert [s.robot_id for s in result.plan.subtasks] == ["r1", "r2"]
    assert all(s.floor is None for s in result.plan.subtasks)


def test_mission_planner_treats_planned_motion_as_patrol():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("patrol",)),
    ])

    result = planner.plan("去坐标 (2.0, 1.5) 做一次简单的规划运动", context=robots)

    assert result.status == "planned"
    assert result.intent == "patrol"
    assert result.plan.subtasks[0].capability_required == "patrol"
    assert result.plan.subtasks[0].target["frame_id"] == "map"


def test_mission_planner_assigns_different_robots_when_enough():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("victim_search",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("victim_search",)),
    ])

    result = planner.plan(
        "搜索坐标 (2.0, 1.5) 和坐标 (3.0, 2.5) 的受困人员",
        context=robots,
    )

    assert result.status == "planned"
    robot_ids = [s.robot_id for s in result.plan.subtasks]
    assert robot_ids == ["r1", "r2"]
    # Both in execution group 0 (parallel)
    assert all(s.execution_group == 0 for s in result.plan.subtasks)


def test_mission_planner_reuses_robot_sequentially_when_not_enough():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("victim_search",)),
    ])

    result = planner.plan(
        "搜索坐标 (2.0, 1.5) 和坐标 (3.0, 2.5) 的受困人员",
        context=robots,
    )

    assert result.status == "planned"
    robot_ids = [s.robot_id for s in result.plan.subtasks]
    assert robot_ids == ["r1", "r1"]
    # Different execution groups (sequential)
    assert result.plan.subtasks[0].execution_group == 0
    assert result.plan.subtasks[1].execution_group == 1
    assert result.plan.execution_groups == 2


def test_mission_planner_clarifies_when_no_capable_robot():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("patrol",)),
    ])

    result = planner.plan("去坐标 (2.0, 1.5) 搜索受困人员", context=robots)

    assert result.status == "clarify"
    assert "victim_search" in result.message


def test_mission_planner_excludes_disabled_robots():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("victim_search",), enabled=False),
    ])

    result = planner.plan("去坐标 (2.0, 1.5) 搜索受困人员", context=robots)

    assert result.status == "clarify"


def test_mission_planner_no_robots_at_all():
    planner = MissionPlanner()
    robots = _robots([])

    result = planner.plan("去坐标 (2.0, 1.5) 搜索受困人员", context=robots)

    assert result.status == "clarify"


def test_mission_planner_context_has_memory_fields():
    """MissionPlannerContext should support retrieved_memories and operator_corrections."""
    ctx = MissionPlannerContext(
        available_robots=[],
        retrieved_memories=[{"mission_id": "m1", "content": {"command": "test"}}],
        operator_corrections=[{"content": {"correction": "fix this"}}],
    )

    assert len(ctx.retrieved_memories) == 1
    assert ctx.retrieved_memories[0]["mission_id"] == "m1"
    assert len(ctx.operator_corrections) == 1
    assert ctx.operator_corrections[0]["content"]["correction"] == "fix this"


def test_mission_planner_context_defaults_to_empty_memory_fields():
    """MissionPlannerContext should default to empty lists for memory fields."""
    ctx = MissionPlannerContext()

    assert ctx.retrieved_memories == []
    assert ctx.operator_corrections == []
