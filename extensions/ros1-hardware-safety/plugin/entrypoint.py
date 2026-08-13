"""Register the real-robot ROS1 hardware stop evidence Provider."""

from __future__ import annotations

from fireclaw_plugin_sdk import PluginApi, stop_evidence_service_id

from .hardware_safety import (
    HardwareSafetyConfig,
    HardwareStopEvidenceProvider,
    Ros1HardwareSafetyObserver,
)


OBSERVER_SERVICE = "fireclaw.safety.ros1-hardware.observer"
STOP_EVIDENCE_SERVICE = stop_evidence_service_id("ros1-hardware-safety")


def register(api: PluginApi) -> None:
    if api.config.get("enabled", False) is not True:
        return
    if api.mode != "real" or api.role != "robot_agent":
        return
    if str(api.services.get("adapter") or "") != "ros1":
        raise ValueError(
            "fireclaw.safety.ros1-hardware requires adapter=ros1"
        )
    config = HardwareSafetyConfig.from_mapping(api.config)
    observer = api.services.get(OBSERVER_SERVICE)
    if observer is None:
        observer = Ros1HardwareSafetyObserver(config)
    if not callable(
        getattr(observer, "collect_hardware_stop_observation", None)
    ):
        raise TypeError(
            "hardware safety observer must implement "
            "collect_hardware_stop_observation"
        )
    api.register_service(
        STOP_EVIDENCE_SERVICE,
        HardwareStopEvidenceProvider(config=config, observer=observer),
    )
