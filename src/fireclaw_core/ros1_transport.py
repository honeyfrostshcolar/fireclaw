from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import time
from typing import Any, Callable

from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig


FeedbackSink = Callable[[dict[str, Any]], None]
CancellationCheck = Callable[[], bool]


@dataclass
class Ros1Transport:
    module: Any | None = None
    feedback_sink: FeedbackSink | None = None
    active_action_client: Any | None = None

    def execute(
        self,
        endpoint: Ros1EndpointConfig,
        payload: dict[str, Any],
        config: Ros1TransportConfig,
        cancellation_requested: CancellationCheck | None = None,
    ) -> dict[str, Any]:
        if not config.enabled:
            return {
                "status": "not_configured",
                "message": "ROS1 transport is disabled.",
            }
        module = self.module or Ros1RuntimeModule.load()
        if endpoint.interface == "topic":
            publisher = module.create_publisher(endpoint.name, endpoint.type)
            publisher.publish(payload)
            return {"status": "succeeded", "response": None}
        if endpoint.interface == "service":
            service = module.create_service_proxy(endpoint.name, endpoint.type)
            response = service(payload)
            return {"status": "succeeded", "response": _response_to_data(response)}
        if endpoint.interface == "action":
            client = module.create_action_client(endpoint.name, endpoint.type)
            self.active_action_client = client
            if not client.wait_for_server(timeout=module.duration(config.wait_for_server_seconds)):
                self.active_action_client = None
                return {"status": "failed", "error": f"ROS1 action server unavailable: {endpoint.name}"}
            client.send_goal(payload, feedback_cb=self._handle_feedback)
            result_status = self._wait_for_action_result(
                client=client,
                endpoint=endpoint,
                module=module,
                wait_for_result_seconds=config.wait_for_result_seconds,
                cancellation_requested=cancellation_requested,
            )
            self.active_action_client = None
            if result_status == "succeeded":
                return {"status": "succeeded", "response": _response_to_data(client.get_result())}
            if result_status == "cancelled":
                return {"status": "cancelled", "error": f"ROS1 action cancelled: {endpoint.name}"}
            return {"status": "failed", "error": f"ROS1 action result timeout: {endpoint.name}"}
        return {"status": "failed", "error": f"Unsupported ROS1 interface: {endpoint.interface}"}

    def cancel_active_action(self) -> bool:
        client = self.active_action_client
        if client is None or not hasattr(client, "cancel_goal"):
            return False
        client.cancel_goal()
        return True

    def _wait_for_action_result(
        self,
        *,
        client: Any,
        endpoint: Ros1EndpointConfig,
        module: Any,
        wait_for_result_seconds: float,
        cancellation_requested: CancellationCheck | None,
    ) -> str:
        started = time.monotonic()
        poll_seconds = 0.05
        while True:
            if cancellation_requested is not None and cancellation_requested():
                if endpoint.cancel_supported:
                    self.cancel_active_action()
                return "cancelled"
            elapsed = time.monotonic() - started
            if elapsed >= wait_for_result_seconds:
                if endpoint.cancel_supported:
                    self.cancel_active_action()
                return "timeout"
            wait_slice = min(poll_seconds, max(0.0, wait_for_result_seconds - elapsed))
            if client.wait_for_result(timeout=module.duration(wait_slice)):
                return "succeeded"

    def _handle_feedback(self, feedback: Any) -> None:
        if self.feedback_sink is None:
            return
        self.feedback_sink(_response_to_data(feedback))


class Ros1RuntimeModule:
    @staticmethod
    def load() -> "Ros1RuntimeModule":
        try:
            rospy = import_module("rospy")
            actionlib = import_module("actionlib")
        except Exception as exc:
            raise RuntimeError("ROS1 transport requires rospy and actionlib to be installed.") from exc
        return Ros1RuntimeModule(rospy=rospy, actionlib=actionlib)

    def __init__(self, *, rospy: Any, actionlib: Any) -> None:
        self.rospy = rospy
        self.actionlib = actionlib

    def create_publisher(self, name: str, type_name: str) -> Any:
        return self.rospy.Publisher(name, self._resolve_ros_type(type_name, preferred_module="msg"), queue_size=10)

    def create_service_proxy(self, name: str, type_name: str) -> Any:
        return self.rospy.ServiceProxy(name, self._resolve_ros_type(type_name, preferred_module="srv"))

    def create_action_client(self, name: str, type_name: str) -> Any:
        return self.actionlib.SimpleActionClient(name, self._resolve_ros_type(type_name, preferred_module="msg"))

    def duration(self, seconds: float) -> Any:
        return self.rospy.Duration(seconds)

    def _resolve_ros_type(self, type_name: str, *, preferred_module: str) -> Any:
        package, _, class_name = type_name.partition("/")
        if not package or not class_name:
            raise ValueError(f"ROS type must be in package/Class form: {type_name}")
        try:
            module = import_module(f"{package}.{preferred_module}")
        except ImportError as exc:
            raise RuntimeError(
                f"ROS type package '{package}' not installed. "
                f"Install with: apt install ros-$(rosversion -d)-{package.replace('_', '-')}"
            ) from exc
        try:
            return getattr(module, class_name)
        except AttributeError as exc:
            available = [n for n in dir(module) if not n.startswith("_")]
            raise RuntimeError(
                f"ROS type '{class_name}' not found in {package}.{preferred_module}. "
                f"Available types: {available}"
            ) from exc


def _response_to_data(response: Any) -> Any:
    """Convert a ROS response to a plain dict/list/scalar.

    Prefers __slots__ (used by ROS messages) over __dict__.
    """
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    if isinstance(response, (tuple, list)):
        return [_response_to_data(item) for item in response]
    if hasattr(response, "__slots__"):
        return {
            slot: _response_to_data(getattr(response, slot))
            for slot in response.__slots__
        }
    if hasattr(response, "__dict__"):
        return {
            key: _response_to_data(value)
            for key, value in vars(response).items()
            if not key.startswith("_")
        }
    return str(response)


def validate_payload_against_type(payload: dict[str, Any], msg_class: Any) -> list[str]:
    """Check payload fields against a ROS message type's slots.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []
    if not hasattr(msg_class, "__slots__"):
        return errors  # Cannot validate without slots
    valid_fields = set(msg_class.__slots__)
    for key in payload:
        if key not in valid_fields:
            errors.append(
                f"Unknown field '{key}' for {msg_class.__name__}. "
                f"Valid fields: {sorted(valid_fields)}"
            )
    return errors
