from __future__ import annotations

from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile
from fireclaw_plugin_sdk import HARDWARE_STOP_EVIDENCE_CLASS


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"
PLUGIN_ID = "fireclaw.safety.ros1-hardware"
OBSERVER_SERVICE = "fireclaw.safety.ros1-hardware.observer"
EVIDENCE_SERVICE = "fireclaw.safety.stop-evidence.ros1-hardware-safety"


@lru_cache(maxsize=1)
def _hardware_module():
    module_path = (
        EXTENSIONS
        / "ros1-hardware-safety"
        / "plugin"
        / "hardware_safety.py"
    )
    spec = spec_from_file_location(
        "fireclaw_test_ros1_hardware_safety_plugin",
        module_path,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(*, brake_required: bool = True) -> dict:
    brake = {"required": brake_required}
    if brake_required:
        brake.update(
            topic="/hardware/brake_state",
            message_type="std_msgs/Bool",
            engaged_field="data",
        )
    return {
        "enabled": True,
        "observation_timeout_seconds": 0.8,
        "max_signal_age_seconds": 5.0,
        "hold_seconds": 0.75,
        "minimum_samples": 3,
        "evidence_ttl_seconds": 15.0,
        "stop": {
            "service": "/hardware/safety/stop",
            "type": "std_srvs/SetBool",
        },
        "watchdog": {
            "topic": "/hardware/watchdog",
            "message_type": "vendor_msgs/HardwareWatchdog",
            "healthy_field": "healthy",
            "stop_asserted_field": "stop_asserted",
        },
        "emergency_stop": {
            "topic": "/hardware/emergency_stop",
            "message_type": "std_msgs/Bool",
            "field": "data",
        },
        "driver": {
            "topic": "/hardware/driver_enabled",
            "message_type": "std_msgs/Bool",
            "field": "data",
        },
        "brake": brake,
        "actuators": {
            "topic": "/hardware/joint_states",
            "message_type": "sensor_msgs/JointState",
            "expected_names": ["left_wheel", "right_wheel", "water_turret"],
            "ignored_names": ["caster_joint"],
            "velocity_threshold": 0.01,
        },
        "independent_motion": {
            "topic": "/hardware/independent_odom",
            "message_type": "nav_msgs/Odometry",
            "linear_velocity_threshold": 0.01,
            "angular_velocity_threshold": 0.02,
        },
    }


def _safe_observation() -> dict:
    actuator_sample = {
        "left_wheel": 0.0,
        "right_wheel": 0.0,
        "water_turret": 0.0,
        "caster_joint": 0.0,
    }
    motion_sample = {"linear_speed": 0.0, "angular_speed": 0.0}
    return {
        "stop_reasserted": True,
        "stop_acknowledged": True,
        "watchdog": {
            "fresh": True,
            "healthy": True,
            "stop_asserted": True,
            "sample_count": 3,
        },
        "emergency_stop": {"fresh": True, "active": True, "sample_count": 3},
        "driver": {"fresh": True, "enabled": False, "sample_count": 3},
        "brake": {"fresh": True, "engaged": True, "sample_count": 3},
        "actuators": {
            "fresh": True,
            "samples": [dict(actuator_sample) for _ in range(3)],
            "sample_span_seconds": 0.8,
        },
        "independent_motion": {
            "fresh": True,
            "samples": [dict(motion_sample) for _ in range(3)],
            "sample_span_seconds": 0.8,
        },
        "observation_errors": [],
    }


class RecordingObserver:
    def __init__(self, observation: dict | None = None) -> None:
        self.observation = observation or _safe_observation()
        self.reasons: list[str | None] = []
        self.state_observations = 0

    def collect_hardware_stop_observation(self, *, reason=None):
        self.reasons.append(reason)
        return self.observation

    def collect_hardware_state(self):
        self.state_observations += 1
        return self.observation


def _load(observer: RecordingObserver, *, mode: str = "real"):
    host = FireClawPluginHost()
    report = load_fireclaw_extensions(
        host,
        (EXTENSIONS,),
        mode=mode,  # type: ignore[arg-type]
        role="robot_agent",
        plugin_configs={PLUGIN_ID: _config()},
        services={"adapter": "ros1", OBSERVER_SERVICE: observer},
        strict=True,
    )
    return host, report


def test_real_plugin_registers_hardware_owned_evidence_provider():
    observer = RecordingObserver()
    host, report = _load(observer)

    contribution = host.get("service", EVIDENCE_SERVICE)
    assert report.ok
    assert contribution is not None
    provider = contribution.value
    assert provider.hardware_owned is True
    assert provider.evidence_class == HARDWARE_STOP_EVIDENCE_CLASS

    evidence = provider.collect_stop_evidence(
        robot_id="firebot-01",
        reason="recover frozen admission",
    )

    assert evidence["status"] == "stopped"
    assert evidence["deployment_mode"] == "real"
    assert evidence["dry_run"] is False
    assert evidence["details"]["blockers"] == []
    assert evidence["details"]["actuators"]["inventory_complete"] is True
    assert evidence["details"]["actuators"]["stationary_samples"] == 3
    assert evidence["details"]["independent_motion"]["stationary_samples"] == 3
    assert observer.reasons == ["recover frozen admission"]


def test_real_deployment_template_selects_and_activates_safety_plugin():
    template = (
        EXTENSIONS.parent
        / "examples"
        / "deployment_profiles"
        / "navigation_robot.toml.example"
    )
    with template.open("rb") as handle:
        raw = tomllib.load(handle)
    selected = raw["plugins"]["selected"]
    config = raw["plugins"]["config"][PLUGIN_ID]
    observer = RecordingObserver()
    host = FireClawPluginHost()

    report = load_fireclaw_extensions(
        host,
        (EXTENSIONS,),
        mode="real",
        role="robot_agent",
        plugin_configs={PLUGIN_ID: config},
        services={"adapter": "ros1", OBSERVER_SERVICE: observer},
        strict=True,
    )

    assert PLUGIN_ID in selected
    assert report.ok
    assert host.get("service", EVIDENCE_SERVICE) is not None


def test_profile_backed_real_gateway_discovers_qualified_provider(tmp_path):
    ros_config = (
        EXTENSIONS.parent
        / "examples"
        / "ros1_configs"
        / "gazebo_turtlebot3_move_base.yaml"
    )
    profile = tmp_path / "firebot.toml"
    profile.write_text(
        f"""
[robot]
id = "firebot-01"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "{ros_config}"
data_dir = "{tmp_path / 'data'}"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]
primitive_skills = ["navigate_to_point"]

[capability_skill_chains]
navigation = ["navigate_to_point"]
""".strip(),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    gateway = FireClawGateway(
        GatewayConfig(
            port=0,
            dry_run=False,
            robot_profile_path=str(profile),
            extension_paths=(str(EXTENSIONS),),
            plugin_configs={PLUGIN_ID: _config()},
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            runtime_state_path=str(tmp_path / "runtime.sqlite3"),
            deployment_profile=DeploymentProfile(
                mode="real",
                role="robot_agent",
                sandbox=SandboxProfile(
                    workspace_root=workspace,
                    allowed_workspace_roots=(workspace,),
                ),
            ),
        ),
        plugin_services={OBSERVER_SERVICE: RecordingObserver()},
    )

    providers = gateway.resource_admission_state()["stop_evidence_providers"]

    assert providers == [
        {
            "service_id": EVIDENCE_SERVICE,
            "owner_plugin_id": PLUGIN_ID,
            "provider_id": "ros1-hardware-safety",
            "evidence_class": HARDWARE_STOP_EVIDENCE_CLASS,
            "hardware_owned": True,
            "qualified_for_real": True,
        }
    ]


def test_provider_fails_closed_for_missing_actuator_and_measured_motion():
    module = _hardware_module()
    raw = _safe_observation()
    raw["actuators"]["samples"] = [
        {"left_wheel": 0.0, "right_wheel": 0.04}
        for _ in range(3)
    ]
    provider = module.HardwareStopEvidenceProvider(
        config=module.HardwareSafetyConfig.from_mapping(_config()),
        observer=RecordingObserver(raw),
    )

    evidence = provider.collect_stop_evidence(robot_id="firebot-01")

    assert evidence["status"] == "moving"
    assert "actuator_inventory_incomplete" in evidence["details"]["blockers"]
    assert "actuator_motion_detected" in evidence["details"]["blockers"]
    assert evidence["details"]["actuators"]["inventory_complete"] is False


def test_negative_acceptance_observation_never_invokes_stop_collection():
    observer = RecordingObserver()
    host, _ = _load(observer)
    provider = host.get("service", EVIDENCE_SERVICE).value

    evidence = provider.inspect_hardware_state(robot_id="firebot-01")

    assert evidence["evidence_class"] == "hardware_safety_observation_v1"
    assert evidence["status"] == "observed"
    assert observer.state_observations == 1
    assert observer.reasons == []


def test_provider_rejects_single_latched_hardware_state_samples():
    module = _hardware_module()
    raw = _safe_observation()
    for key in ("watchdog", "emergency_stop", "driver", "brake"):
        raw[key]["sample_count"] = 1
    provider = module.HardwareStopEvidenceProvider(
        config=module.HardwareSafetyConfig.from_mapping(_config()),
        observer=RecordingObserver(raw),
    )

    evidence = provider.collect_stop_evidence(robot_id="firebot-01")

    assert evidence["status"] == "unknown"
    assert {
        "watchdog_samples_insufficient",
        "emergency_stop_samples_insufficient",
        "driver_samples_insufficient",
        "brake_samples_insufficient",
    } <= set(evidence["details"]["blockers"])


def test_plugin_never_registers_real_hardware_witness_in_simulation():
    host, report = _load(RecordingObserver(), mode="simulation")

    assert report.ok
    assert host.get("service", EVIDENCE_SERVICE) is None


def test_config_requires_explicit_brake_policy_and_complete_bindings():
    module = _hardware_module()
    config = _config()
    del config["brake"]

    with pytest.raises(ValueError, match="brake must be an object"):
        module.HardwareSafetyConfig.from_mapping(config)


def test_ros1_observer_reasserts_stop_and_collects_all_channels(monkeypatch):
    module = _hardware_module()
    config = module.HardwareSafetyConfig.from_mapping(_config())
    clock = [100.0]
    handles: list[SimpleNamespace] = []
    service_calls: list[bool] = []

    zero_twist = SimpleNamespace(
        linear=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        angular=SimpleNamespace(x=0.0, y=0.0, z=0.0),
    )
    messages = {
        "/hardware/watchdog": [
            SimpleNamespace(healthy=True, stop_asserted=True)
            for _ in range(3)
        ],
        "/hardware/emergency_stop": [SimpleNamespace(data=True) for _ in range(3)],
        "/hardware/driver_enabled": [SimpleNamespace(data=False) for _ in range(3)],
        "/hardware/brake_state": [SimpleNamespace(data=True) for _ in range(3)],
        "/hardware/joint_states": [
            SimpleNamespace(
                name=["left_wheel", "right_wheel", "water_turret", "caster_joint"],
                velocity=[0.0, 0.0, 0.0, 0.0],
            )
            for _ in range(3)
        ],
        "/hardware/independent_odom": [
            SimpleNamespace(twist=SimpleNamespace(twist=zero_twist))
            for _ in range(3)
        ],
    }

    def subscribe(topic, _message_type, callback, queue_size):
        assert queue_size in {10, 50}
        handle = SimpleNamespace(unregistered=False)
        handle.unregister = lambda: setattr(handle, "unregistered", True)
        handles.append(handle)
        for message in messages[topic]:
            callback(message)
            if topic in {"/hardware/joint_states", "/hardware/independent_odom"}:
                clock[0] += 0.4
        return handle

    def service_proxy(_name, _service_type):
        def invoke(value):
            service_calls.append(value)
            return SimpleNamespace(success=True)

        return invoke

    rospy = SimpleNamespace(
        core=SimpleNamespace(is_initialized=lambda: False),
        init_node=lambda name, anonymous, disable_signals: None,
        get_master=lambda: SimpleNamespace(getPid=lambda: 1234),
        wait_for_service=lambda _name, timeout: None,
        ServiceProxy=service_proxy,
        Subscriber=subscribe,
        is_shutdown=lambda: False,
    )
    roslib_message = SimpleNamespace(get_message_class=lambda _name: object)
    rosservice = SimpleNamespace(
        get_service_type=lambda _name: "std_srvs/SetBool"
    )
    topic_types = {
        config.watchdog.topic: config.watchdog.message_type,
        config.emergency_stop.topic: config.emergency_stop.message_type,
        config.driver.topic: config.driver.message_type,
        config.brake.topic: config.brake.message_type,
        config.actuators.topic: config.actuators.message_type,
        config.independent_motion.topic: config.independent_motion.message_type,
    }
    rostopic = SimpleNamespace(
        get_topic_type=lambda topic, blocking: (topic_types[topic], topic, None)
    )
    std_srvs = SimpleNamespace(SetBool=object, Trigger=object)

    def fake_import(name):
        return {
            "rospy": rospy,
            "roslib.message": roslib_message,
            "rosservice": rosservice,
            "rostopic": rostopic,
            "std_srvs.srv": std_srvs,
        }[name]

    monkeypatch.setattr(module, "import_module", fake_import)
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    observer = module.Ros1HardwareSafetyObserver(config)

    preflight = observer.preflight()
    assert preflight["status"] == "ready"
    assert service_calls == []
    observation = observer.collect_hardware_stop_observation(reason="operator")
    observer.collect_hardware_state()
    provider = module.HardwareStopEvidenceProvider(config=config, observer=RecordingObserver(observation))
    evidence = provider.collect_stop_evidence(robot_id="firebot-01")

    assert service_calls == [True]
    assert evidence["status"] == "stopped"
    assert all(handle.unregistered for handle in handles)
