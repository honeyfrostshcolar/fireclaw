from fireclaw_core.mission_planner import MissionPlanner, MissionPlannerContext
from fireclaw_core.robot_registry import RobotRegistryEntry


def _robots(entries):
    return MissionPlannerContext(available_robots=list(entries))


def test_mission_planner_parses_multi_floor_command():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])

    result = planner.plan("去二楼和三楼搜索受困人员", context=robots)

    assert result.status == "planned"
    assert result.intent == "search"
    floors = [s.floor for s in result.plan.subtasks]
    assert sorted(floors) == [2, 3]
    assert all(s.capability_required == "search_for_victims" for s in result.plan.subtasks)


def test_mission_planner_parses_single_floor_command():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "planned"
    assert result.intent == "search"
    assert len(result.plan.subtasks) == 1
    assert result.plan.subtasks[0].floor == 2


def test_mission_planner_clarifies_when_no_floor_specified():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])

    result = planner.plan("搜索整栋楼", context=robots)

    assert result.status == "clarify"
    assert "楼层" in result.message


def test_mission_planner_parses_patrol_command():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("patrol",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("patrol",)),
    ])

    result = planner.plan("巡逻一楼和二楼", context=robots)

    assert result.status == "planned"
    assert result.intent == "patrol"
    floors = [s.floor for s in result.plan.subtasks]
    assert sorted(floors) == [1, 2]


def test_mission_planner_assigns_different_robots_when_enough():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])

    result = planner.plan("去二楼和三楼搜索受困人员", context=robots)

    assert result.status == "planned"
    robot_ids = [s.robot_id for s in result.plan.subtasks]
    assert robot_ids == ["r1", "r2"]
    # Both in execution group 0 (parallel)
    assert all(s.execution_group == 0 for s in result.plan.subtasks)


def test_mission_planner_reuses_robot_sequentially_when_not_enough():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])

    result = planner.plan("去二楼和三楼搜索受困人员", context=robots)

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

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "clarify"
    assert "search_for_victims" in result.message


def test_mission_planner_excludes_disabled_robots():
    planner = MissionPlanner()
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",), enabled=False),
    ])

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "clarify"


def test_mission_planner_no_robots_at_all():
    planner = MissionPlanner()
    robots = _robots([])

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "clarify"
