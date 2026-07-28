from dataclasses import replace

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationDecision,
    MissionDeliberationLimits,
    MissionDeliberationRuntime,
)
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshotBuilder,
)


def _registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims",),
        ),
    ])


def _snapshot():
    return MissionStateSnapshotBuilder(registry=_registry()).build(
        mission_id="mission-1",
        presence={
            "robot-a": {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 85,
                        "current_floor": 1,
                    }
                },
            }
        },
        captured_at="2026-07-28T01:00:01+00:00",
    )


def _context(snapshot):
    return MissionPlannerContext(
        available_robots=_registry().enabled_entries(),
        state_snapshot=snapshot.to_dict(),
    )


def _planning_result(*, floor: int = 2) -> MissionPlanningResult:
    return MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-a",
                    command="去二楼搜索受困人员",
                    floor=floor,
                    capability_required="search_for_victims",
                )
            ],
        ),
    )


class _SequencePolicy:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.decisions.pop(0)


def _run(policy, *, snapshot=None, limits=None, **kwargs):
    current_snapshot = snapshot or _snapshot()
    runtime = MissionDeliberationRuntime(
        registry=_registry(),
        policy=policy,
        limits=limits,
        **kwargs,
    )
    return runtime.deliberate(
        mission_id="mission-1",
        command="去二楼搜索受困人员",
        state_snapshot=current_snapshot,
        planner_context=_context(current_snapshot),
    )


def test_runtime_accepts_validated_plan_proposal_without_dispatching() -> None:
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(_planning_result()),
    ])

    result = _run(policy)

    assert result.status == "proposed"
    assert result.task_graph is not None
    assert result.task_graph.state_snapshot_id == "mission-1:state:1"
    assert result.attempts[0].outcome == "accepted"
    assert result.validation_errors == ()


def test_runtime_can_inspect_snapshot_before_proposing() -> None:
    policy = _SequencePolicy([
        MissionDeliberationDecision.inspect("robot_state", subject_id="robot-a"),
        MissionDeliberationDecision.propose(_planning_result()),
    ])

    result = _run(policy)

    assert result.status == "proposed"
    assert [attempt.outcome for attempt in result.attempts] == [
        "observed",
        "accepted",
    ]
    assert result.observations[0].data["robot"]["battery_percent"] == 85.0
    assert len(policy.requests[1].observations) == 1


def test_runtime_rejects_repeated_snapshot_read_without_progress() -> None:
    policy = _SequencePolicy([
        MissionDeliberationDecision.inspect("fleet_state"),
        MissionDeliberationDecision.inspect("fleet_state"),
    ])

    result = _run(policy)

    assert result.status == "blocked"
    assert result.reason_code == "repeated_state_read"
    assert [attempt.outcome for attempt in result.attempts] == [
        "observed",
        "rejected",
    ]


def test_runtime_can_revise_rejected_proposal_with_validator_feedback() -> None:
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(_planning_result(floor=0)),
        MissionDeliberationDecision.propose(_planning_result(floor=2)),
    ])

    result = _run(policy)

    assert result.status == "proposed"
    assert [attempt.outcome for attempt in result.attempts] == [
        "rejected",
        "accepted",
    ]
    assert "Subtask floor must be positive" in policy.requests[1].validation_errors[0]
    assert policy.requests[1].last_planning_result is not None


def test_runtime_blocks_after_iteration_limit() -> None:
    invalid = MissionDeliberationDecision(
        operation="unknown_operation",
        message="invalid",
    )
    policy = _SequencePolicy([invalid, invalid])

    result = _run(
        policy,
        limits=MissionDeliberationLimits(
            max_iterations=2,
            timeout_seconds=5,
            max_observations=1,
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "iteration_limit"
    assert len(result.attempts) == 2


def test_runtime_rejects_invalid_policy_decision_type() -> None:
    class InvalidPolicy:
        def decide(self, request):
            return {"operation": "propose_plan"}

    result = _run(
        InvalidPolicy(),
        limits=MissionDeliberationLimits(max_iterations=1),
    )

    assert result.status == "blocked"
    assert result.reason_code == "iteration_limit"
    assert result.attempts[0].reason_code == "invalid_runtime_decision"


def test_runtime_fails_closed_when_proposal_cannot_be_projected() -> None:
    malformed_result = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan="not-a-mission-plan",  # type: ignore[arg-type]
    )
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(malformed_result),
    ])

    result = _run(
        policy,
        limits=MissionDeliberationLimits(max_iterations=1),
    )

    assert result.status == "blocked"
    assert result.validation_errors == (
        "Mission plan proposal could not be projected for validation.",
    )


def test_runtime_times_out_after_policy_attempt() -> None:
    class Clock:
        value = 0.0

        def monotonic(self):
            return self.value

    clock = Clock()

    class SlowPolicy:
        def decide(self, request):
            clock.value = 2.0
            return MissionDeliberationDecision.propose(_planning_result())

    result = _run(
        SlowPolicy(),
        limits=MissionDeliberationLimits(timeout_seconds=1),
        monotonic=clock.monotonic,
    )

    assert result.status == "timed_out"
    assert result.reason_code == "deliberation_timeout"
    assert result.task_graph is None


def test_runtime_honors_cancellation_before_policy_call() -> None:
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(_planning_result()),
    ])

    result = _run(policy, cancellation_requested=lambda: True)

    assert result.status == "cancelled"
    assert result.attempts == ()
    assert policy.requests == []


def test_runtime_blocks_mismatched_planner_snapshot() -> None:
    snapshot = _snapshot()
    runtime = MissionDeliberationRuntime(
        registry=_registry(),
        policy=_SequencePolicy([
            MissionDeliberationDecision.propose(_planning_result()),
        ]),
    )
    context = MissionPlannerContext(
        available_robots=_registry().enabled_entries(),
        state_snapshot={**snapshot.to_dict(), "snapshot_id": "wrong-snapshot"},
    )

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼搜索受困人员",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "blocked"
    assert result.reason_code == "invalid_state_snapshot"
    assert "does not match" in result.validation_errors[0]


def test_runtime_builds_evidence_bound_plan_revision() -> None:
    fact = MissionEnvironmentFact(
        fact_id="route-blocked",
        kind="route_blocked",
        value="west-stair",
        source="execution_monitor",
        observed_at="2026-07-28T01:00:00+00:00",
        evidence_ids=("route-blocked-observation",),
        confidence=1.0,
        subject_id="west-stair",
    )
    snapshot = MissionStateSnapshotBuilder(
        registry=_registry()
    ).with_environment_facts(
        replace(
        _snapshot(),
        snapshot_id="mission-1:state:2",
        version=2,
        previous_snapshot_id="mission-1:state:1",
        ),
        (fact,),
        extra_evidence_ids=("route-blocked-observation",),
    )
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(_planning_result()),
        MissionDeliberationDecision.inspect(
            "environment_beliefs",
            subject_id="route_blocked",
        ),
        MissionDeliberationDecision.propose(_planning_result()),
    ])
    runtime = MissionDeliberationRuntime(registry=_registry(), policy=policy)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼搜索受困人员",
        state_snapshot=snapshot,
        planner_context=_context(snapshot),
        plan_revision=2,
        supersedes_plan_id="mission-1:plan:1",
        invalidation_evidence_ids=("route-blocked-observation",),
    )

    assert result.status == "proposed"
    assert [attempt.outcome for attempt in result.attempts] == [
        "rejected",
        "observed",
        "accepted",
    ]
    assert "must inspect environment beliefs" in (
        policy.requests[1].validation_errors[0]
    )
    assert result.task_graph is not None
    assert result.task_graph.revision == 2
    assert result.task_graph.supersedes_plan_id == "mission-1:plan:1"
    assert result.task_graph.invalidation_evidence_ids == (
        "route-blocked-observation",
    )


def test_runtime_requires_inspection_of_unresolved_beliefs() -> None:
    fact = MissionEnvironmentFact(
        fact_id="thermal-victim-candidate",
        kind="victim_present",
        value=True,
        source="robot-a:thermal",
        observed_at="2026-07-28T01:00:00+00:00",
        evidence_ids=("thermal-frame-7",),
        confidence=0.6,
        subject_id="second-floor-west-room",
    )
    snapshot = MissionStateSnapshotBuilder(
        registry=_registry()
    ).with_environment_facts(
        _snapshot(),
        (fact,),
    )
    belief = snapshot.environment_beliefs[0]
    assert belief.status == "uncertain"
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(_planning_result()),
        MissionDeliberationDecision.inspect(
            "environment_beliefs",
            subject_id=belief.belief_id,
        ),
        MissionDeliberationDecision.propose(_planning_result()),
    ])
    runtime = MissionDeliberationRuntime(registry=_registry(), policy=policy)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼搜索受困人员",
        state_snapshot=snapshot,
        planner_context=_context(snapshot),
    )

    assert result.status == "proposed"
    assert [attempt.outcome for attempt in result.attempts] == [
        "rejected",
        "observed",
        "accepted",
    ]
    assert "must inspect unresolved environment beliefs" in (
        policy.requests[1].validation_errors[0]
    )


def test_runtime_rejects_revision_evidence_missing_from_snapshot() -> None:
    snapshot = replace(
        _snapshot(),
        snapshot_id="mission-1:state:2",
        version=2,
        previous_snapshot_id="mission-1:state:1",
    )
    policy = _SequencePolicy([
        MissionDeliberationDecision.propose(_planning_result()),
    ])
    runtime = MissionDeliberationRuntime(
        registry=_registry(),
        policy=policy,
        limits=MissionDeliberationLimits(max_iterations=1),
    )

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼搜索受困人员",
        state_snapshot=snapshot,
        planner_context=_context(snapshot),
        plan_revision=2,
        supersedes_plan_id="mission-1:plan:1",
        invalidation_evidence_ids=("unknown-observation",),
    )

    assert result.status == "blocked"
    assert "absent from its state snapshot" in result.validation_errors[0]
