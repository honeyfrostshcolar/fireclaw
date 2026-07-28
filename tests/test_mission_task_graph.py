from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.task_graph import (
    MissionCondition,
    MissionEvidenceRequirement,
    MissionTarget,
    MissionTaskGraph,
    MissionTaskGraphValidator,
    MissionTaskNode,
    task_graph_from_mission_plan,
)


def _registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims", "recon"),
        ),
        RobotRegistryEntry(
            robot_id="robot-b",
            base_url="http://robot-b.test",
            capabilities=("search_for_victims",),
        ),
    ])


def _node(
    node_id: str,
    *,
    robot_id: str = "robot-a",
    depends_on: tuple[str, ...] = (),
    risk_level: str = "low",
    resources: tuple[str, ...] = (),
    evidence: tuple[MissionEvidenceRequirement, ...] | None = None,
) -> MissionTaskNode:
    return MissionTaskNode(
        node_id=node_id,
        robot_id=robot_id,
        command="搜索二楼受困人员",
        capability_required="search_for_victims",
        target=MissionTarget(frame_id="building", floor=2),
        depends_on=depends_on,
        preconditions=(
            MissionCondition(kind="robot_enabled", subject=robot_id),
            MissionCondition(
                kind="robot_has_capability",
                subject=robot_id,
                details={"capability": "search_for_victims"},
            ),
        ),
        expected_effects=(f"task_completed:{node_id}",),
        success_evidence=evidence
        if evidence is not None
        else (
            MissionEvidenceRequirement(
                kind="task_terminal_success",
                source="execution_monitor",
            ),
        ),
        risk_level=risk_level,
        exclusive_resources=resources,
        recovery_policy="replan",
    )


def _graph(*nodes: MissionTaskNode, **kwargs: object) -> MissionTaskGraph:
    return MissionTaskGraph(
        plan_id="plan-1",
        mission_id="mission-1",
        intent="search",
        command="去二楼搜索受困人员",
        nodes=nodes,
        **kwargs,
    )


def test_legacy_plan_projects_execution_groups_to_dependencies() -> None:
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索受困人员",
        subtasks=[
            MissionSubtask(
                robot_id="robot-a",
                command="去二楼搜索受困人员",
                floor=2,
                capability_required="search_for_victims",
                execution_group=0,
            ),
            MissionSubtask(
                robot_id="robot-a",
                command="去三楼搜索受困人员",
                floor=3,
                capability_required="search_for_victims",
                execution_group=1,
            ),
        ],
    )

    graph = task_graph_from_mission_plan(
        plan,
        mission_id="mission-1",
        plan_id="plan-1",
    )

    assert [node.node_id for node in graph.nodes] == ["task-1", "task-2"]
    assert graph.nodes[0].depends_on == ()
    assert graph.nodes[1].depends_on == ("task-1",)
    assert MissionTaskGraphValidator().validate(graph, _registry()) == []


def test_validator_rejects_high_risk_node_without_sensor_evidence() -> None:
    graph = _graph(_node("search", risk_level="high"))

    errors = MissionTaskGraphValidator().validate(graph, _registry())

    assert errors == [
        "Mission task node 'search' requires sensor_observation evidence at risk high."
    ]


def test_validator_requires_evidence_for_plan_revision() -> None:
    graph = _graph(
        _node("search"),
        revision=2,
        state_snapshot_id=None,
        supersedes_plan_id=None,
    )

    errors = MissionTaskGraphValidator().validate(graph, _registry())

    assert "Revised mission task graph requires state_snapshot_id." in errors
    assert "Revised mission task graph requires supersedes_plan_id." in errors
    assert "Revised mission task graph requires invalidation evidence." in errors


def test_validator_allows_evidence_bound_revised_graph() -> None:
    graph = _graph(
        _node(
            "route-recon",
            robot_id="robot-a",
            resources=("robot:robot-a",),
            evidence=(
                MissionEvidenceRequirement(
                    kind="sensor_observation",
                    source="thermal_camera",
                    max_age_seconds=10,
                ),
            ),
        ),
        revision=2,
        state_snapshot_id="snapshot-after-route-block",
        supersedes_plan_id="plan-0",
        invalidation_evidence_ids=("observation-route-blocked",),
    )

    assert MissionTaskGraphValidator().validate(graph, _registry()) == []


def test_validator_rejects_parallel_exclusive_resource_conflict() -> None:
    graph = _graph(
        _node("search-a", robot_id="robot-a", resources=("water-line-1",)),
        _node("search-b", robot_id="robot-b", resources=("water-line-1",)),
    )

    errors = MissionTaskGraphValidator().validate(graph, _registry())

    assert errors == [
        "Mission task nodes 'search-a' and 'search-b' conflict on exclusive resources ['water-line-1']."
    ]


def test_validator_allows_resource_reuse_when_dependency_orders_nodes() -> None:
    graph = _graph(
        _node("search-a", robot_id="robot-a", resources=("water-line-1",)),
        _node(
            "search-b",
            robot_id="robot-b",
            depends_on=("search-a",),
            resources=("water-line-1",),
        ),
    )

    assert MissionTaskGraphValidator().validate(graph, _registry()) == []


def test_legacy_plan_validator_blocks_parallel_reuse_of_one_robot() -> None:
    plan = MissionPlan(
        intent="search",
        command="并行搜索二楼和三楼",
        subtasks=[
            MissionSubtask(
                robot_id="robot-a",
                command="去二楼搜索受困人员",
                floor=2,
                capability_required="search_for_victims",
                execution_group=0,
            ),
            MissionSubtask(
                robot_id="robot-a",
                command="去三楼搜索受困人员",
                floor=3,
                capability_required="search_for_victims",
                execution_group=0,
            ),
        ],
    )

    errors = MissionPlanValidator().validate(plan, _registry())

    assert errors == [
        "Mission task nodes 'task-1' and 'task-2' conflict on exclusive resources ['robot:robot-a']."
    ]


def test_mission_agent_returns_the_validated_task_graph() -> None:
    class Planner:
        def plan(self, command: str, *, context: object) -> MissionPlanningResult:
            return MissionPlanningResult(
                status="planned",
                message="planned",
                intent="search",
                plan=MissionPlan(
                    intent="search",
                    command=command,
                    subtasks=[
                        MissionSubtask(
                            robot_id="robot-a",
                            command="去二楼搜索受困人员",
                            floor=2,
                            capability_required="search_for_victims",
                        )
                    ],
                ),
            )

    class Client:
        def check_presence(self, entry: RobotRegistryEntry) -> dict[str, object]:
            return {
                "robot_id": entry.robot_id,
                "online": True,
                "last_seen_at": "2026-07-28T00:00:00+00:00",
            }

        def submit_task(self, entry: RobotRegistryEntry, **kwargs: object) -> dict[str, object]:
            return {"status": "accepted", "task_id": "robot-task", "robot_id": entry.robot_id}

    result = MissionAgent(
        registry=_registry(),
        planner=Planner(),
        subagent_client=Client(),
    ).plan_and_submit("去二楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    assert result["task_graph"]["plan_id"] == "mission-1:plan:1"
    assert result["task_graph"]["state_snapshot_id"] == "mission-1:state:1"
    assert result["state_snapshot"]["snapshot_id"] == "mission-1:state:1"
    assert result["task_graph"]["nodes"][0]["node_id"] == "task-1"
    assert result["task_graph"]["nodes"][0]["target"] == {
        "frame_id": "building",
        "floor": 2,
    }
