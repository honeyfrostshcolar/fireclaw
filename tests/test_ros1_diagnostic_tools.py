from __future__ import annotations

import sys
import threading
from pathlib import Path

from fireclaw_core.agent.ros_diagnostic_tools import (
    register_ros1_diagnostic_tool_plugin,
)
from fireclaw_core.agent.robot_deliberation import (
    RobotAgentDecision,
    RobotAgentDeliberationRuntime,
)
from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import (
    DeploymentProfile,
    SandboxProfile,
)
from fireclaw_core.ros.ros1_config import (
    Ros1DiagnosticsConfig,
    parse_ros1_adapter_config,
)
from fireclaw_core.ros.ros1_diagnostics import (
    BoundedCommandResult,
    Ros1DiagnosticPolicy,
    Ros1DiagnosticsBackend,
    SubprocessRos1CommandRunner,
)
from fireclaw_core.task.task_contract import StructuredRobotTask


class RecordingRunner:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self._lock = threading.Lock()

    def run(self, argv, *, timeout_seconds, max_output_bytes):
        command = tuple(argv)
        with self._lock:
            self.calls.append(
                (command, timeout_seconds, max_output_bytes)
            )
        response = self.responder(command)
        if isinstance(response, BoundedCommandResult):
            return response
        return BoundedCommandResult(
            argv=command,
            exit_code=0,
            output=response,
            duration_seconds=0.01,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )


class SequencePolicy:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.decisions.pop(0)


def _backend(runner) -> Ros1DiagnosticsBackend:
    return Ros1DiagnosticsBackend.from_config(
        Ros1DiagnosticsConfig(),
        robot_id="robot-1",
        runner=runner,
    )


def _runtime(
    backend: Ros1DiagnosticsBackend,
    *,
    role: str = "robot_agent",
) -> AgentToolRuntime:
    host = FireClawPluginHost()
    register_ros1_diagnostic_tool_plugin(host, backend)
    return AgentToolRuntime(
        plugin_host=host,
        profile=DeploymentProfile(
            mode="simulation",
            role=role,
            sandbox=SandboxProfile(
                enabled=False,
                workspace_root=Path("/tmp/fireclaw-ros-diagnostic-test"),
            ),
        ),
    )


def test_ros_diagnostic_tools_project_only_to_robot_without_sandbox() -> None:
    backend = _backend(RecordingRunner(lambda command: ""))

    robot_names = {
        schema["function"]["name"]
        for schema in _runtime(backend).tool_schemas()
    }
    mission_names = {
        schema["function"]["name"]
        for schema in _runtime(
            backend,
            role="mission_agent",
        ).tool_schemas()
    }

    assert robot_names == {
        "ros_topic_list",
        "ros_topic_info",
        "ros_topic_sample",
        "ros_topic_rate",
        "tf_lookup",
        "move_base_status",
        "navigation_diagnostics",
    }
    assert mission_names == set()


def test_robot_gateway_react_uses_ros_tools_without_computer_sandbox(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        lambda command: """
header:
  seq: 1
  stamp: {secs: 1, nsecs: 0}
  frame_id: laser
angle_min: -1.0
---
"""
    )
    backend = _backend(runner)
    profile = DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=SandboxProfile(
            enabled=False,
            workspace_root=tmp_path / "sandbox",
        ),
    )
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="dry-run",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            runtime_state_path=str(tmp_path / "runtime.sqlite3"),
            robot_agent_checkpoint_path=str(
                tmp_path / "checkpoints.jsonl"
            ),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            deployment_profile=profile,
        ),
        ros_diagnostics_backend=backend,
    )
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_agent_tool",
                message="inspect laser stream",
                tool_name="ros_topic_sample",
                inputs={"topic": "/scan", "sample_count": 1},
                tool_effect="read",
            ),
            RobotAgentDecision(
                operation="blocked",
                message="diagnostic-only test finished",
                reason_code="test_finished",
            ),
        ]
    )
    gateway.robot_agent_runtime = RobotAgentDeliberationRuntime(
        policy=policy
    )
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="inspect_navigation",
        command="检查激光雷达是否断流",
        target={},
        required_skills=[],
        allowed_skills=[],
        risk_level="low",
    )

    result = gateway._run_robot_agent_structured_task(
        agent=gateway._create_agent(
            task_id="task-1",
            session_id="mission-1",
        ),
        task_object=task,
        session_id="mission-1",
        task_id="task-1",
    )

    projected_names = {
        item["function"]["name"]
        for item in policy.requests[0].context["agent_tools"]
    }
    assert "ros_topic_sample" in projected_names
    assert "computer_exec" not in projected_names
    observation = policy.requests[1].observations[0]
    assert observation.authoritative is False
    assert observation.output["status"] == "executed"
    assert observation.output["output"]["frame_ids"] == ["laser"]
    audit_events = [
        event
        for event in gateway.events.events_for_task("task-1")
        if event["type"] == "agent_tool.execution"
    ]
    assert len(audit_events) == 1
    assert audit_events[0]["payload"]["tool_name"] == "ros_topic_sample"
    assert result["status"] == "blocked"


def test_ros_sample_uses_fixed_argv_and_returns_structured_advisory() -> None:
    runner = RecordingRunner(
        lambda command: """
header:
  seq: 4
  stamp:
    secs: 12
    nsecs: 34
  frame_id: laser
angle_min: -1.57
ranges: "<array type: float32, length: 360>"
---
"""
    )
    runtime = _runtime(_backend(runner))

    execution = runtime.execute(
        "ros_topic_sample",
        {
            "topic": "/scan",
            "sample_count": 1,
            "timeout_seconds": 0.5,
        },
    )

    assert execution.status == "executed"
    assert execution.result_authority == "advisory"
    output = execution.output
    assert output["status"] == "ok"
    assert output["robot_id"] == "robot-1"
    assert output["read_only"] is True
    assert output["authority"] == "advisory"
    assert output["sample_count_received"] == 1
    assert output["frame_ids"] == ["laser"]
    assert output["samples"][0]["angle_min"] == -1.57
    assert runner.calls == [
        (
            (
                "rostopic",
                "echo",
                "-n",
                "1",
                "--noarr",
                "/scan",
            ),
            0.5,
            32_768,
        )
    ]


def test_ros_policy_blocks_unallowed_or_malformed_topic_before_process() -> None:
    runner = RecordingRunner(lambda command: "")
    runtime = _runtime(_backend(runner))

    denied = runtime.execute(
        "ros_topic_sample",
        {"topic": "/private/operator_audio"},
    )
    malformed = runtime.execute(
        "ros_topic_sample",
        {"topic": "/scan;rostopic pub /cmd_vel"},
    )

    assert denied.status == "blocked"
    assert denied.error_code == "ros_diagnostic_request_denied"
    assert malformed.status == "blocked"
    assert malformed.error_code == "ros_diagnostic_request_denied"
    assert runner.calls == []


def test_ros_schema_enforces_sampling_cap_before_process() -> None:
    runner = RecordingRunner(lambda command: "")
    runtime = _runtime(_backend(runner))

    execution = runtime.execute(
        "ros_topic_sample",
        {"topic": "/scan", "sample_count": 4},
    )

    assert execution.status == "blocked"
    assert execution.error_code == "agent_tool_arguments_invalid"
    assert runner.calls == []


def test_topic_rate_accepts_expected_bounded_timeout_output() -> None:
    def respond(command):
        return BoundedCommandResult(
            argv=command,
            exit_code=-15,
            output=(
                "average rate: 9.950\n"
                "\tmin: 0.099s max: 0.102s std dev: 0.001s "
                "window: 20\n"
            ),
            duration_seconds=2.0,
            timeout_seconds=2.0,
            max_output_bytes=32_768,
            timed_out=True,
        )

    result = _backend(RecordingRunner(respond)).topic_rate(
        "/scan",
        window_seconds=2.0,
    )

    assert result["status"] == "ok"
    assert result["average_hz"] == 9.95
    assert result["sample_window_messages"] == 20
    assert result["bounded"]["timed_out"] is True


def test_topic_list_filters_policy_and_hard_limits_results() -> None:
    output = """
Published topics:
 * /scan [sensor_msgs/LaserScan] 1 publisher
 * /move_base/status [actionlib_msgs/GoalStatusArray] 1 publisher
 * /private/operator_audio [audio_common_msgs/AudioData] 1 publisher
"""
    backend = _backend(RecordingRunner(lambda command: output))

    result = backend.list_topics(limit=1)

    assert result["status"] == "ok"
    assert result["topics"] == [
        {
            "topic": "/move_base/status",
            "message_type": "actionlib_msgs/GoalStatusArray",
        }
    ]
    assert result["matched_count"] == 2
    assert result["policy_filtered"] == 1
    assert result["truncated"] is True


def test_missing_ros_executable_returns_structured_error() -> None:
    backend = Ros1DiagnosticsBackend(
        robot_id="robot-1",
        policy=Ros1DiagnosticPolicy.from_config(
            Ros1DiagnosticsConfig()
        ),
        rostopic_executable="fireclaw-no-such-rostopic",
    )

    result = backend.sample_topic(
        "/scan",
        timeout_seconds=0.1,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "ros_executable_not_found"
    assert result["read_only"] is True
    assert result["authority"] == "advisory"


def test_tf_lookup_returns_structured_transform() -> None:
    output = """
At time 42.100
- Translation: [1.250, -0.500, 0.000]
- Rotation: in Quaternion [0.000, 0.000, 0.707, 0.707]
            in RPY (radian) [0.000, 0.000, 1.571]
"""
    result = _backend(RecordingRunner(lambda command: output)).lookup_transform(
        reference_frame="map",
        target_frame="base_link",
        timeout_seconds=0.5,
    )

    assert result["status"] == "ok"
    assert result["transform"]["translation"] == {
        "x": 1.25,
        "y": -0.5,
        "z": 0.0,
    }
    assert result["transform"]["rotation"]["w"] == 0.707
    assert result["transform"]["rpy_radians"]["yaw"] == 1.571


def test_move_base_status_structures_actionlib_goal_state() -> None:
    output = """
header:
  seq: 10
  stamp: {secs: 20, nsecs: 0}
  frame_id: ''
status_list:
- goal_id:
    stamp: {secs: 19, nsecs: 0}
    id: goal-1
  status: 1
  text: driving
---
"""
    runner = RecordingRunner(lambda command: output)
    result = _backend(runner).move_base_status(timeout_seconds=0.5)

    assert result["status"] == "ok"
    assert result["navigation_state"] == "active"
    assert result["goals"] == [
        {
            "goal_id": "goal-1",
            "status_code": 1,
            "status_name": "active",
            "text": "driving",
        }
    ]
    assert "--noarr" not in runner.calls[0][0]


def test_navigation_diagnostics_reports_possible_stall() -> None:
    status = """
header: {seq: 1, stamp: {secs: 1, nsecs: 0}, frame_id: ''}
status_list:
- goal_id: {stamp: {secs: 1, nsecs: 0}, id: goal-1}
  status: 1
  text: driving
---
"""
    scan = """
header: {seq: 1, stamp: {secs: 1, nsecs: 0}, frame_id: laser}
ranges: "<array type: float32, length: 360>"
---
"""
    odom = """
header: {seq: 1, stamp: {secs: 1, nsecs: 0}, frame_id: odom}
pose:
  pose:
    position: {x: 1.0, y: 2.0, z: 0.0}
---
header: {seq: 2, stamp: {secs: 1, nsecs: 1}, frame_id: odom}
pose:
  pose:
    position: {x: 1.001, y: 2.0, z: 0.0}
---
"""
    cmd_vel = """
linear: {x: 0.2, y: 0.0, z: 0.0}
angular: {x: 0.0, y: 0.0, z: 0.0}
---
linear: {x: 0.2, y: 0.0, z: 0.0}
angular: {x: 0.0, y: 0.0, z: 0.0}
---
"""
    transform = """
At time 1.0
- Translation: [1.000, 2.000, 0.000]
- Rotation: in Quaternion [0.000, 0.000, 0.000, 1.000]
"""

    def respond(command):
        if command[:4] == (
            "rosrun",
            "tf",
            "tf_echo",
            "map",
        ):
            return transform
        topic = command[-1]
        return {
            "/move_base/status": status,
            "/scan": scan,
            "/odom": odom,
            "/cmd_vel": cmd_vel,
        }[topic]

    result = _backend(
        RecordingRunner(respond)
    ).navigation_diagnostics(timeout_seconds=0.5)

    assert result["status"] == "ok"
    assert result["overall_status"] == "degraded"
    assert {
        finding["code"] for finding in result["findings"]
    } == {"possible_navigation_stall"}
    assert result["checks"]["move_base"]["navigation_state"] == "active"
    assert result["checks"]["tf"]["status"] == "ok"


def test_subprocess_runner_hard_caps_output_and_time() -> None:
    runner = SubprocessRos1CommandRunner()

    output = runner.run(
        (sys.executable, "-c", "print('x' * 10000)"),
        timeout_seconds=1.0,
        max_output_bytes=64,
    )
    timeout = runner.run(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        timeout_seconds=0.1,
        max_output_bytes=64,
    )

    assert len(output.output.encode("utf-8")) <= 64
    assert output.truncated is True
    assert timeout.timed_out is True
    assert timeout.duration_seconds < 1.0


def test_ros1_config_parses_diagnostic_allowlists_and_limits() -> None:
    config = parse_ros1_adapter_config(
        {
            "robot_id": "robot-1",
            "diagnostics": {
                "topic_allowlist": ["/scan", "/custom/*"],
                "frame_allowlist": "map, base_link",
                "action_allowlist": ["/move_base"],
                "max_topics": 25,
                "max_samples": 2,
                "max_timeout_seconds": 1.5,
                "max_output_bytes": 8_192,
            },
        }
    )

    assert config.diagnostics.topic_allowlist == (
        "/scan",
        "/custom/*",
    )
    assert config.diagnostics.frame_allowlist == ("map", "base_link")
    assert config.diagnostics.max_samples == 2
    assert Ros1DiagnosticPolicy.from_config(
        config.diagnostics
    ).max_output_bytes == 8_192


def test_ros1_diagnostics_can_be_disabled_by_adapter_config() -> None:
    config = parse_ros1_adapter_config(
        {
            "robot_id": "robot-1",
            "diagnostics": {"enabled": False},
        }
    )

    assert Ros1DiagnosticsBackend.from_config(
        config.diagnostics,
        robot_id="robot-1",
    ) is None
