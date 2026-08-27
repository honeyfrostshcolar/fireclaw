from __future__ import annotations

from functools import lru_cache
from math import cos, sin
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import sys

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.execution.action_runtime import RegisteredActionBackend, RobotActionRuntime
from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile


PLUGIN_ID = "fireclaw.navigation.move-base"
BACKEND_SERVICE = f"{PLUGIN_ID}.backend"
EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


@lru_cache(maxsize=1)
def _move_base_module():
    module_path = (
        EXTENSIONS
        / "navigation-move-base"
        / "plugin"
        / "move_base.py"
    )
    spec = spec_from_file_location("fireclaw_test_move_base_plugin", module_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RecordingMoveBaseBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.parameters: dict[str, dict] = {
            "global_planner": {},
            "dwa": {},
            "local_costmap": {},
            "global_costmap": {},
        }

    def get_status(self):
        self.calls.append({"operation": "get_status"})
        return {"status": "succeeded", "goal_active": False}

    def navigate_to_point(
        self,
        x,
        y,
        yaw=0.0,
        frame_id="map",
        feedback_sink=None,
        cancellation_requested=None,
    ):
        call = {
            "operation": "navigate_to_point",
            "x": x,
            "y": y,
            "yaw": yaw,
            "frame_id": frame_id,
        }
        self.calls.append(call)
        if feedback_sink is not None:
            feedback_sink({"progress": 1.0, "message": "goal reached"})
        if cancellation_requested is not None and cancellation_requested():
            return {
                "status": "cancelled",
                "cancelled": True,
                "cancellation_acknowledged": True,
                "runtime_stopped": True,
                **call,
            }
        return {"status": "succeeded", "goal_reached": True, **call}

    def get_parameters(self, scope, names=None):
        values = dict(self.parameters[scope])
        if names:
            values = {name: values.get(name) for name in names}
        return {"status": "succeeded", "scope": scope, "parameters": values}

    def set_parameters(self, scope, parameters):
        self.parameters[scope].update(parameters)
        return {"status": "succeeded", "scope": scope, "updated": dict(parameters)}

    def cancel_navigation(self, reason=None):
        return {"status": "succeeded", "cancelled": True, "reason": reason}

    def clear_costmaps(self, scope="both"):
        return {"status": "succeeded", "cleared": True, "scope": scope}

    provider_id = "recording-move-base"

    def collect_stop_evidence(self, *, robot_id, reason=None):
        self.calls.append(
            {
                "operation": "collect_stop_evidence",
                "robot_id": robot_id,
                "reason": reason,
            }
        )
        return {
            "provider_id": self.provider_id,
            "robot_id": robot_id,
            "status": "stopped",
            "deployment_mode": "simulation",
            "dry_run": False,
            "observed_at": "2026-08-12T10:00:00+00:00",
            "expires_at": "2026-08-12T10:00:15+00:00",
            "details": {"runtime": "recording_move_base"},
        }


def _profile(tmp_path: Path, mode: str) -> DeploymentProfile:
    profile_mode = "real" if mode.startswith("real") else "simulation"
    root = tmp_path / mode
    return DeploymentProfile(
        mode=profile_mode,  # type: ignore[arg-type]
        role="robot_agent",
        sandbox=SandboxProfile(
            workspace_root=root,
            allowed_workspace_roots=(root,),
        ),
    )


def _load(
    host: FireClawPluginHost,
    backend: RecordingMoveBaseBackend,
    *,
    mode: str,
    config: dict | None = None,
):
    return load_fireclaw_extensions(
        host,
        (EXTENSIONS,),
        mode=mode,  # type: ignore[arg-type]
        role="robot_agent",
        plugin_configs={PLUGIN_ID: dict(config or {})},
        services={"adapter": "dry-run", BACKEND_SERVICE: backend},
        strict=True,
    )


def test_simulation_registers_catalog_and_bounded_tools(tmp_path: Path) -> None:
    host = FireClawPluginHost()
    backend = RecordingMoveBaseBackend()
    report = _load(host, backend, mode="simulation")

    assert PLUGIN_ID in {record.plugin_id for record in report.loaded}
    assert {
        "move_base_parameter_catalog",
        "move_base_navigation_status",
        "move_base_get_parameters",
        "move_base_set_parameters",
        "move_base_cancel_navigation",
        "move_base_clear_costmaps",
    } <= {item.contribution_id for item in host.contributions("tool")}

    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path, "simulation"),
    )
    result = runtime.execute(
        "move_base_set_parameters",
        {"scope": "dwa", "parameters": {"max_vel_x": 0.35, "sim_time": 2.0}},
    )
    assert result.status == "executed"
    assert backend.parameters["dwa"]["max_vel_x"] == 0.35


def test_simulation_registers_trusted_stop_evidence_service_but_real_does_not():
    simulation_host = FireClawPluginHost()
    backend = RecordingMoveBaseBackend()
    _load(simulation_host, backend, mode="simulation")

    service = simulation_host.get(
        "service",
        "fireclaw.safety.stop-evidence.navigation-move-base",
    )
    assert service is not None
    assert service.owner_plugin_id == PLUGIN_ID
    assert service.value is backend

    real_host = FireClawPluginHost()
    _load(real_host, backend, mode="real")
    assert real_host.get(
        "service",
        "fireclaw.safety.stop-evidence.navigation-move-base",
    ) is None


def test_parameter_policy_rejects_unknown_or_unsafe_updates(tmp_path: Path) -> None:
    host = FireClawPluginHost()
    backend = RecordingMoveBaseBackend()
    _load(host, backend, mode="simulation")
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path, "simulation-invalid"),
    )

    unknown = runtime.execute(
        "move_base_set_parameters",
        {"scope": "dwa", "parameters": {"controller_frequency": 10.0}},
    )
    unsafe = runtime.execute(
        "move_base_set_parameters",
        {"scope": "dwa", "parameters": {"max_vel_x": 100.0}},
    )
    catalog = runtime.execute("move_base_parameter_catalog", {})

    assert unknown.output["status"] == "blocked"
    assert unknown.output["error_code"] == "move_base_parameter_policy_rejected"
    assert unsafe.status == "blocked"
    assert unsafe.error_code == "agent_tool_arguments_invalid"
    assert any(
        item["name"] == "max_vel_x" and item["mutable"]
        for item in catalog.output["parameters"]
    )


def test_real_mode_hides_mutation_by_default_and_requires_approval_when_opened(
    tmp_path: Path,
) -> None:
    backend = RecordingMoveBaseBackend()
    host = FireClawPluginHost()
    _load(host, backend, mode="real")
    runtime = AgentToolRuntime(plugin_host=host, profile=_profile(tmp_path, "real"))
    assert runtime.projection("move_base_set_parameters") is None
    assert runtime.projection("move_base_get_parameters") is not None

    approved_host = FireClawPluginHost()
    _load(
        approved_host,
        backend,
        mode="real",
        config={
            "real_mutation_enabled": True,
            "real_mutable_parameters": ["max_vel_x"],
        },
    )
    approved_runtime = AgentToolRuntime(
        plugin_host=approved_host,
        profile=_profile(tmp_path, "real-approved"),
    )
    projection = approved_runtime.projection("move_base_set_parameters")
    assert projection is not None
    assert projection.decision.status == "require_approval"


def test_simulation_tool_schema_exposes_named_parameters() -> None:
    host = FireClawPluginHost()
    _load(host, RecordingMoveBaseBackend(), mode="simulation")
    tool = host.get("tool", "move_base_set_parameters").value
    properties = tool.input_schema["properties"]["parameters"]["properties"]
    assert properties["max_vel_x"]["minimum"] == 0.0
    assert "robot_base_frame" not in properties


def test_navigation_motion_is_plugin_owned_and_never_adapter_dispatched() -> None:
    host = FireClawPluginHost()
    backend = RecordingMoveBaseBackend()
    report = _load(host, backend, mode="simulation")
    contribution = host.get("physical_capability", "navigate_to_point")
    assert contribution is not None
    assert contribution.owner_plugin_id == PLUGIN_ID
    assert PLUGIN_ID in {record.plugin_id for record in report.loaded}
    assert contribution.value.timeout_seconds == 360.0
    assert contribution.value.cancellation_ack_timeout_seconds == 2.0

    robot = DryRunRobotAdapter(robot_id="plugin-navigation")
    assert not hasattr(robot, "navigate_to_point")
    runtime = RobotActionRuntime(backend=RegisteredActionBackend(robot))
    registry = SkillRegistry(skills={}, host=host)
    skill = registry.register_plugin(
        contribution.value,
        robot=robot,
        action_runtime=runtime,
    )

    result = skill.run({"x": 1.0, "y": 2.0})

    assert result.ok is True
    assert result.action == "navigate_to_point"
    assert backend.calls[0]["operation"] == "navigate_to_point"


def test_navigation_deadline_is_trusted_plugin_configuration() -> None:
    host = FireClawPluginHost()
    backend = RecordingMoveBaseBackend()
    _load(
        host,
        backend,
        mode="simulation",
        config={
            "navigate_timeout_seconds": 3.0,
            "cancellation_ack_timeout_seconds": 5.0,
        },
    )

    contribution = host.get("physical_capability", "navigate_to_point")
    assert contribution is not None
    assert contribution.value.timeout_seconds == 3.0
    assert contribution.value.cancellation_ack_timeout_seconds == 5.0


class FakeMoveBaseActionClient:
    def __init__(
        self,
        *,
        acknowledge_cancel: bool,
        terminal_state: int | None = None,
        goal_status_text: str = "",
        server_available: bool = True,
    ) -> None:
        self.acknowledge_cancel = acknowledge_cancel
        self.terminal_state = terminal_state
        self.goal_status_text = goal_status_text
        self.server_available = server_available
        self.cancelled = False
        self.cancel_calls = 0
        self.calls = []

    def wait_for_server(self, timeout=None):
        self.calls.append("wait_for_server")
        return self.server_available

    def send_goal(self, _goal, feedback_cb=None):
        self.calls.append("send_goal")
        self.feedback_cb = feedback_cb

    def wait_for_result(self, _timeout=None):
        if self.terminal_state is not None:
            return True
        return self.cancelled and self.acknowledge_cancel

    def cancel_goal(self):
        self.cancelled = True
        self.cancel_calls += 1

    def cancel_all_goals(self):
        self.cancel_goal()

    def get_state(self):
        if self.terminal_state is not None:
            return self.terminal_state
        return 2 if self.acknowledge_cancel else 1

    def get_goal_status_text(self):
        return self.goal_status_text


def _fake_ros_import(action_client: FakeMoveBaseActionClient):
    class MoveBaseGoal:
        def __init__(self):
            self.target_pose = SimpleNamespace(
                header=SimpleNamespace(frame_id=None, stamp=None),
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=None, y=None),
                    orientation=SimpleNamespace(z=None, w=None),
                ),
            )

    initialized = False
    init_calls = []

    def init_node(name, *, anonymous, disable_signals):
        nonlocal initialized
        initialized = True
        init_calls.append(
            {
                "name": name,
                "anonymous": anonymous,
                "disable_signals": disable_signals,
            }
        )

    rospy = SimpleNamespace(
        Duration=lambda seconds: seconds,
        Time=SimpleNamespace(now=lambda: 123.0),
        core=SimpleNamespace(is_initialized=lambda: initialized),
        init_node=init_node,
        init_calls=init_calls,
    )
    messages = SimpleNamespace(
        MoveBaseGoal=MoveBaseGoal,
        MoveBaseAction=object,
    )
    def simple_action_client(_name, _action):
        assert initialized, "rospy must be initialized before creating an action client"
        return action_client

    actionlib = SimpleNamespace(SimpleActionClient=simple_action_client)

    def load(name):
        return {
            "rospy": rospy,
            "move_base_msgs.msg": messages,
            "actionlib": actionlib,
        }[name]

    load.rospy = rospy
    return load


def test_ros1_move_base_initializes_gateway_node_once(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        terminal_state=3,
    )
    fake_import = _fake_ros_import(action_client)
    monkeypatch.setattr(module, "import_module", fake_import)
    backend = module.Ros1MoveBaseBackend()

    backend.navigate_to_point(1.0, 2.0)
    backend.navigate_to_point(2.0, 3.0)

    assert fake_import.rospy.init_calls == [
        {
            "name": "fireclaw_gateway",
            "anonymous": True,
            "disable_signals": True,
        }
    ]
    assert action_client.calls[:2] == ["wait_for_server", "send_goal"]


def test_ros1_move_base_status_initializes_before_action_client(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        terminal_state=3,
    )
    fake_import = _fake_ros_import(action_client)
    monkeypatch.setattr(module, "import_module", fake_import)
    backend = module.Ros1MoveBaseBackend()

    result = backend.get_status()

    assert result["status"] == "succeeded"
    assert result["goal_state"] == 3
    assert len(fake_import.rospy.init_calls) == 1


def test_ros1_move_base_does_not_send_goal_before_server_is_ready(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        server_available=False,
    )
    monkeypatch.setattr(module, "import_module", _fake_ros_import(action_client))
    backend = module.Ros1MoveBaseBackend()

    result = backend.navigate_to_point(1.0, 2.0)

    assert action_client.calls == ["wait_for_server"]
    assert result["status"] == "error"
    assert result["error_code"] == "move_base_unavailable"
    assert result["runtime_stopped"] is True
    assert result["resource_release_safe"] is True


def test_ros1_move_base_cancel_waits_for_terminal_acknowledgement(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(acknowledge_cancel=True)
    monkeypatch.setattr(module, "import_module", _fake_ros_import(action_client))
    backend = module.Ros1MoveBaseBackend(
        cancellation_ack_timeout_seconds=0.1,
    )

    result = backend.navigate_to_point(
        1.0,
        2.0,
        cancellation_requested=lambda: True,
    )

    assert action_client.cancel_calls == 1
    assert result["status"] == "cancelled"
    assert result["cancellation_acknowledged"] is True
    assert result["runtime_stopped"] is True
    assert result["goal_state"] == 2


def test_ros1_move_base_cancel_fails_safe_without_acknowledgement(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(acknowledge_cancel=False)
    monkeypatch.setattr(module, "import_module", _fake_ros_import(action_client))
    backend = module.Ros1MoveBaseBackend(
        cancellation_ack_timeout_seconds=0.01,
    )

    result = backend.navigate_to_point(
        1.0,
        2.0,
        cancellation_requested=lambda: True,
    )

    assert action_client.cancel_calls == 1
    assert result["status"] == "lost"
    assert result["cancellation_acknowledged"] is False
    assert result["runtime_stopped"] is False
    assert result["resource_release_safe"] is False


def test_ros1_move_base_aborted_preserves_actionlib_failure_evidence(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        terminal_state=4,
        goal_status_text=(
            "Failed to find a valid plan. Even after executing recovery "
            "behaviors."
        ),
    )
    monkeypatch.setattr(module, "import_module", _fake_ros_import(action_client))
    backend = module.Ros1MoveBaseBackend()

    result = backend.navigate_to_point(9.5, 9.5)

    assert result["status"] == "aborted"
    assert result["error_code"] == "move_base_aborted"
    assert "valid plan" in result["goal_status_text"]
    assert result["goal_state"] == 4
    assert result["goal_state_name"] == "aborted"
    assert result["runtime_stopped"] is True
    assert result["resource_release_safe"] is True


def test_ros1_explicit_cancel_tool_also_requires_acknowledgement(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(acknowledge_cancel=True)
    monkeypatch.setattr(module, "import_module", _fake_ros_import(action_client))
    backend = module.Ros1MoveBaseBackend(
        cancellation_ack_timeout_seconds=0.1,
    )

    result = backend.cancel_navigation("operator requested stop")

    assert result["status"] == "succeeded"
    assert result["cancelled"] is True
    assert result["cancellation_acknowledged"] is True
    assert result["runtime_stopped"] is True


def test_ros1_stop_evidence_reasserts_zero_velocity_and_observes_stationarity(
    monkeypatch,
):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(acknowledge_cancel=True)

    class Twist:
        def __init__(self):
            self.linear = SimpleNamespace(x=0.0, y=0.0, z=0.0)
            self.angular = SimpleNamespace(x=0.0, y=0.0, z=0.0)

    odometry = SimpleNamespace(
        twist=SimpleNamespace(
            twist=SimpleNamespace(
                linear=SimpleNamespace(x=0.0, y=0.0, z=0.0),
                angular=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            )
        )
    )
    goal_status = SimpleNamespace(status_list=[])

    class Handle:
        def __init__(self):
            self.unregistered = False

        def unregister(self):
            self.unregistered = True

    class Publisher(Handle):
        def __init__(self):
            super().__init__()
            self.messages = []

        def publish(self, message):
            self.messages.append(message)

    publisher = Publisher()
    subscriptions = []

    def subscribe(topic, _message_type, callback, queue_size):
        assert queue_size == 10
        handle = Handle()
        subscriptions.append(handle)
        callback(odometry if topic == "/odom" else goal_status)
        return handle

    rospy = SimpleNamespace(
        Duration=lambda seconds: seconds,
        Publisher=lambda *_args, **_kwargs: publisher,
        Subscriber=subscribe,
    )

    def fake_import(name):
        return {
            "geometry_msgs.msg": SimpleNamespace(Twist=Twist),
            "nav_msgs.msg": SimpleNamespace(Odometry=object),
            "actionlib_msgs.msg": SimpleNamespace(GoalStatusArray=object),
        }[name]

    monkeypatch.setattr(module, "import_module", fake_import)
    backend = module.Ros1MoveBaseBackend(
        stop_evidence_timeout_seconds=0.1,
        stop_evidence_hold_seconds=0.0,
        stop_evidence_minimum_samples=1,
    )
    monkeypatch.setattr(backend, "_rospy", lambda: rospy)
    monkeypatch.setattr(backend, "_action", lambda: action_client)

    result = backend.collect_stop_evidence(
        robot_id="gazebo_turtlebot3",
        reason="recover frozen admission",
    )

    assert result["status"] == "stopped"
    assert result["robot_id"] == "gazebo_turtlebot3"
    assert result["dry_run"] is False
    assert result["details"]["active_goal_count"] == 0
    assert result["details"]["stationary_samples"] == 1
    assert action_client.cancel_calls == 1
    assert len(publisher.messages) >= 1
    assert publisher.unregistered is True
    assert all(handle.unregistered for handle in subscriptions)


def _make_map_transform(x: float, y: float, yaw: float, frame: str = "map"):
    return SimpleNamespace(
        header=SimpleNamespace(frame_id=frame),
        transform=SimpleNamespace(
            translation=SimpleNamespace(x=x, y=y, z=0.0),
            rotation=SimpleNamespace(
                x=0.0,
                y=0.0,
                z=sin(yaw / 2.0),
                w=cos(yaw / 2.0),
            ),
        ),
    )


def _fake_ros_import_with_tf(action_client, transform):
    """Fake ROS import stack including tf2_ros for measured-pose lookups."""
    initialized = []

    class FakeTime:
        def __init__(self, value=0):
            self.value = value

        @staticmethod
        def now():
            return FakeTime(123.0)

    rospy = SimpleNamespace(
        Duration=lambda seconds: seconds,
        Time=FakeTime,
        core=SimpleNamespace(is_initialized=lambda: bool(initialized)),
        init_node=lambda name, *, anonymous, disable_signals: initialized.append(name),
    )

    class FakeBuffer:
        def lookup_transform(self, target_frame, source_frame, stamp, timeout=None):
            assert (target_frame, source_frame) == ("map", "base_footprint")
            if transform is None:
                raise RuntimeError("no transform data")
            return transform

    tf2_ros = SimpleNamespace(
        Buffer=FakeBuffer,
        TransformListener=lambda buffer: SimpleNamespace(buffer=buffer),
    )

    class MoveBaseGoal:
        def __init__(self):
            self.target_pose = SimpleNamespace(
                header=SimpleNamespace(frame_id=None, stamp=None),
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=None, y=None),
                    orientation=SimpleNamespace(z=None, w=None),
                ),
            )

    def load(name):
        return {
            "rospy": rospy,
            "move_base_msgs.msg": SimpleNamespace(
                MoveBaseGoal=MoveBaseGoal, MoveBaseAction=object
            ),
            "actionlib": SimpleNamespace(
                SimpleActionClient=lambda name, action: action_client
            ),
            "tf2_ros": tf2_ros,
        }[name]

    return load


def test_ros1_move_base_success_reports_measured_pose(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        terminal_state=3,
    )
    monkeypatch.setattr(
        module,
        "import_module",
        _fake_ros_import_with_tf(
            action_client,
            transform=_make_map_transform(0.66, 0.49, -0.02),
        ),
    )
    backend = module.Ros1MoveBaseBackend(pose_lookup_timeout_seconds=0.1)

    result = backend.navigate_to_point(0.63, 0.54)

    assert result["goal_reached"] is True
    pose = result["pose"]
    assert pose["x"] == 0.66
    assert pose["y"] == 0.49
    assert abs(pose["yaw"] - (-0.02)) < 1e-6
    assert pose["frame_id"] == "map"


def test_ros1_move_base_omits_pose_when_tf_has_no_data(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        terminal_state=3,
    )
    monkeypatch.setattr(
        module,
        "import_module",
        _fake_ros_import_with_tf(action_client, transform=None),
    )
    backend = module.Ros1MoveBaseBackend(pose_lookup_timeout_seconds=0.1)

    result = backend.navigate_to_point(0.63, 0.54)

    # Fail honest: without TF evidence the result must not claim a pose.
    assert result["goal_reached"] is True
    assert "pose" not in result


def test_ros1_move_base_failure_does_not_claim_measured_pose(monkeypatch):
    module = _move_base_module()
    action_client = FakeMoveBaseActionClient(
        acknowledge_cancel=False,
        terminal_state=4,
    )
    monkeypatch.setattr(
        module,
        "import_module",
        _fake_ros_import_with_tf(
            action_client,
            transform=_make_map_transform(0.66, 0.49, 0.0),
        ),
    )
    backend = module.Ros1MoveBaseBackend(pose_lookup_timeout_seconds=0.1)

    result = backend.navigate_to_point(9.0, 9.0)

    assert result["goal_reached"] is False
    assert "pose" not in result
