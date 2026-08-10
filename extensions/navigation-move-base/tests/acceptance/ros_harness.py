"""Read-only ROS probes and evidence capture for Gazebo acceptance."""

from __future__ import annotations

from datetime import datetime, timezone
from math import atan2, cos, hypot, sin
import os
import subprocess
from threading import Lock
from time import monotonic, sleep
from typing import Any

from .scenario import AcceptanceScenario, Pose2D


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RosHarness:
    def __init__(self, scenario: AcceptanceScenario) -> None:
        self.scenario = scenario
        try:
            import actionlib
            import rospy
            import tf2_ros
            from move_base_msgs.msg import MoveBaseAction
        except ImportError as exc:
            raise RuntimeError(
                "ROS1 Python modules are unavailable; source the Noetic and "
                "FireClaw/TurtleBot3 workspaces before running acceptance"
            ) from exc
        self.actionlib = actionlib
        self.rospy = rospy
        self.tf2_ros = tf2_ros
        self.MoveBaseAction = MoveBaseAction
        if not rospy.core.is_initialized():
            rospy.init_node(
                "fireclaw_gazebo_acceptance",
                anonymous=True,
                disable_signals=True,
            )
        self.tf_buffer = tf2_ros.Buffer(cache_time=rospy.Duration(30.0))
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)

    def wait_until_ready(self) -> dict[str, Any]:
        deadline = monotonic() + self.scenario.ros_readiness_seconds
        master_uri = os.getenv("ROS_MASTER_URI", "")
        if not (
            master_uri.startswith("http://127.0.0.1:")
            or master_uri.startswith("http://localhost:")
        ):
            raise RuntimeError(
                "Gazebo acceptance requires a loopback ROS_MASTER_URI"
            )
        try:
            _code, _message, master_pid = self.rospy.get_master().getPid()
        except Exception as exc:
            raise RuntimeError("ROS master is not reachable") from exc

        topic_samples: dict[str, dict[str, Any]] = {}
        topic_types = {
            "/clock": ("rosgraph_msgs.msg", "Clock"),
            "/scan": ("sensor_msgs.msg", "LaserScan"),
            "/odom": ("nav_msgs.msg", "Odometry"),
        }
        for topic in self.scenario.readiness_topics:
            module_name, type_name = topic_types[topic]
            module = __import__(module_name, fromlist=[type_name])
            message_type = getattr(module, type_name)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise RuntimeError(f"ROS readiness timed out before {topic}")
            try:
                message = self.rospy.wait_for_message(
                    topic,
                    message_type,
                    timeout=remaining,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"required ROS topic did not produce a message: {topic}"
                ) from exc
            topic_samples[topic] = _message_summary(topic, message)

        action_client = self.actionlib.SimpleActionClient(
            self.scenario.action_name,
            self.MoveBaseAction,
        )
        remaining = deadline - monotonic()
        if remaining <= 0 or not action_client.wait_for_server(
            self.rospy.Duration(remaining)
        ):
            raise RuntimeError(
                f"action server is not ready: {self.scenario.action_name}"
            )

        transforms: list[dict[str, Any]] = []
        for parent, child in self.scenario.readiness_transforms:
            transform = self._wait_for_transform(parent, child, deadline)
            transforms.append(_transform_summary(transform))

        return {
            "status": "ready",
            "checked_at": _utc_now(),
            "ros_master_uri": master_uri,
            "master_pid": master_pid,
            "action_server": self.scenario.action_name,
            "topics": topic_samples,
            "transforms": transforms,
        }

    def current_pose(self) -> Pose2D:
        deadline = monotonic() + min(
            10.0,
            self.scenario.ros_readiness_seconds,
        )
        transform = self._wait_for_transform("map", "base_link", deadline)
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return Pose2D(
            frame_id="map",
            x=float(translation.x),
            y=float(translation.y),
            yaw=_yaw_from_quaternion(rotation),
        )

    def map_bounds(self) -> dict[str, Any]:
        from nav_msgs.msg import OccupancyGrid

        message = self.rospy.wait_for_message(
            "/map",
            OccupancyGrid,
            timeout=min(10.0, self.scenario.ros_readiness_seconds),
        )
        resolution = float(message.info.resolution)
        width = int(message.info.width)
        height = int(message.info.height)
        origin = message.info.origin
        origin_yaw = _yaw_from_quaternion(origin.orientation)
        cos_yaw = cos(origin_yaw)
        sin_yaw = sin(origin_yaw)
        local_corners = (
            (0.0, 0.0),
            (width * resolution, 0.0),
            (0.0, height * resolution),
            (width * resolution, height * resolution),
        )
        corners = [
            {
                "x": float(origin.position.x) + x * cos_yaw - y * sin_yaw,
                "y": float(origin.position.y) + x * sin_yaw + y * cos_yaw,
            }
            for x, y in local_corners
        ]
        xs = [corner["x"] for corner in corners]
        ys = [corner["y"] for corner in corners]
        return {
            "captured_at": _utc_now(),
            "topic": "/map",
            "frame_id": str(message.header.frame_id),
            "resolution_m_per_cell": resolution,
            "width_cells": width,
            "height_cells": height,
            "origin": {
                "x": float(origin.position.x),
                "y": float(origin.position.y),
                "yaw": origin_yaw,
            },
            "corners": corners,
            "axis_aligned_bounds": {
                "min_x": min(xs),
                "max_x": max(xs),
                "min_y": min(ys),
                "max_y": max(ys),
            },
        }

    def navigation_parameters(self) -> dict[str, Any]:
        from dynamic_reconfigure.client import Client

        planner_patience = self.rospy.get_param(
            "/move_base/planner_patience"
        )
        recovery_enabled = self.rospy.get_param(
            "/move_base/recovery_behavior_enabled"
        )
        if isinstance(planner_patience, bool) or not isinstance(
            planner_patience,
            (int, float),
        ):
            raise AssertionError(
                "/move_base/planner_patience is not numeric"
            )
        if not isinstance(recovery_enabled, bool):
            raise AssertionError(
                "/move_base/recovery_behavior_enabled is not boolean"
            )
        dwa_configuration = Client(
            "/move_base/DWAPlannerROS",
            timeout=min(5.0, self.scenario.ros_readiness_seconds),
        ).get_configuration()
        max_vel_x = dwa_configuration.get("max_vel_x")
        min_vel_x = dwa_configuration.get("min_vel_x")
        for name, value in (
            ("max_vel_x", max_vel_x),
            ("min_vel_x", min_vel_x),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                (int, float),
            ):
                raise AssertionError(
                    f"/move_base/DWAPlannerROS/{name} is not numeric"
                )
        return {
            "captured_at": _utc_now(),
            "planner_patience_seconds": float(planner_patience),
            "recovery_behavior_enabled": recovery_enabled,
            "dwa": {
                "namespace": "/move_base/DWAPlannerROS",
                "max_vel_x": float(max_vel_x),
                "min_vel_x": float(min_vel_x),
            },
        }

    def wait_until_stopped(self) -> dict[str, Any]:
        from nav_msgs.msg import Odometry

        deadline = monotonic() + self.scenario.stopped_seconds
        consecutive = 0
        last: dict[str, Any] | None = None
        while monotonic() < deadline:
            remaining = max(0.01, deadline - monotonic())
            try:
                message = self.rospy.wait_for_message(
                    "/odom",
                    Odometry,
                    timeout=min(1.0, remaining),
                )
            except Exception:
                continue
            linear = abs(float(message.twist.twist.linear.x))
            angular = abs(float(message.twist.twist.angular.z))
            last = {
                "observed_at": _utc_now(),
                "linear_x_mps": linear,
                "angular_z_rps": angular,
            }
            if (
                linear <= self.scenario.stopped_linear_velocity_mps
                and angular <= self.scenario.stopped_angular_velocity_rps
            ):
                consecutive += 1
                if consecutive >= 3:
                    return {"status": "stopped", **last}
            else:
                consecutive = 0
        raise AssertionError(
            "robot did not produce three consecutive stopped odometry samples; "
            f"last={last}"
        )

    def wait_for_navigation_progress(
        self,
        recorder: "RosEvidenceRecorder",
        *,
        initial_pose: Pose2D,
    ) -> tuple[Pose2D, dict[str, Any]]:
        expectation = self.scenario.cancellation or self.scenario.timeout
        if expectation is None:
            raise ValueError(
                "navigation progress observation requires a cancel or "
                "timeout scenario"
            )
        deadline = monotonic() + expectation.observation_timeout_seconds
        last: dict[str, Any] = {}
        while monotonic() < deadline:
            goals = recorder.goals()
            if not goals:
                sleep(0.05)
                continue
            goal_id = str(goals[-1]["goal_id"])
            feedback = [
                item
                for item in recorder.feedback()
                if str(item.get("goal_id")) == goal_id
            ]
            pose = self.current_pose()
            displacement = hypot(
                pose.x - initial_pose.x,
                pose.y - initial_pose.y,
            )
            last = {
                "observed_at": _utc_now(),
                "goal_id": goal_id,
                "feedback_count": len(feedback),
                "displacement_m": displacement,
                "pose": _pose_dict(pose),
            }
            if (
                len(feedback) >= expectation.minimum_feedback_count
                and displacement >= expectation.minimum_displacement_m
            ):
                return pose, last
            if any(
                item.get("goal_id") == goal_id
                and item.get("status") in self.scenario.actionlib_terminal_statuses
                for item in recorder.statuses()
            ):
                raise AssertionError(
                    "move_base reached a terminal state before the configured "
                    f"{self.scenario.scenario_type} progress proof: {last}"
                )
            sleep(0.05)
        raise TimeoutError(
            "navigation did not produce the configured live progress proof; "
            f"last={last}"
        )

    def graph_snapshot(self) -> dict[str, Any]:
        commands = {
            "nodes": ["rosnode", "list"],
            "topics": ["rostopic", "list", "-v"],
            "use_sim_time": ["rosparam", "get", "/use_sim_time"],
        }
        result: dict[str, Any] = {
            "captured_at": _utc_now(),
            "ros_master_uri": os.getenv("ROS_MASTER_URI"),
            "ros_distro": os.getenv("ROS_DISTRO"),
        }
        for name, argv in commands.items():
            try:
                completed = subprocess.run(
                    argv,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except Exception as exc:
                result[name] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            else:
                result[name] = {
                    "status": "succeeded",
                    "output": completed.stdout,
                }
        return result

    def recorder(self) -> "RosEvidenceRecorder":
        return RosEvidenceRecorder(self.rospy)

    def _wait_for_transform(
        self,
        parent: str,
        child: str,
        deadline: float,
    ) -> Any:
        last_error: Exception | None = None
        while monotonic() < deadline and not self.rospy.is_shutdown():
            try:
                return self.tf_buffer.lookup_transform(
                    parent,
                    child,
                    self.rospy.Time(0),
                    self.rospy.Duration(0.2),
                )
            except Exception as exc:
                last_error = exc
                # Readiness must remain wall-clock bounded even if /clock stalls.
                sleep(0.05)
        raise RuntimeError(
            f"required transform is unavailable: {parent} -> {child}; "
            f"last_error={last_error}"
        )


class RosEvidenceRecorder:
    def __init__(self, rospy: Any) -> None:
        from actionlib_msgs.msg import GoalStatusArray
        from geometry_msgs.msg import Twist
        from move_base_msgs.msg import (
            MoveBaseActionFeedback,
            MoveBaseActionGoal,
        )

        self._lock = Lock()
        self._records: list[dict[str, Any]] = []
        self._subscribers = [
            rospy.Subscriber(
                "/move_base/goal",
                MoveBaseActionGoal,
                self._on_goal,
                queue_size=20,
            ),
            rospy.Subscriber(
                "/move_base/feedback",
                MoveBaseActionFeedback,
                self._on_feedback,
                queue_size=200,
            ),
            rospy.Subscriber(
                "/move_base/status",
                GoalStatusArray,
                self._on_status,
                queue_size=50,
            ),
            rospy.Subscriber(
                "/cmd_vel",
                Twist,
                self._on_cmd_vel,
                queue_size=200,
            ),
        ]

    def close(self) -> None:
        for subscriber in self._subscribers:
            try:
                subscriber.unregister()
            except Exception:
                pass

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._records]

    def goals(self) -> list[dict[str, Any]]:
        return [item for item in self.records() if item.get("kind") == "goal"]

    def feedback(self) -> list[dict[str, Any]]:
        return [
            item for item in self.records() if item.get("kind") == "feedback"
        ]

    def statuses(self) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for record in self.records():
            if record.get("kind") != "status":
                continue
            for status in record.get("statuses", []):
                flattened.append(
                    {
                        "captured_at": record.get("captured_at"),
                        **dict(status),
                    }
                )
        return flattened

    def wait_for_goal_status(
        self,
        goal_id: str,
        statuses: tuple[int, ...],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = monotonic() + timeout_seconds
        last: dict[str, Any] | None = None
        while monotonic() < deadline:
            matches = [
                item
                for item in self.statuses()
                if str(item.get("goal_id")) == goal_id
            ]
            if matches:
                last = matches[-1]
            for item in reversed(matches):
                if item.get("status") in statuses:
                    return item
            sleep(0.05)
        raise AssertionError(
            "move_base did not publish an expected terminal status for "
            f"goal {goal_id}; expected={statuses}, last={last}"
        )

    def _append(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._records.append({"captured_at": _utc_now(), **value})

    def _on_goal(self, message: Any) -> None:
        pose = message.goal.target_pose.pose
        self._append(
            {
                "kind": "goal",
                "goal_id": str(message.goal_id.id),
                "frame_id": str(message.goal.target_pose.header.frame_id),
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "yaw": _yaw_from_quaternion(pose.orientation),
            }
        )

    def _on_feedback(self, message: Any) -> None:
        pose = message.feedback.base_position.pose
        self._append(
            {
                "kind": "feedback",
                "goal_id": str(message.status.goal_id.id),
                "status": int(message.status.status),
                "frame_id": str(
                    message.feedback.base_position.header.frame_id
                ),
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "yaw": _yaw_from_quaternion(pose.orientation),
            }
        )

    def _on_status(self, message: Any) -> None:
        statuses = [
            {
                "goal_id": str(item.goal_id.id),
                "status": int(item.status),
                "text": str(item.text),
            }
            for item in message.status_list
        ]
        self._append({"kind": "status", "statuses": statuses})

    def _on_cmd_vel(self, message: Any) -> None:
        self._append(
            {
                "kind": "cmd_vel",
                "linear_x_mps": float(message.linear.x),
                "angular_z_rps": float(message.angular.z),
            }
        )


def _yaw_from_quaternion(rotation: Any) -> float:
    siny_cosp = 2.0 * (
        float(rotation.w) * float(rotation.z)
        + float(rotation.x) * float(rotation.y)
    )
    cosy_cosp = 1.0 - 2.0 * (
        float(rotation.y) ** 2 + float(rotation.z) ** 2
    )
    return atan2(siny_cosp, cosy_cosp)


def _pose_dict(pose: Pose2D) -> dict[str, Any]:
    return {
        "frame_id": pose.frame_id,
        "x": pose.x,
        "y": pose.y,
        "yaw": pose.yaw,
    }


def _message_summary(topic: str, message: Any) -> dict[str, Any]:
    summary = {
        "type": f"{type(message).__module__}.{type(message).__name__}",
        "received_at": _utc_now(),
    }
    if topic == "/clock":
        summary["clock_seconds"] = float(message.clock.to_sec())
    elif topic == "/scan":
        summary.update(
            {
                "frame_id": str(message.header.frame_id),
                "range_count": len(message.ranges),
            }
        )
    elif topic == "/odom":
        summary.update(
            {
                "frame_id": str(message.header.frame_id),
                "child_frame_id": str(message.child_frame_id),
            }
        )
    return summary


def _transform_summary(transform: Any) -> dict[str, Any]:
    return {
        "parent": str(transform.header.frame_id),
        "child": str(transform.child_frame_id),
        "stamp": float(transform.header.stamp.to_sec()),
        "translation": {
            "x": float(transform.transform.translation.x),
            "y": float(transform.transform.translation.y),
            "z": float(transform.transform.translation.z),
        },
    }


__all__ = ["RosEvidenceRecorder", "RosHarness"]
