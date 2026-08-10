from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = (
    ROOT
    / "extensions/navigation-move-base/tests/acceptance"
)


def _load(name: str):
    path = ACCEPTANCE / f"{name}.py"
    spec = spec_from_file_location(f"fireclaw_acceptance_{name}", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_package_module(name: str):
    package_name = "fireclaw_gazebo_acceptance_tests"
    if package_name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(ACCEPTANCE)]
        sys.modules[package_name] = package
    qualified = f"{package_name}.{name}"
    existing = sys.modules.get(qualified)
    if existing is not None:
        return existing
    path = ACCEPTANCE / f"{name}.py"
    spec = spec_from_file_location(qualified, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


def test_success_scenario_is_fixed_safe_and_repository_local() -> None:
    scenario_module = _load("scenario")
    scenario = scenario_module.load_acceptance_scenario(repo_root=ROOT)

    assert scenario.scenario_id == "turtlebot3-move-base-success"
    assert scenario.scenario_type == "success"
    assert scenario.initial_pose.frame_id == "map"
    assert scenario.goal.frame_id == "map"
    assert scenario.planned_displacement_m >= scenario.minimum_displacement_m
    assert scenario.plugin_owner == "fireclaw.navigation.move-base"
    assert scenario.backend_class == "Ros1MoveBaseBackend"
    assert scenario.action_name == "/move_base"
    assert scenario.mission_authorization_status == "awaiting_confirmation"
    assert scenario.mission_authorization_raw_status == "awaiting_confirmation"
    assert scenario.terminal_status == "completed"
    assert scenario.actionlib_terminal_statuses == (3,)
    assert scenario.cancellation is None
    for path in scenario.assets.to_dict().values():
        Path(path).resolve(strict=True).relative_to(ROOT)


def test_cancel_scenario_requires_progress_and_bounded_stop_proof() -> None:
    scenario_module = _load("scenario")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/cancel.yaml",
        repo_root=ROOT,
    )

    assert scenario.scenario_id == "turtlebot3-move-base-cancel"
    assert scenario.scenario_type == "cancel"
    assert scenario.terminal_status == "cancelled"
    assert scenario.actionlib_terminal_statuses == (2, 8)
    assert scenario.cancellation is not None
    assert scenario.cancellation.minimum_feedback_count >= 1
    assert scenario.cancellation.minimum_displacement_m > 0
    assert scenario.cancellation.observation_timeout_seconds > 0
    assert scenario.cancellation.acknowledgement_timeout_seconds > 0
    assert scenario.cancellation.maximum_post_cancel_displacement_m > 0
    for path in scenario.assets.to_dict().values():
        Path(path).resolve(strict=True).relative_to(ROOT)


def test_timeout_scenario_requires_deadline_and_bounded_stop_proof() -> None:
    scenario_module = _load("scenario")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/timeout.yaml",
        repo_root=ROOT,
    )

    assert scenario.scenario_id == "turtlebot3-move-base-timeout"
    assert scenario.scenario_type == "timeout"
    assert scenario.terminal_status == "timed_out"
    assert scenario.actionlib_terminal_statuses == (2, 8)
    assert scenario.cancellation is None
    assert scenario.timeout is not None
    assert scenario.timeout.execution_timeout_seconds == 3.0
    assert scenario.timeout.minimum_feedback_count >= 1
    assert scenario.timeout.minimum_displacement_m > 0
    assert scenario.timeout.observation_timeout_seconds > 0
    assert scenario.timeout.acknowledgement_timeout_seconds > 0
    assert scenario.timeout.maximum_post_timeout_displacement_m > 0
    manifest = scenario.to_manifest()
    assert manifest["timeout"]["execution_timeout_seconds"] == 3.0
    for path in scenario.assets.to_dict().values():
        Path(path).resolve(strict=True).relative_to(ROOT)


def test_abort_scenario_requires_real_move_base_failure_proof() -> None:
    scenario_module = _load("scenario")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/abort.yaml",
        repo_root=ROOT,
    )

    assert scenario.scenario_id == "turtlebot3-move-base-abort"
    assert scenario.scenario_type == "abort"
    assert scenario.terminal_status == "failed"
    assert scenario.actionlib_terminal_statuses == (4,)
    assert scenario.cancellation is None
    assert scenario.timeout is None
    assert scenario.abort is not None
    assert scenario.abort.planner_patience_seconds == 2.0
    assert scenario.abort.recovery_behavior_enabled is False
    assert scenario.abort.minimum_feedback_count >= 1
    assert scenario.abort.terminal_timeout_seconds > 0
    assert scenario.abort.maximum_displacement_m > 0
    assert scenario.abort.required_status_text_substring == "valid plan"
    manifest = scenario.to_manifest()
    assert manifest["abort"]["planner_patience_seconds"] == 2.0
    assert manifest["abort"]["recovery_behavior_enabled"] is False
    for path in scenario.assets.to_dict().values():
        Path(path).resolve(strict=True).relative_to(ROOT)


def test_stall_recover_scenario_requires_diagnostics_and_one_retry() -> None:
    scenario_module = _load("scenario")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/stall-recover.yaml",
        repo_root=ROOT,
    )

    assert scenario.scenario_id == "turtlebot3-move-base-stall-recover"
    assert scenario.scenario_type == "stall_recover"
    assert scenario.terminal_status == "completed"
    assert scenario.actionlib_terminal_statuses == (2, 3, 8)
    assert scenario.stall is not None
    assert scenario.stall.execution_timeout_seconds > 0
    assert scenario.stall.diagnostics_timeout_seconds > 0
    assert scenario.stall.minimum_feedback_count >= 1
    assert scenario.stall.maximum_stall_displacement_m > 0
    assert scenario.stall.maximum_recovery_attempts == 1
    assert scenario.stall.stalled_parameters == {
        "max_vel_x": 0.0,
        "min_vel_x": 0.0,
    }
    assert scenario.stall.recovery_parameters == {
        "max_vel_x": 0.22,
        "min_vel_x": 0.0,
    }
    assert scenario.stall.first_actionlib_terminal_statuses == (2, 8)
    assert scenario.stall.final_actionlib_terminal_statuses == (3,)
    assert scenario.stall.required_diagnostic_finding_codes == (
        "navigation_action_failed",
    )
    assert scenario.stall.escalation_reason_code is None
    manifest = scenario.to_manifest()
    assert manifest["stall"]["maximum_recovery_attempts"] == 1


def test_stall_escalate_scenario_requires_diagnostics_without_recovery() -> None:
    scenario_module = _load("scenario")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/stall-escalate.yaml",
        repo_root=ROOT,
    )

    assert scenario.scenario_id == "turtlebot3-move-base-stall-escalate"
    assert scenario.scenario_type == "stall_escalate"
    assert scenario.terminal_status == "escalated"
    assert scenario.actionlib_terminal_statuses == (2, 8)
    assert scenario.stall is not None
    assert scenario.stall.maximum_recovery_attempts == 0
    assert scenario.stall.recovery_parameters is None
    assert scenario.stall.first_actionlib_terminal_statuses == (2, 8)
    assert scenario.stall.final_actionlib_terminal_statuses == ()
    assert scenario.stall.escalation_reason_code == (
        "persistent_navigation_stall"
    )
    assert scenario.stall.required_diagnostic_finding_codes == (
        "navigation_action_failed",
    )


def test_trusted_runner_injects_stall_only_for_exact_stall_scenarios() -> None:
    launch = (
        ROOT
        / "extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch"
    ).read_text(encoding="utf-8")
    runner = (
        ACCEPTANCE / "run_gazebo_acceptance.sh"
    ).read_text(encoding="utf-8")

    assert '<arg name="inject_stall" default="false"/>' in launch
    assert '<group if="$(arg inject_stall)">' in launch
    assert "/move_base/DWAPlannerROS/max_vel_x" in launch
    assert "/move_base/DWAPlannerROS/min_vel_x" in launch
    assert "config/acceptance/stall-recover.yaml" in runner
    assert "config/acceptance/stall-escalate.yaml" in runner
    assert "inject_stall:=true" in runner


def test_stall_recover_policy_enforces_diagnostics_before_one_recovery() -> None:
    from fireclaw_core.agent.robot_deliberation import (
        RobotAgentExecutionObservation,
    )

    scenario_module = _load_package_module("scenario")
    harness_module = _load_package_module("fireclaw_harness")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/stall-recover.yaml",
        repo_root=ROOT,
    )
    policy = harness_module.DiagnosticsFirstStallPolicy(scenario)
    envelope = SimpleNamespace(
        target={
            "frame_id": "map",
            "pose": {"x": -1.25, "y": -0.5, "yaw": 0.0},
        }
    )

    def request(*observations):
        return SimpleNamespace(
            envelope=envelope,
            observations=tuple(observations),
        )

    stalled = RobotAgentExecutionObservation(
        iteration=1,
        operation="execute_skill",
        status="timed_out",
        message="stalled",
        tool_name="navigate_to_point",
        output={"execution": {"status": "timed_out"}},
    )
    diagnostic = RobotAgentExecutionObservation(
        iteration=2,
        operation="execute_agent_tool",
        status="executed",
        message="diagnosed",
        tool_name="navigation_diagnostics",
        output={
            "status": "executed",
            "output": {
                "status": "ok",
                "evidence_id": "ros1:diagnostic:1",
                "findings": [{"code": "navigation_action_failed"}],
            },
        },
        authoritative=False,
    )
    recovery = RobotAgentExecutionObservation(
        iteration=3,
        operation="execute_agent_tool",
        status="executed",
        message="recovered",
        tool_name="move_base_set_parameters",
        output={
            "status": "executed",
            "output": {"status": "succeeded"},
        },
        authoritative=False,
    )
    succeeded = RobotAgentExecutionObservation(
        iteration=4,
        operation="execute_skill",
        status="succeeded",
        message="reached",
        tool_name="navigate_to_point",
        output={"execution": {"status": "succeeded"}},
    )

    first = policy.decide(request())
    diagnose = policy.decide(request(stalled))
    recover = policy.decide(request(stalled, diagnostic))
    retry = policy.decide(request(stalled, diagnostic, recovery))
    complete = policy.decide(
        request(stalled, diagnostic, recovery, succeeded)
    )

    assert first.operation == "execute_skill"
    assert diagnose.tool_name == "navigation_diagnostics"
    assert diagnose.tool_effect == "read"
    assert recover.tool_name == "move_base_set_parameters"
    assert recover.tool_effect == "bounded_mutation"
    assert recover.inputs == {
        "scope": "dwa",
        "parameters": {"max_vel_x": 0.22, "min_vel_x": 0.0},
    }
    assert retry.operation == "execute_skill"
    assert retry.inputs == first.inputs
    assert complete.operation == "complete"
    assert complete.evidence_ids == ("ros1:diagnostic:1",)


def test_stall_escalate_policy_never_mutates_or_retries() -> None:
    from fireclaw_core.agent.robot_deliberation import (
        RobotAgentExecutionObservation,
    )

    scenario_module = _load_package_module("scenario")
    harness_module = _load_package_module("fireclaw_harness")
    scenario = scenario_module.load_acceptance_scenario(
        ROOT
        / "extensions/navigation-move-base/config/acceptance/stall-escalate.yaml",
        repo_root=ROOT,
    )
    policy = harness_module.DiagnosticsFirstStallPolicy(scenario)
    envelope = SimpleNamespace(
        target={
            "frame_id": "map",
            "pose": {"x": -1.25, "y": -0.5, "yaw": 0.0},
        }
    )
    stalled = RobotAgentExecutionObservation(
        iteration=1,
        operation="execute_skill",
        status="timed_out",
        message="stalled",
        tool_name="navigate_to_point",
        output={},
    )
    diagnostic = RobotAgentExecutionObservation(
        iteration=2,
        operation="execute_agent_tool",
        status="executed",
        message="diagnosed",
        tool_name="navigation_diagnostics",
        output={
            "status": "executed",
            "output": {
                "status": "ok",
                "evidence_id": "ros1:diagnostic:2",
                "findings": [{"code": "navigation_action_failed"}],
            },
        },
        authoritative=False,
    )

    decision = policy.decide(
        SimpleNamespace(
            envelope=envelope,
            observations=(stalled, diagnostic),
        )
    )

    assert decision.operation == "escalate"
    assert decision.reason_code == "persistent_navigation_stall"
    assert decision.evidence_ids == ("ros1:diagnostic:2",)


def test_artifact_bundle_rejects_path_traversal_and_writes_atomic_json(
    tmp_path: Path,
) -> None:
    artifacts_module = _load("artifacts")
    bundle = artifacts_module.ArtifactBundle(
        tmp_path / "run",
        run_id="acceptance-test",
    )

    target = bundle.write_json("run-manifest.json", {"status": "prepared"})
    bundle.write_jsonl("robot-events.jsonl", [{"type": "action.started"}])

    assert target.read_text(encoding="utf-8").endswith("\n")
    assert bundle.manifest_files() == [
        "robot-events.jsonl",
        "run-manifest.json",
    ]
    with pytest.raises(ValueError, match="unsafe"):
        bundle.write_json("../escape.json", {})
    with pytest.raises(ValueError, match="unsafe"):
        artifacts_module.ArtifactBundle(tmp_path, run_id="../escape")
