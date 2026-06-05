from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig


FeedbackSink = Callable[[dict[str, Any]], None]


@dataclass
class Ros1Transport:
    module: Any | None = None
    feedback_sink: FeedbackSink | None = None

    def execute(
        self,
        endpoint: Ros1EndpointConfig,
        payload: dict[str, Any],
        config: Ros1TransportConfig,
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
            if not client.wait_for_server(timeout=module.duration(config.wait_for_server_seconds)):
                return {"status": "failed", "error": f"ROS1 action server unavailable: {endpoint.name}"}
            client.send_goal(payload, feedback_cb=self._handle_feedback)
            if not client.wait_for_result(timeout=module.duration(config.wait_for_result_seconds)):
                if endpoint.cancel_supported and hasattr(client, "cancel_goal"):
                    client.cancel_goal()
                return {"status": "failed", "error": f"ROS1 action result timeout: {endpoint.name}"}
            return {"status": "succeeded", "response": _response_to_data(client.get_result())}
        return {"status": "failed", "error": f"Unsupported ROS1 interface: {endpoint.interface}"}

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
        module = import_module(f"{package}.{preferred_module}")
        return getattr(module, class_name)


def _response_to_data(response: Any) -> Any:
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    if hasattr(response, "__dict__"):
        return {
            key: value
            for key, value in vars(response).items()
            if not key.startswith("_")
        }
    return str(response)
