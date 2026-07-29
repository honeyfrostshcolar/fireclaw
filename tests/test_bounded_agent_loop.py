from __future__ import annotations

from dataclasses import dataclass

import pytest

from fireclaw_core.agent.bounded_loop import (
    AgentLoopLimits,
    AgentLoopTransition,
    BoundedAgentLoop,
)
from fireclaw_core.agent.loop_checkpoint import (
    JsonlAgentLoopCheckpointStore,
)


@dataclass(frozen=True)
class Decision:
    operation: str


def test_bounded_loop_returns_tool_observation_to_next_turn():
    seen = []

    def decide(turn):
        seen.append(turn)
        return Decision("execute_skill" if not turn.observations else "complete")

    def execute(decision, turn):
        if decision.operation == "execute_skill":
            return AgentLoopTransition.continuing(
                operation=decision.operation,
                message="skill executed",
                observation={"status": "succeeded"},
            )
        return AgentLoopTransition(
            status="completed",
            operation=decision.operation,
            message="done",
            result={"status": "completed"},
        )

    result = BoundedAgentLoop[Decision, dict, dict]().run(
        run_id="run-1",
        decide=decide,
        execute=execute,
    )

    assert result.status == "completed"
    assert seen[1].observations == ({"status": "succeeded"},)
    assert [attempt.outcome for attempt in result.attempts] == [
        "observed",
        "completed",
    ]


def test_bounded_loop_blocks_continuation_without_observation():
    result = BoundedAgentLoop[Decision, dict, dict]().run(
        run_id="run-1",
        decide=lambda turn: Decision("inspect"),
        execute=lambda decision, turn: AgentLoopTransition(
            status="continue",
            operation=decision.operation,
            message="no result",
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "no_progress"


def test_bounded_loop_enforces_iteration_limit():
    loop = BoundedAgentLoop[Decision, dict, dict](
        limits=AgentLoopLimits(max_iterations=2, timeout_seconds=30),
    )

    result = loop.run(
        run_id="run-1",
        decide=lambda turn: Decision("inspect"),
        execute=lambda decision, turn: AgentLoopTransition.continuing(
            operation=decision.operation,
            message="observed",
            observation={"iteration": turn.iteration},
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "iteration_limit"
    assert len(result.observations) == 2


def test_bounded_loop_honors_cancellation_before_policy_call():
    called = False

    def decide(turn):
        nonlocal called
        called = True
        return Decision("complete")

    result = BoundedAgentLoop[Decision, dict, dict](
        cancellation_requested=lambda: True,
    ).run(
        run_id="run-1",
        decide=decide,
        execute=lambda decision, turn: AgentLoopTransition(
            status="completed",
            operation="complete",
            message="done",
        ),
    )

    assert result.status == "cancelled"
    assert called is False


def test_bounded_loop_converts_policy_exception_to_failed_result():
    def decide(turn):
        raise RuntimeError("provider failed")

    result = BoundedAgentLoop[Decision, dict, dict]().run(
        run_id="run-1",
        decide=decide,
        execute=lambda decision, turn: AgentLoopTransition(
            status="completed",
            operation="complete",
            message="done",
        ),
    )

    assert result.status == "failed"
    assert result.reason_code == "policy_error"


def test_bounded_loop_reconciles_pending_operation_without_replaying(tmp_path):
    store = JsonlAgentLoopCheckpointStore(tmp_path / "loop.jsonl")
    execute_calls = []
    first_loop = BoundedAgentLoop[Decision, dict, dict](
        checkpoint_store=store,
        checkpoint_role="robot",
    )

    def crash_after_dispatch(decision, turn):
        execute_calls.append(turn.operation_id)
        raise SystemExit("simulated process loss")

    with pytest.raises(SystemExit, match="simulated process loss"):
        first_loop.run(
            run_id="robot-1:task-1",
            checkpoint_key="robot:robot-1:task-1",
            decide=lambda turn: Decision("execute_skill"),
            execute=crash_after_dispatch,
            requires_reconciliation=lambda decision: True,
        )

    checkpoint = store.latest("robot:robot-1:task-1")
    assert checkpoint is not None
    assert checkpoint.is_recoverable
    assert checkpoint.pending_operation is not None
    assert checkpoint.pending_operation.operation_id == (
        "robot-1:task-1:operation:1"
    )

    reconciled = []

    def decide_after_resume(turn):
        return Decision("complete")

    def reconcile(pending, turn):
        reconciled.append(pending.operation_id)
        return AgentLoopTransition.continuing(
            operation=pending.operation,
            message="recovered completed skill result",
            observation={"status": "succeeded"},
        )

    result = BoundedAgentLoop[Decision, dict, dict](
        checkpoint_store=store,
        checkpoint_role="robot",
    ).run(
        run_id="robot-1:task-1",
        checkpoint_key="robot:robot-1:task-1",
        resume_checkpoint=checkpoint,
        decide=decide_after_resume,
        execute=lambda decision, turn: AgentLoopTransition(
            status="completed",
            operation=decision.operation,
            message="done",
        ),
        requires_reconciliation=lambda decision: True,
        reconcile_pending=reconcile,
    )

    assert result.status == "completed"
    assert execute_calls == ["robot-1:task-1:operation:1"]
    assert reconciled == ["robot-1:task-1:operation:1"]
    assert result.observations == ({"status": "succeeded"},)
    assert store.latest("robot:robot-1:task-1").status == "completed"


def test_unresolved_pending_operation_remains_recoverable(tmp_path):
    store = JsonlAgentLoopCheckpointStore(tmp_path / "loop.jsonl")
    loop = BoundedAgentLoop[Decision, dict, dict](
        checkpoint_store=store,
        checkpoint_role="robot",
    )

    with pytest.raises(SystemExit):
        loop.run(
            run_id="robot-1:task-1",
            checkpoint_key="robot:robot-1:task-1",
            decide=lambda turn: Decision("execute_skill"),
            execute=lambda decision, turn: (_ for _ in ()).throw(
                SystemExit("lost")
            ),
            requires_reconciliation=lambda decision: True,
        )

    checkpoint = store.latest("robot:robot-1:task-1")
    result = loop.run(
        run_id="robot-1:task-1",
        checkpoint_key="robot:robot-1:task-1",
        resume_checkpoint=checkpoint,
        decide=lambda turn: Decision("complete"),
        execute=lambda decision, turn: AgentLoopTransition(
            status="completed",
            operation="complete",
            message="must not run",
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "pending_operation_requires_reconciliation"
    latest = store.latest("robot:robot-1:task-1")
    assert latest.is_recoverable
    assert latest.pending_operation is not None


def test_checkpoint_store_rejects_corrupt_middle_record(tmp_path):
    path = tmp_path / "loop.jsonl"
    store = JsonlAgentLoopCheckpointStore(path)

    with pytest.raises(SystemExit):
        BoundedAgentLoop[Decision, dict, dict](
            checkpoint_store=store,
        ).run(
            run_id="run-1",
            checkpoint_key="run-1",
            decide=lambda turn: Decision("execute_skill"),
            execute=lambda decision, turn: (_ for _ in ()).throw(
                SystemExit("lost")
            ),
            requires_reconciliation=lambda decision: True,
        )

    with path.open("ab") as handle:
        handle.write(b"{not-json}\n")

    with pytest.raises(ValueError, match="Malformed agent loop checkpoint"):
        store.latest_by_key()


def test_checkpoint_store_ignores_incomplete_tail(tmp_path):
    path = tmp_path / "loop.jsonl"
    store = JsonlAgentLoopCheckpointStore(path)

    with pytest.raises(SystemExit):
        BoundedAgentLoop[Decision, dict, dict](
            checkpoint_store=store,
        ).run(
            run_id="run-1",
            checkpoint_key="run-1",
            decide=lambda turn: Decision("execute_skill"),
            execute=lambda decision, turn: (_ for _ in ()).throw(
                SystemExit("lost")
            ),
            requires_reconciliation=lambda decision: True,
        )

    with path.open("ab") as handle:
        handle.write(b'{"partial":')

    checkpoint = store.latest("run-1")
    assert checkpoint is not None
    assert checkpoint.pending_operation is not None

    store.append(checkpoint)

    repaired = store.latest("run-1")
    assert repaired is not None
    assert repaired.pending_operation is not None
