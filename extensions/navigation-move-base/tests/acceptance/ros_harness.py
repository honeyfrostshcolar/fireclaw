"""Read-only ROS probes and evidence capture for Gazebo acceptance."""

from __future__ import annotations

from datetime import datetime, timezone
from math import atan2, cos, hypot, sin
import os
from pathlib import Path
import subprocess
from threading import Lock
from time import monotonic, sleep
from typing import Any, Mapping

from .artifacts import sha256_file
from .scenario import (
    AcceptanceScenario,
    CollisionCalibrationExpectation,
    CollisionCalibrationScenario,
    Pose2D,
)


COLLISION_EVIDENCE_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-evidence/v1"
)
COLLISION_CONTACT_STREAM_ARTIFACT = "collision-contact-stream.jsonl"
COLLISION_CALIBRATION_INJECTION_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-injection-evidence/v1"
)
_COLLISION_STREAM_MAX_RECORDS = 100_000
_COLLISION_EPISODE_GAP_SECONDS = 0.25
_SUPPORT_LINKS = frozenset({
    "wheel_left_link",
    "wheel_right_link",
    "caster_back_link",
})
_ROBOT_COLLISION_LINKS = (
    "base_link",
    "wheel_left_link",
    "wheel_right_link",
    "caster_back_link",
    "base_scan",
)
_CONTACT_TOPIC = "/fireclaw/acceptance/contacts"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RosHarness:
    def __init__(
        self,
        scenario: AcceptanceScenario | CollisionCalibrationScenario,
    ) -> None:
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

    def spawn_collision_calibration_probe(
        self,
        calibration: CollisionCalibrationExpectation,
        sdf_path: Path,
    ) -> dict[str, Any]:
        """Spawn the fixed positive-control body through Gazebo's real API."""

        from gazebo_msgs.srv import GetModelState, SpawnModel
        from geometry_msgs.msg import Pose

        if sdf_path.is_symlink() or not sdf_path.is_file():
            raise ValueError(
                "collision calibration SDF must be a regular non-symlink file"
            )
        model_xml = sdf_path.read_text(encoding="utf-8")
        timeout = min(
            self.scenario.ros_readiness_seconds,
            calibration.detection_timeout_seconds,
        )
        spawn_service = "/gazebo/spawn_sdf_model"
        state_service = "/gazebo/get_model_state"
        self._wait_for_service(spawn_service, timeout)
        self._wait_for_service(state_service, timeout)
        get_state = self.rospy.ServiceProxy(state_service, GetModelState)
        before = get_state(calibration.model_name, "world")
        if bool(before.success):
            raise AssertionError(
                "collision calibration model already exists before injection"
            )

        pose = Pose()
        pose.position.x = calibration.world_pose.x
        pose.position.y = calibration.world_pose.y
        pose.position.z = calibration.world_pose.z
        pose.orientation.z = sin(calibration.world_pose.yaw / 2.0)
        pose.orientation.w = cos(calibration.world_pose.yaw / 2.0)
        requested_at = _utc_now()
        response = self.rospy.ServiceProxy(
            spawn_service,
            SpawnModel,
        )(
            calibration.model_name,
            model_xml,
            "",
            pose,
            "world",
        )
        completed_at = _utc_now()
        if not bool(response.success):
            raise AssertionError(
                "Gazebo rejected collision calibration model: "
                f"{response.status_message}"
            )
        state = get_state(calibration.model_name, "world")
        if not bool(state.success):
            raise AssertionError(
                "spawn service succeeded but calibration model state is absent"
            )
        return {
            "schema_version": (
                COLLISION_CALIBRATION_INJECTION_SCHEMA_VERSION
            ),
            "status": "spawned",
            "method": "gazebo_spawn_sdf_model_static_overlap",
            "simulation_only": True,
            "model_name": calibration.model_name,
            "reference_frame": "world",
            "spawn_service": spawn_service,
            "state_service": state_service,
            "requested_at": requested_at,
            "completed_at": completed_at,
            "requested_pose": {
                "x": calibration.world_pose.x,
                "y": calibration.world_pose.y,
                "z": calibration.world_pose.z,
                "yaw": calibration.world_pose.yaw,
            },
            "sdf_asset": {
                "path": str(sdf_path),
                "sha256": sha256_file(sdf_path),
            },
            "response": {
                "success": bool(response.success),
                "status_message": str(response.status_message),
            },
            "model_state": _gazebo_model_state_dict(state),
        }

    def delete_collision_calibration_probe(
        self,
        model_name: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Remove the positive-control body and prove it is absent."""

        from gazebo_msgs.srv import DeleteModel, GetModelState

        delete_service = "/gazebo/delete_model"
        state_service = "/gazebo/get_model_state"
        self._wait_for_service(delete_service, timeout_seconds)
        self._wait_for_service(state_service, timeout_seconds)
        requested_at = _utc_now()
        response = self.rospy.ServiceProxy(
            delete_service,
            DeleteModel,
        )(model_name)
        if not bool(response.success):
            raise AssertionError(
                "Gazebo rejected collision calibration cleanup: "
                f"{response.status_message}"
            )
        get_state = self.rospy.ServiceProxy(state_service, GetModelState)
        deadline = monotonic() + timeout_seconds
        last_state = None
        while monotonic() < deadline:
            last_state = get_state(model_name, "world")
            if not bool(last_state.success):
                break
            sleep(0.05)
        if last_state is None or bool(last_state.success):
            raise AssertionError(
                "collision calibration model remained after delete_model"
            )
        return {
            "status": "deleted",
            "simulation_only": True,
            "model_name": model_name,
            "reference_frame": "world",
            "delete_service": delete_service,
            "state_service": state_service,
            "requested_at": requested_at,
            "completed_at": _utc_now(),
            "response": {
                "success": bool(response.success),
                "status_message": str(response.status_message),
            },
            "post_delete_model_present": bool(last_state.success),
            "post_delete_status_message": str(last_state.status_message),
        }

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
        return RosEvidenceRecorder(self.rospy, self.scenario)

    def _wait_for_service(self, service: str, timeout_seconds: float) -> None:
        try:
            self.rospy.wait_for_service(service, timeout=timeout_seconds)
        except Exception as exc:
            raise RuntimeError(
                f"required Gazebo service is unavailable: {service}"
            ) from exc

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
    def __init__(
        self,
        rospy: Any,
        scenario: AcceptanceScenario | CollisionCalibrationScenario,
    ) -> None:
        from actionlib_msgs.msg import GoalStatusArray
        from gazebo_msgs.msg import ContactsState
        from geometry_msgs.msg import Twist
        from move_base_msgs.msg import (
            MoveBaseActionFeedback,
            MoveBaseActionGoal,
        )

        self._rospy = rospy
        self._scenario = scenario
        library_value = os.getenv(
            "FIRECLAW_GAZEBO_CONTACT_MONITOR_LIBRARY",
            "",
        )
        self._collision_library_path = Path(library_value).resolve(
            strict=False
        )
        if (
            not library_value
            or self._collision_library_path.is_symlink()
            or not self._collision_library_path.is_file()
        ):
            raise RuntimeError(
                "FIRECLAW_GAZEBO_CONTACT_MONITOR_LIBRARY must name the "
                "built, regular acceptance WorldPlugin library"
            )
        self._collision_library_identity = {
            "source_path": str(self._collision_library_path),
            "sha256": sha256_file(self._collision_library_path),
            "embedded_artifact": "collision-monitor-plugin.so",
        }
        self._lock = Lock()
        self._records: list[dict[str, Any]] = []
        self._collision_started_at = _utc_now()
        self._collision_started_monotonic = monotonic()
        self._collision_ready_at: str | None = None
        self._collision_ready_before_first_goal = False
        self._collision_finalized = False
        self._collision_evidence: dict[str, Any] | None = None
        self._collision_stream: list[dict[str, Any]] = []
        self._collision_stream_truncated = False
        self._collision_state_count = 0
        self._collision_episodes: list[dict[str, Any]] = []
        self._active_collision_episodes: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._contact_pair_aggregates: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        self._gazebo_model_name = f"turtlebot3_{scenario.robot_model}"
        self._contact_topic_stats = {
            _CONTACT_TOPIC: {
                "topic": _CONTACT_TOPIC,
                "monitored_scope": (
                    f"all collisions prefixed by {self._gazebo_model_name}::"
                ),
                "message_count": 0,
                "contact_state_count": 0,
                "first_received_at": None,
                "last_received_at": None,
            }
        }
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
        self._collision_subscribers = {
            topic: rospy.Subscriber(
                topic,
                ContactsState,
                self._contact_callback(topic),
                queue_size=500,
            )
            for topic in self._contact_topic_stats
        }
        self._subscribers.extend(self._collision_subscribers.values())

    def wait_for_collision_instrumentation(
        self,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = monotonic() + timeout_seconds
        last_connections: dict[str, int] = {}
        while monotonic() < deadline:
            last_connections = {
                topic: int(subscriber.get_num_connections())
                for topic, subscriber in self._collision_subscribers.items()
            }
            if all(count > 0 for count in last_connections.values()):
                ready_at = _utc_now()
                with self._lock:
                    ready_before_first_goal = not any(
                        item.get("kind") == "goal"
                        for item in self._records
                    )
                    self._collision_ready_at = ready_at
                    self._collision_ready_before_first_goal = (
                        ready_before_first_goal
                    )
                if not ready_before_first_goal:
                    raise AssertionError(
                        "collision instrumentation became ready after the "
                        "first /move_base goal"
                    )
                return {
                    "status": "ready",
                    "checked_at": ready_at,
                    "ready_before_first_goal": True,
                    "source": "gazebo.physics.ContactManager",
                    "sensor_plugin": (
                        "libfireclaw_gazebo_contact_monitor.so"
                    ),
                    "message_type": "gazebo_msgs/ContactsState",
                    "library": dict(self._collision_library_identity),
                    "topics": [
                        {
                            **dict(self._contact_topic_stats[topic]),
                            "publisher_connections": count,
                        }
                        for topic, count in sorted(
                            last_connections.items()
                        )
                    ],
                }
            sleep(0.05)
        raise RuntimeError(
            "Gazebo collision instrumentation publishers are not ready; "
            f"connections={last_connections}"
        )

    def collision_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._collision_stream]

    def collision_monitor_library_path(self) -> Path:
        return self._collision_library_path

    def wait_for_prohibited_collision(
        self,
        *,
        other_model_name: str,
        minimum_state_count: int,
        minimum_episode_count: int,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Wait for real prohibited contacts involving the injected model."""

        other_prefix = f"{other_model_name}::"
        deadline = monotonic() + timeout_seconds
        last_state_count = 0
        last_episode_ids: list[str] = []
        while monotonic() < deadline:
            with self._lock:
                matches = [
                    (index, dict(item))
                    for index, item in enumerate(self._collision_stream)
                    if item.get("classification") == "prohibited_collision"
                    and (
                        str(item.get("collision1_name") or "").startswith(
                            other_prefix
                        )
                        or str(
                            item.get("collision2_name") or ""
                        ).startswith(other_prefix)
                    )
                ]
            episode_ids = sorted({
                str(item.get("episode_id"))
                for _index, item in matches
                if item.get("episode_id")
            })
            last_state_count = len(matches)
            last_episode_ids = episode_ids
            if (
                last_state_count >= minimum_state_count
                and len(episode_ids) >= minimum_episode_count
            ):
                records = [item for _index, item in matches]
                return {
                    "status": "detected",
                    "detected_at": _utc_now(),
                    "expected_other_model": other_model_name,
                    "prohibited_contact_state_count": len(records),
                    "collision_episode_count": len(episode_ids),
                    "collision_episode_ids": episode_ids,
                    "matching_stream_record_indices": [
                        index for index, _item in matches
                    ],
                    "collision_pairs": sorted({
                        tuple(sorted((
                            str(item["collision1_name"]),
                            str(item["collision2_name"]),
                        )))
                        for item in records
                    }),
                    "first_observed_at": records[0].get("captured_at"),
                    "last_observed_at": records[-1].get("captured_at"),
                }
            sleep(0.02)
        raise TimeoutError(
            "Gazebo ContactManager did not detect the positive-control "
            f"collision; states={last_state_count}, "
            f"episodes={last_episode_ids}"
        )

    def finalize_collision_evidence(
        self,
        *,
        terminal_stop: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._collision_evidence is not None:
                return dict(self._collision_evidence)
            self._collision_finalized = True
            ended_at = _utc_now()
            ended_monotonic = monotonic()
            topic_entries = []
            for topic, stats in sorted(self._contact_topic_stats.items()):
                topic_entries.append({
                    **dict(stats),
                    "publisher_connections_at_finalize": int(
                        self._collision_subscribers[
                            topic
                        ].get_num_connections()
                    ),
                    "produced_messages": stats["message_count"] > 0,
                })
            stream = [dict(item) for item in self._collision_stream]
            aggregates = [
                {
                    **{
                        key: value
                        for key, value in aggregate.items()
                        if key != "sensor_topics"
                    },
                    "sensor_topics": sorted(aggregate["sensor_topics"]),
                }
                for aggregate in self._contact_pair_aggregates.values()
            ]
            aggregates.sort(
                key=lambda item: (
                    str(item["classification"]),
                    tuple(item["collision_pair"]),
                )
            )
            topics_complete = all(
                item["message_count"] > 0
                and item["publisher_connections_at_finalize"] > 0
                for item in topic_entries
            )
            ended_after_terminal_stop = (
                isinstance(terminal_stop, Mapping)
                and terminal_stop.get("status") == "stopped"
            )
            instrumentation_complete = (
                self._collision_ready_at is not None
                and self._collision_ready_before_first_goal
                and topics_complete
                and not self._collision_stream_truncated
                and ended_after_terminal_stop
            )
            prohibited_states = sum(
                item.get("classification") == "prohibited_collision"
                for item in stream
            )
            allowed_states = sum(
                item.get("classification") == "allowed_support_contact"
                for item in stream
            )
            evidence = {
                "schema_version": COLLISION_EVIDENCE_SCHEMA_VERSION,
                "status": (
                    "captured" if instrumentation_complete else "incomplete"
                ),
                "robot": {
                    "id": self._scenario.robot_id,
                    "model": self._scenario.robot_model,
                    "gazebo_model_name": (
                        self._gazebo_model_name
                    ),
                },
                "instrumentation": {
                    "status": (
                        "complete"
                        if instrumentation_complete
                        else "incomplete"
                    ),
                    "source": "gazebo.physics.ContactManager",
                    "sensor_plugin": (
                        "libfireclaw_gazebo_contact_monitor.so"
                    ),
                    "message_type": "gazebo_msgs/ContactsState",
                    "library": dict(self._collision_library_identity),
                    "ready_at": self._collision_ready_at,
                    "topics": topic_entries,
                },
                "observation_window": {
                    "started_at": self._collision_started_at,
                    "ended_at": ended_at,
                    "wall_duration_seconds": (
                        ended_monotonic - self._collision_started_monotonic
                    ),
                    "started_before_first_goal": (
                        self._collision_ready_before_first_goal
                    ),
                    "ended_after_terminal_stop": ended_after_terminal_stop,
                },
                "filter_policy": {
                    "policy_id": (
                        "fireclaw.acceptance.prohibited-contact/v1"
                    ),
                    "allowed_support_links": sorted(_SUPPORT_LINKS),
                    "allowed_other_model": "ground_plane",
                    "rule": (
                        "Only wheel/caster contact with ground_plane is "
                        "allowed; every other observed robot contact is a "
                        "prohibited collision."
                    ),
                    "episode_gap_seconds": (
                        _COLLISION_EPISODE_GAP_SECONDS
                    ),
                },
                "raw_stream": {
                    "artifact": COLLISION_CONTACT_STREAM_ARTIFACT,
                    "record_count": len(stream),
                    "max_records": _COLLISION_STREAM_MAX_RECORDS,
                    "truncated": self._collision_stream_truncated,
                },
                "contact_message_count": sum(
                    int(item["message_count"]) for item in topic_entries
                ),
                "contact_state_count": self._collision_state_count,
                "allowed_support_contact_state_count": allowed_states,
                "prohibited_contact_state_count": prohibited_states,
                "contact_pair_aggregates": aggregates,
                "collision_episodes": [
                    dict(item) for item in self._collision_episodes
                ],
                "collision_count": len(self._collision_episodes),
                "collision_free": (
                    len(self._collision_episodes) == 0
                    if instrumentation_complete
                    else None
                ),
            }
            self._collision_evidence = evidence
            return dict(evidence)

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

    def _contact_callback(self, topic: str):
        def callback(message: Any) -> None:
            self._on_contact(topic, message)

        return callback

    def _on_contact(self, topic: str, message: Any) -> None:
        captured_at = _utc_now()
        captured_monotonic = monotonic()
        header = getattr(message, "header", None)
        stamp = getattr(header, "stamp", None)
        sim_time_seconds = (
            float(stamp.to_sec())
            if stamp is not None and hasattr(stamp, "to_sec")
            else None
        )
        states = list(getattr(message, "states", ()))
        with self._lock:
            if self._collision_finalized:
                return
            stats = self._contact_topic_stats[topic]
            stats["message_count"] += 1
            stats["contact_state_count"] += len(states)
            stats["first_received_at"] = (
                stats["first_received_at"] or captured_at
            )
            stats["last_received_at"] = captured_at
            for state in states:
                self._collision_state_count += 1
                collision1 = str(
                    getattr(state, "collision1_name", "")
                )
                collision2 = str(
                    getattr(state, "collision2_name", "")
                )
                classification, reason = _classify_contact(
                    self._gazebo_model_name,
                    collision1,
                    collision2,
                )
                link_name = _robot_contact_link(
                    self._gazebo_model_name,
                    collision1,
                    collision2,
                )
                pair = tuple(sorted((collision1, collision2)))
                episode_id = None
                if classification == "prohibited_collision":
                    episode = self._active_collision_episodes.get(pair)
                    if (
                        episode is None
                        or captured_monotonic
                        - float(episode["last_monotonic"])
                        > _COLLISION_EPISODE_GAP_SECONDS
                    ):
                        episode_id = (
                            f"collision-{len(self._collision_episodes) + 1:04d}"
                        )
                        public_episode = {
                            "episode_id": episode_id,
                            "collision_pair": list(pair),
                            "first_observed_at": captured_at,
                            "last_observed_at": captured_at,
                            "sample_count": 1,
                            "sensor_topics": [topic],
                        }
                        self._collision_episodes.append(public_episode)
                        episode = {
                            "episode_id": episode_id,
                            "last_monotonic": captured_monotonic,
                            "public": public_episode,
                        }
                        self._active_collision_episodes[pair] = episode
                    else:
                        episode_id = str(episode["episode_id"])
                        episode["last_monotonic"] = captured_monotonic
                        public_episode = episode["public"]
                        public_episode["last_observed_at"] = captured_at
                        public_episode["sample_count"] += 1
                        public_episode["sensor_topics"] = sorted(set([
                            *public_episode["sensor_topics"],
                            topic,
                        ]))
                record = _contact_record(
                    topic=topic,
                    link_name=link_name,
                    captured_at=captured_at,
                    sim_time_seconds=sim_time_seconds,
                    state=state,
                    collision1=collision1,
                    collision2=collision2,
                    classification=classification,
                    reason=reason,
                    episode_id=episode_id,
                )
                if len(self._collision_stream) < _COLLISION_STREAM_MAX_RECORDS:
                    self._collision_stream.append(record)
                else:
                    self._collision_stream_truncated = True
                aggregate_key = (classification, *pair)
                aggregate = self._contact_pair_aggregates.get(aggregate_key)
                if aggregate is None:
                    aggregate = {
                        "classification": classification,
                        "reason": reason,
                        "collision_pair": list(pair),
                        "sample_count": 0,
                        "first_observed_at": captured_at,
                        "last_observed_at": captured_at,
                        "sensor_topics": set(),
                    }
                    self._contact_pair_aggregates[aggregate_key] = aggregate
                aggregate["sample_count"] += 1
                aggregate["last_observed_at"] = captured_at
                aggregate["sensor_topics"].add(topic)


def assert_collision_free(evidence: Mapping[str, Any]) -> None:
    if evidence.get("status") != "captured":
        raise AssertionError(
            "collision evidence does not cover the complete execution window: "
            f"{evidence.get('instrumentation')}"
        )
    if evidence.get("collision_free") is not True:
        raise AssertionError(
            "Gazebo acceptance observed prohibited collision episodes: "
            f"{evidence.get('collision_episodes')}"
        )


def assert_collision_detected(
    evidence: Mapping[str, Any],
    *,
    expected_other_model: str,
    minimum_state_count: int,
    minimum_episode_count: int,
) -> None:
    if evidence.get("status") != "captured":
        raise AssertionError(
            "collision evidence does not cover the complete calibration window"
        )
    if evidence.get("collision_free") is not False:
        raise AssertionError("positive control did not produce a collision")
    if int(evidence.get("prohibited_contact_state_count") or 0) < (
        minimum_state_count
    ):
        raise AssertionError("positive control produced too few contact states")
    episodes = evidence.get("collision_episodes")
    if not isinstance(episodes, list) or len(episodes) < minimum_episode_count:
        raise AssertionError("positive control produced too few episodes")
    other_prefix = f"{expected_other_model}::"
    matching = [
        episode
        for episode in episodes
        if isinstance(episode, Mapping)
        and isinstance(episode.get("collision_pair"), list)
        and any(
            isinstance(name, str) and name.startswith(other_prefix)
            for name in episode["collision_pair"]
        )
    ]
    if len(matching) < minimum_episode_count:
        raise AssertionError(
            "collision episodes do not involve the calibration model"
        )


def _classify_contact(
    robot_model_name: str,
    collision1: str,
    collision2: str,
) -> tuple[str, str]:
    model_prefix = f"{robot_model_name}::"
    first_matches = collision1.startswith(model_prefix)
    second_matches = collision2.startswith(model_prefix)
    if first_matches and not second_matches:
        robot_collision = collision1
        other = collision2
    elif second_matches and not first_matches:
        robot_collision = collision2
        other = collision1
    elif first_matches and second_matches:
        return "prohibited_collision", "robot_self_contact"
    else:
        return "prohibited_collision", "unexpected_sensor_contact_pair"
    link_name = _collision_link_name(robot_collision)
    other_model = other.split("::", 1)[0]
    if link_name in _SUPPORT_LINKS and other_model == "ground_plane":
        return "allowed_support_contact", "support_link_on_ground_plane"
    return "prohibited_collision", "robot_contact_with_non_support_surface"


def _robot_contact_link(
    robot_model_name: str,
    collision1: str,
    collision2: str,
) -> str:
    prefix = f"{robot_model_name}::"
    if collision1.startswith(prefix):
        return _collision_link_name(collision1)
    if collision2.startswith(prefix):
        return _collision_link_name(collision2)
    return "unknown"


def _collision_link_name(collision_name: str) -> str:
    for link_name in _ROBOT_COLLISION_LINKS:
        if f"{link_name}_collision" in collision_name:
            return link_name
    return "unknown"


def _contact_record(
    *,
    topic: str,
    link_name: str,
    captured_at: str,
    sim_time_seconds: float | None,
    state: Any,
    collision1: str,
    collision2: str,
    classification: str,
    reason: str,
    episode_id: str | None,
) -> dict[str, Any]:
    positions = list(getattr(state, "contact_positions", ()))
    normals = list(getattr(state, "contact_normals", ()))
    depths = [float(item) for item in getattr(state, "depths", ())]
    result = {
        "captured_at": captured_at,
        "sim_time_seconds": sim_time_seconds,
        "sensor_topic": topic,
        "sensor_link": link_name,
        "collision1_name": collision1,
        "collision2_name": collision2,
        "classification": classification,
        "classification_reason": reason,
        "episode_id": episode_id,
        "contact_point_count": len(positions),
        "max_depth_m": max(depths, default=None),
        "contact_positions": [_vector_dict(item) for item in positions],
        "contact_normals": [_vector_dict(item) for item in normals],
        "depths_m": depths,
    }
    total_wrench = getattr(state, "total_wrench", None)
    if total_wrench is not None:
        result["total_wrench"] = _wrench_dict(total_wrench)
    return result


def _vector_dict(value: Any) -> dict[str, float]:
    return {
        "x": float(getattr(value, "x", 0.0)),
        "y": float(getattr(value, "y", 0.0)),
        "z": float(getattr(value, "z", 0.0)),
    }


def _wrench_dict(value: Any) -> dict[str, dict[str, float]]:
    return {
        "force": _vector_dict(getattr(value, "force", None)),
        "torque": _vector_dict(getattr(value, "torque", None)),
    }


def _yaw_from_quaternion(rotation: Any) -> float:
    siny_cosp = 2.0 * (
        float(rotation.w) * float(rotation.z)
        + float(rotation.x) * float(rotation.y)
    )
    cosy_cosp = 1.0 - 2.0 * (
        float(rotation.y) ** 2 + float(rotation.z) ** 2
    )
    return atan2(siny_cosp, cosy_cosp)


def _gazebo_model_state_dict(response: Any) -> dict[str, Any]:
    pose = response.pose
    twist = response.twist
    return {
        "success": bool(response.success),
        "status_message": str(response.status_message),
        "pose": {
            "x": float(pose.position.x),
            "y": float(pose.position.y),
            "z": float(pose.position.z),
            "yaw": _yaw_from_quaternion(pose.orientation),
        },
        "twist": {
            "linear_x_mps": float(twist.linear.x),
            "linear_y_mps": float(twist.linear.y),
            "linear_z_mps": float(twist.linear.z),
            "angular_x_rps": float(twist.angular.x),
            "angular_y_rps": float(twist.angular.y),
            "angular_z_rps": float(twist.angular.z),
        },
    }


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


__all__ = [
    "COLLISION_CALIBRATION_INJECTION_SCHEMA_VERSION",
    "RosEvidenceRecorder",
    "RosHarness",
    "assert_collision_detected",
    "assert_collision_free",
]
