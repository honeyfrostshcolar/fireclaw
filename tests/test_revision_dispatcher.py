from __future__ import annotations

from dataclasses import replace

import pytest

from fireclaw_core.mission.execution_event import MissionExecutionEvent
from fireclaw_core.mission.revision_dispatcher import (
    JsonlMissionDispatchStore,
    MissionNodeExecution,
    RevisionDispatchReconciler,
    checkpoint_for_graph,
    execution_spec_hash,
)
from fireclaw_core.mission.task_graph import (
    MissionCondition,
    MissionEvidenceRequirement,
    MissionTarget,
    MissionTaskGraph,
    MissionTaskNode,
)


def _node(
    node_id: str,
    robot_id: str,
    *,
    command: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> MissionTaskNode:
    return MissionTaskNode(
        node_id=node_id,
        robot_id=robot_id,
        command=command or f"execute {node_id}",
        capability_required="victim_search",
        target=MissionTarget(frame_id="building", floor=2),
        depends_on=depends_on,
        preconditions=(
            MissionCondition(kind="robot_enabled", subject=robot_id),
            MissionCondition(
                kind="robot_has_capability",
                subject=robot_id,
                details={"capability": "victim_search"},
            ),
        ),
        expected_effects=(f"task_completed:{node_id}",),
        success_evidence=(
            MissionEvidenceRequirement(
                kind="task_terminal_success",
                source="execution_monitor",
            ),
        ),
        exclusive_resources=(f"robot:{robot_id}",),
        recovery_policy="reassign",
    )


def _graph(
    plan_id: str,
    nodes: tuple[MissionTaskNode, ...],
    *,
    revision: int,
    supersedes_plan_id: str | None = None,
) -> MissionTaskGraph:
    return MissionTaskGraph(
        plan_id=plan_id,
        mission_id="mission-1",
        intent="search",
        command="search floor 2",
        nodes=nodes,
        revision=revision,
        state_snapshot_id=f"mission-1:state:{revision}",
        supersedes_plan_id=supersedes_plan_id,
        invalidation_evidence_ids=(
            ("evidence-route-blocked",)
            if supersedes_plan_id is not None
            else ()
        ),
    )


def _event(*, node_id: str = "task-9") -> MissionExecutionEvent:
    return MissionExecutionEvent(
        event_id="event-route-blocked",
        mission_id="mission-1",
        event_type="route_blocked",
        evidence_id="evidence-route-blocked",
        source_type="execution_monitor",
        observed_at="2026-07-28T10:00:00+00:00",
        robot_id="robot-a",
        node_id=node_id,
    )


def test_completed_node_is_carried_across_robot_reassignment() -> None:
    old_node = _node("task-1", "robot-a")
    revised_node = _node("task-1", "robot-b")
    current = _graph("mission-1:plan:1", (old_node,), revision=1)
    revised = _graph(
        "mission-1:plan:2",
        (revised_node,),
        revision=2,
        supersedes_plan_id=current.plan_id,
    )
    execution = MissionNodeExecution(
        plan_id=current.plan_id,
        node_id=old_node.node_id,
        robot_id=old_node.robot_id,
        task_id="robot-task-1",
        status="succeeded",
        node_spec_hash=execution_spec_hash(old_node),
    )

    decision = RevisionDispatchReconciler().reconcile(
        current_graph=current,
        revised_graph=revised,
        executions=(execution,),
        invalidation_event=_event(),
    )

    assert decision.carried_node_ids == ("task-1",)
    assert decision.pending_node_ids == ()
    assert decision.cancel_executions == ()
    assert decision.fenced_task_ids == ()


def test_invalidated_completed_node_is_not_carried() -> None:
    node = _node("task-1", "robot-a")
    current = _graph("mission-1:plan:1", (node,), revision=1)
    revised = _graph(
        "mission-1:plan:2",
        (node,),
        revision=2,
        supersedes_plan_id=current.plan_id,
    )
    execution = MissionNodeExecution(
        plan_id=current.plan_id,
        node_id=node.node_id,
        robot_id=node.robot_id,
        task_id="robot-task-1",
        status="succeeded",
    )

    decision = RevisionDispatchReconciler().reconcile(
        current_graph=current,
        revised_graph=revised,
        executions=(execution,),
        invalidation_event=_event(node_id="task-1"),
    )

    assert decision.carried_node_ids == ()
    assert decision.pending_node_ids == ("task-1",)
    assert decision.fenced_task_ids == ("robot-task-1",)


def test_unchanged_active_node_is_preserved_but_changed_node_is_cancelled() -> None:
    keep = _node("task-1", "robot-a")
    change = _node("task-2", "robot-b")
    current = _graph(
        "mission-1:plan:1",
        (keep, change),
        revision=1,
    )
    revised = _graph(
        "mission-1:plan:2",
        (keep, replace(change, command="use east stair")),
        revision=2,
        supersedes_plan_id=current.plan_id,
    )
    executions = (
        MissionNodeExecution(
            plan_id=current.plan_id,
            node_id="task-1",
            robot_id="robot-a",
            task_id="robot-task-1",
            status="running",
        ),
        MissionNodeExecution(
            plan_id=current.plan_id,
            node_id="task-2",
            robot_id="robot-b",
            task_id="robot-task-2",
            status="accepted",
        ),
    )

    decision = RevisionDispatchReconciler().reconcile(
        current_graph=current,
        revised_graph=revised,
        executions=executions,
        invalidation_event=_event(node_id="task-2"),
    )

    assert decision.preserved_node_ids == ("task-1",)
    assert decision.pending_node_ids == ("task-2",)
    assert [
        item.task_id for item in decision.cancel_executions
    ] == ["robot-task-2"]
    assert decision.fenced_task_ids == ("robot-task-2",)


def test_removed_active_node_is_cancelled_and_fenced() -> None:
    keep = _node("task-1", "robot-a")
    remove = _node("task-2", "robot-b")
    current = _graph(
        "mission-1:plan:1",
        (keep, remove),
        revision=1,
    )
    revised = _graph(
        "mission-1:plan:2",
        (keep,),
        revision=2,
        supersedes_plan_id=current.plan_id,
    )
    execution = MissionNodeExecution(
        plan_id=current.plan_id,
        node_id="task-2",
        robot_id="robot-b",
        task_id="robot-task-2",
        status="running",
    )

    decision = RevisionDispatchReconciler().reconcile(
        current_graph=current,
        revised_graph=revised,
        executions=(execution,),
        invalidation_event=_event(node_id="task-2"),
    )

    assert decision.pending_node_ids == ("task-1",)
    assert [
        item.task_id for item in decision.cancel_executions
    ] == ["robot-task-2"]
    assert decision.fenced_task_ids == ("robot-task-2",)


def test_checkpoint_store_restores_latest_valid_snapshot(tmp_path) -> None:
    node = _node("task-1", "robot-a")
    graph = _graph("mission-1:plan:1", (node,), revision=1)
    store = JsonlMissionDispatchStore(tmp_path / "dispatch.jsonl")
    first = checkpoint_for_graph(graph, ())
    running = checkpoint_for_graph(
        graph,
        (
            MissionNodeExecution(
                plan_id=graph.plan_id,
                node_id=node.node_id,
                robot_id=node.robot_id,
                task_id="robot-task-1",
                status="running",
                node_spec_hash=execution_spec_hash(node),
            ),
        ),
    )

    store.append(first)
    store.append(running)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{malformed\n")

    restored = store.latest("mission-1")

    assert restored == running
    assert restored is not None
    assert restored.node_executions[0].task_id == "robot-task-1"
    assert restored.task_graph == graph


def test_checkpoint_store_lists_latest_checkpoint_per_mission(tmp_path) -> None:
    first_node = _node("task-1", "robot-a")
    first_graph = _graph(
        "mission-1:plan:1",
        (first_node,),
        revision=1,
    )
    second_graph = replace(
        first_graph,
        mission_id="mission-2",
        plan_id="mission-2:plan:1",
        state_snapshot_id="mission-2:state:1",
    )
    store = JsonlMissionDispatchStore(tmp_path / "dispatch.jsonl")
    store.append(checkpoint_for_graph(first_graph, ()))
    store.append(checkpoint_for_graph(second_graph, ()))
    latest_first = checkpoint_for_graph(
        first_graph,
        (
            MissionNodeExecution(
                plan_id=first_graph.plan_id,
                node_id=first_node.node_id,
                robot_id=first_node.robot_id,
                status="succeeded",
                node_spec_hash=execution_spec_hash(first_node),
            ),
        ),
    )
    store.append(latest_first)

    latest = store.latest_by_mission()

    assert set(latest) == {"mission-1", "mission-2"}
    assert latest["mission-1"] == latest_first
    assert latest["mission-2"].task_graph == second_graph


def test_task_graph_round_trips_through_checkpoint_payload() -> None:
    graph = _graph(
        "mission-1:plan:1",
        (
            _node("task-1", "robot-a"),
            _node("task-2", "robot-b", depends_on=("task-1",)),
        ),
        revision=1,
    )
    checkpoint = checkpoint_for_graph(graph, ())

    restored = type(checkpoint).from_dict(checkpoint.to_dict())

    assert restored == checkpoint
    assert restored.task_graph == graph


def test_recovery_reader_blocks_complete_corrupt_checkpoint_line(
    tmp_path,
) -> None:
    graph = _graph(
        "mission-1:plan:1",
        (_node("task-1", "robot-a"),),
        revision=1,
    )
    store = JsonlMissionDispatchStore(tmp_path / "dispatch.jsonl")
    store.append(checkpoint_for_graph(graph, ()))
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{malformed\n")

    with pytest.raises(ValueError, match="Malformed dispatch checkpoint"):
        store.recoverable_latest_by_mission()


def test_recovery_reader_ignores_only_unterminated_tail(tmp_path) -> None:
    graph = _graph(
        "mission-1:plan:1",
        (_node("task-1", "robot-a"),),
        revision=1,
    )
    store = JsonlMissionDispatchStore(tmp_path / "dispatch.jsonl")
    checkpoint = checkpoint_for_graph(graph, ())
    store.append(checkpoint)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write('{"mission_id":')

    assert store.recoverable_latest_by_mission() == {
        "mission-1": checkpoint
    }
