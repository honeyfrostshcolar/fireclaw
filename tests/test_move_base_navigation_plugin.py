from __future__ import annotations

from functools import lru_cache
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
    assert contribution.value.timeout_seconds == 120.0
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
    ) -> None:
        self.acknowledge_cancel = acknowledge_cancel
        self.terminal_state = terminal_state
        self.goal_status_text = goal_status_text
        self.cancelled = False
        self.cancel_calls = 0

    def send_goal(self, _goal, feedback_cb=None):
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

    rospy = SimpleNamespace(
        Duration=lambda seconds: seconds,
        Time=SimpleNamespace(now=lambda: 123.0),
    )
    messages = SimpleNamespace(
        MoveBaseGoal=MoveBaseGoal,
        MoveBaseAction=object,
    )
    actionlib = SimpleNamespace(
        SimpleActionClient=lambda _name, _action: action_client,
    )

    def load(name):
        return {
            "rospy": rospy,
            "move_base_msgs.msg": messages,
            "actionlib": actionlib,
        }[name]

    return load


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
