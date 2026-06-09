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
            if isinstance(payload, dict) and hasattr(module, "resolve_message_class"):
                msg_cls = module.resolve_message_class(endpoint.type)
                publisher.publish(_build_ros_message(msg_cls, payload))
            else:
                publisher.publish(payload)
            return {"status": "succeeded", "response": None}
        if endpoint.interface == "service":
            service = module.create_service_proxy(endpoint.name, endpoint.type)
            if payload:
                if isinstance(payload, dict) and hasattr(module, "resolve_service_request_class"):
                    request_cls = module.resolve_service_request_class(endpoint.type)
                    request = _build_ros_message(request_cls, payload)
                else:
                    request = payload
                response = service(request)
            else:
                response = service()
            return {"status": "succeeded", "response": _response_to_data(response)}
        if endpoint.interface == "action":
            client = module.create_action_client(endpoint.name, endpoint.type)
            self.active_action_client = client
            if not client.wait_for_server(timeout=module.duration(config.wait_for_server_seconds)):
                self.active_action_client = None
                return {"status": "failed", "error": f"ROS1 action server unavailable: {endpoint.name}"}
            if isinstance(payload, dict) and hasattr(module, "resolve_action_goal_class"):
                goal_cls = module.resolve_action_goal_class(endpoint.type)
                goal = _build_ros_message(goal_cls, payload)
            else:
                goal = payload
            client.send_goal(goal, feedback_cb=self._handle_feedback)
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
            return {"status": "timeout", "error": f"ROS1 action result timeout: {endpoint.name}"}
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

    def resolve_message_class(self, type_name: str) -> Any:
        """Resolve a ROS message type name (e.g. ``geometry_msgs/Twist``) to its class."""
        return self._resolve_ros_type(type_name, preferred_module="msg")

    def resolve_service_request_class(self, service_type_name: str) -> Any:
        """Resolve a ROS service type name to its *Request class."""
        service_cls = self._resolve_ros_type(service_type_name, preferred_module="srv")
        request_cls_name = service_cls.__name__ + "Request"
        package = service_type_name.partition("/")[0]
        module = import_module(f"{package}.srv")
        return getattr(module, request_cls_name)

    def resolve_action_goal_class(self, action_type_name: str) -> Any:
        """Resolve a ROS action type name to its *Goal message class."""
        action_cls = self._resolve_ros_type(action_type_name, preferred_module="msg")
        goal_cls_name = action_cls.__name__.removesuffix("Action") + "Goal"
        # Prefer the package-level msg module (e.g. actionlib_tutorials.msg)
        # because action_cls.__module__ may point to a private submodule
        # (e.g. actionlib_tutorials.msg._FibonacciAction) that doesn't export Goal.
        package = action_type_name.partition("/")[0]
        module = import_module(f"{package}.msg")
        return getattr(module, goal_cls_name)

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


def _build_ros_message(message_cls: Any, payload: Any) -> Any:
    """Recursively build a ROS message object from a plain dict.

    Uses ``__slots__`` and ``_slot_types`` (standard ROS message attributes)
    to walk the message structure.  Nested dicts are converted to sub-message
    objects when the corresponding default value on the message instance has
    ``__slots__``.
    """
    if not isinstance(payload, dict):
        return payload
    msg = message_cls()
    slots = getattr(msg, "__slots__", ())
    slot_types = getattr(msg, "_slot_types", ())
    for field_name, field_type in zip(slots, slot_types):
        if field_name not in payload:
            continue
        value = payload[field_name]
        current = getattr(msg, field_name, None)
        if isinstance(value, dict) and hasattr(current, "__slots__"):
            setattr(msg, field_name, _build_ros_message(type(current), value))
        else:
            setattr(msg, field_name, value)
    unknown = set(payload) - set(slots)
    if unknown:
        raise ValueError(f"Unknown ROS message fields for {message_cls.__name__}: {sorted(unknown)}")
    return msg


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
