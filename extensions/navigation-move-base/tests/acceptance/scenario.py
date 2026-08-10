"""Validated, data-only scenario contract for Gazebo acceptance tests."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, isfinite, pi, sin
from pathlib import Path
import re
from typing import Any, Mapping

import yaml


SCHEMA_VERSION = "fireclaw.gazebo-acceptance/v1"
COLLISION_CALIBRATION_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-calibration/v1"
)
COLLISION_CALIBRATION_SCENARIO_TYPE = "collision_calibration"
_COLLISION_CALIBRATION_MODEL_NAME = (
    "fireclaw_collision_calibration_probe"
)
_GAZEBO_MODEL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
SCENARIO_TYPES = frozenset(
    {
        "success",
        "cancel",
        "timeout",
        "abort",
        "stall_recover",
        "stall_escalate",
    }
)


@dataclass(frozen=True)
class Pose2D:
    frame_id: str
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class AcceptanceAssets:
    launch: Path
    robot_description: Path
    collision_monitor: Path
    world: Path
    map: Path
    map_image: Path
    ros1_config: Path
    collision_probe: Path | None = None

    def to_dict(self) -> dict[str, str]:
        result = {
            "launch": str(self.launch),
            "robot_description": str(self.robot_description),
            "collision_monitor": str(self.collision_monitor),
            "world": str(self.world),
            "map": str(self.map),
            "map_image": str(self.map_image),
            "ros1_config": str(self.ros1_config),
        }
        if self.collision_probe is not None:
            result["collision_probe"] = str(self.collision_probe)
        return result


@dataclass(frozen=True)
class Pose3D:
    x: float
    y: float
    z: float
    yaw: float


@dataclass(frozen=True)
class CollisionCalibrationExpectation:
    model_name: str
    world_pose: Pose3D
    detection_timeout_seconds: float
    minimum_prohibited_contact_states: int
    minimum_collision_episodes: int
    maximum_robot_displacement_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_only": True,
            "excluded_from_task_metrics": True,
            "injection": {
                "method": "gazebo_spawn_sdf_model_static_overlap",
                "model_name": self.model_name,
                "reference_frame": "world",
                "world_pose": {
                    "x": self.world_pose.x,
                    "y": self.world_pose.y,
                    "z": self.world_pose.z,
                    "yaw": self.world_pose.yaw,
                },
                "asset_label": "collision_probe",
            },
            "detection_timeout_seconds": self.detection_timeout_seconds,
            "minimum_prohibited_contact_states": (
                self.minimum_prohibited_contact_states
            ),
            "minimum_collision_episodes": self.minimum_collision_episodes,
            "maximum_robot_displacement_m": (
                self.maximum_robot_displacement_m
            ),
        }


@dataclass(frozen=True)
class CollisionCalibrationScenario:
    schema_version: str
    scenario_version: str
    scenario_id: str
    scenario_type: str
    description: str
    seed: int
    robot_id: str
    robot_model: str
    initial_pose: Pose2D
    action_name: str
    initial_position_tolerance_m: float
    initial_yaw_tolerance_rad: float
    stopped_linear_velocity_mps: float
    stopped_angular_velocity_rps: float
    ros_readiness_seconds: float
    stopped_seconds: float
    readiness_topics: tuple[str, ...]
    readiness_transforms: tuple[tuple[str, str], ...]
    calibration: CollisionCalibrationExpectation
    assets: AcceptanceAssets

    def initial_pose_error(self, pose: Pose2D) -> tuple[float, float]:
        position_error = hypot(
            pose.x - self.initial_pose.x,
            pose.y - self.initial_pose.y,
        )
        yaw_error = abs(
            atan2(
                sin(pose.yaw - self.initial_pose.yaw),
                cos(pose.yaw - self.initial_pose.yaw),
            )
        )
        return position_error, min(yaw_error, pi)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_version": self.scenario_version,
            "scenario_id": self.scenario_id,
            "scenario_type": self.scenario_type,
            "simulation_only": True,
            "excluded_from_task_metrics": True,
            "description": self.description,
            "seed": self.seed,
            "robot": {
                "id": self.robot_id,
                "model": self.robot_model,
                "initial_pose": _pose_dict(self.initial_pose),
            },
            "readiness": {
                "action_server": self.action_name,
                "topics": list(self.readiness_topics),
                "transforms": [
                    list(transform) for transform in self.readiness_transforms
                ],
            },
            "assertions": {
                "initial_position_tolerance_m": (
                    self.initial_position_tolerance_m
                ),
                "initial_yaw_tolerance_rad": (
                    self.initial_yaw_tolerance_rad
                ),
                "stopped_linear_velocity_mps": (
                    self.stopped_linear_velocity_mps
                ),
                "stopped_angular_velocity_rps": (
                    self.stopped_angular_velocity_rps
                ),
            },
            "timeouts": {
                "ros_readiness_seconds": self.ros_readiness_seconds,
                "stopped_seconds": self.stopped_seconds,
            },
            "calibration": self.calibration.to_dict(),
            "assets": self.assets.to_dict(),
        }


@dataclass(frozen=True)
class CancellationExpectation:
    minimum_feedback_count: int
    minimum_displacement_m: float
    observation_timeout_seconds: float
    acknowledgement_timeout_seconds: float
    maximum_post_cancel_displacement_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_feedback_count": self.minimum_feedback_count,
            "minimum_displacement_m": self.minimum_displacement_m,
            "observation_timeout_seconds": self.observation_timeout_seconds,
            "acknowledgement_timeout_seconds": (
                self.acknowledgement_timeout_seconds
            ),
            "maximum_post_cancel_displacement_m": (
                self.maximum_post_cancel_displacement_m
            ),
        }


@dataclass(frozen=True)
class TimeoutExpectation:
    execution_timeout_seconds: float
    minimum_feedback_count: int
    minimum_displacement_m: float
    observation_timeout_seconds: float
    acknowledgement_timeout_seconds: float
    maximum_post_timeout_displacement_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "minimum_feedback_count": self.minimum_feedback_count,
            "minimum_displacement_m": self.minimum_displacement_m,
            "observation_timeout_seconds": self.observation_timeout_seconds,
            "acknowledgement_timeout_seconds": (
                self.acknowledgement_timeout_seconds
            ),
            "maximum_post_timeout_displacement_m": (
                self.maximum_post_timeout_displacement_m
            ),
        }


@dataclass(frozen=True)
class AbortExpectation:
    planner_patience_seconds: float
    recovery_behavior_enabled: bool
    minimum_feedback_count: int
    terminal_timeout_seconds: float
    maximum_displacement_m: float
    required_status_text_substring: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_patience_seconds": self.planner_patience_seconds,
            "recovery_behavior_enabled": self.recovery_behavior_enabled,
            "minimum_feedback_count": self.minimum_feedback_count,
            "terminal_timeout_seconds": self.terminal_timeout_seconds,
            "maximum_displacement_m": self.maximum_displacement_m,
            "required_status_text_substring": (
                self.required_status_text_substring
            ),
        }


@dataclass(frozen=True)
class StallExpectation:
    execution_timeout_seconds: float
    diagnostics_timeout_seconds: float
    minimum_feedback_count: int
    maximum_stall_displacement_m: float
    terminal_timeout_seconds: float
    stalled_parameters: dict[str, float]
    recovery_parameters: dict[str, float] | None
    maximum_recovery_attempts: int
    first_actionlib_terminal_statuses: tuple[int, ...]
    final_actionlib_terminal_statuses: tuple[int, ...]
    required_diagnostic_finding_codes: tuple[str, ...]
    escalation_reason_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "diagnostics_timeout_seconds": self.diagnostics_timeout_seconds,
            "minimum_feedback_count": self.minimum_feedback_count,
            "maximum_stall_displacement_m": (
                self.maximum_stall_displacement_m
            ),
            "terminal_timeout_seconds": self.terminal_timeout_seconds,
            "stalled_parameters": dict(self.stalled_parameters),
            "recovery_parameters": (
                dict(self.recovery_parameters)
                if self.recovery_parameters is not None
                else None
            ),
            "maximum_recovery_attempts": self.maximum_recovery_attempts,
            "first_actionlib_terminal_statuses": list(
                self.first_actionlib_terminal_statuses
            ),
            "final_actionlib_terminal_statuses": list(
                self.final_actionlib_terminal_statuses
            ),
            "required_diagnostic_finding_codes": list(
                self.required_diagnostic_finding_codes
            ),
            "escalation_reason_code": self.escalation_reason_code,
        }


@dataclass(frozen=True)
class AcceptanceScenario:
    schema_version: str
    scenario_version: str
    scenario_id: str
    scenario_type: str
    description: str
    seed: int
    robot_id: str
    robot_model: str
    initial_pose: Pose2D
    command: str
    capability: str
    required_tool: str
    goal: Pose2D
    plugin_owner: str
    backend_class: str
    action_name: str
    mission_authorization_status: str
    mission_authorization_raw_status: str
    terminal_status: str
    actionlib_terminal_statuses: tuple[int, ...]
    minimum_displacement_m: float
    initial_position_tolerance_m: float
    initial_yaw_tolerance_rad: float
    position_tolerance_m: float
    yaw_tolerance_rad: float
    stopped_linear_velocity_mps: float
    stopped_angular_velocity_rps: float
    require_feedback: bool
    ros_readiness_seconds: float
    navigation_seconds: float
    mission_seconds: float
    stopped_seconds: float
    readiness_topics: tuple[str, ...]
    readiness_transforms: tuple[tuple[str, str], ...]
    cancellation: CancellationExpectation | None
    timeout: TimeoutExpectation | None
    abort: AbortExpectation | None
    stall: StallExpectation | None
    assets: AcceptanceAssets

    @property
    def planned_displacement_m(self) -> float:
        return hypot(
            self.goal.x - self.initial_pose.x,
            self.goal.y - self.initial_pose.y,
        )

    def goal_error(self, pose: Pose2D) -> tuple[float, float]:
        return self._pose_error(pose, self.goal)

    def initial_pose_error(self, pose: Pose2D) -> tuple[float, float]:
        return self._pose_error(pose, self.initial_pose)

    @staticmethod
    def _pose_error(
        actual: Pose2D,
        expected: Pose2D,
    ) -> tuple[float, float]:
        position_error = hypot(actual.x - expected.x, actual.y - expected.y)
        yaw_error = abs(
            atan2(
                sin(actual.yaw - expected.yaw),
                cos(actual.yaw - expected.yaw),
            )
        )
        return position_error, min(yaw_error, pi)

    def to_manifest(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "scenario_version": self.scenario_version,
            "scenario_id": self.scenario_id,
            "scenario_type": self.scenario_type,
            "description": self.description,
            "seed": self.seed,
            "robot": {
                "id": self.robot_id,
                "model": self.robot_model,
                "initial_pose": _pose_dict(self.initial_pose),
            },
            "mission": {
                "command": self.command,
                "capability": self.capability,
                "required_tool": self.required_tool,
            },
            "goal": _pose_dict(self.goal),
            "expected": {
                "plugin_owner": self.plugin_owner,
                "backend_class": self.backend_class,
                "action_name": self.action_name,
                "mission_authorization_status": (
                    self.mission_authorization_status
                ),
                "mission_authorization_raw_status": (
                    self.mission_authorization_raw_status
                ),
                "terminal_status": self.terminal_status,
                "actionlib_terminal_statuses": list(
                    self.actionlib_terminal_statuses
                ),
            },
            "assertions": {
                "minimum_displacement_m": self.minimum_displacement_m,
                "initial_position_tolerance_m": (
                    self.initial_position_tolerance_m
                ),
                "initial_yaw_tolerance_rad": self.initial_yaw_tolerance_rad,
                "position_tolerance_m": self.position_tolerance_m,
                "yaw_tolerance_rad": self.yaw_tolerance_rad,
                "stopped_linear_velocity_mps": (
                    self.stopped_linear_velocity_mps
                ),
                "stopped_angular_velocity_rps": (
                    self.stopped_angular_velocity_rps
                ),
                "require_feedback": self.require_feedback,
            },
            "timeouts": {
                "ros_readiness_seconds": self.ros_readiness_seconds,
                "navigation_seconds": self.navigation_seconds,
                "mission_seconds": self.mission_seconds,
                "stopped_seconds": self.stopped_seconds,
            },
            "readiness": {
                "action_server": self.action_name,
                "topics": list(self.readiness_topics),
                "transforms": [
                    list(transform) for transform in self.readiness_transforms
                ],
            },
            "planned_displacement_m": self.planned_displacement_m,
            "assets": self.assets.to_dict(),
        }
        if self.cancellation is not None:
            result["cancellation"] = self.cancellation.to_dict()
        if self.timeout is not None:
            result["timeout"] = self.timeout.to_dict()
        if self.abort is not None:
            result["abort"] = self.abort.to_dict()
        if self.stall is not None:
            result["stall"] = self.stall.to_dict()
        return result


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_scenario_path() -> Path:
    return (
        repository_root()
        / "extensions/navigation-move-base/config/acceptance/success.yaml"
    )


def load_acceptance_scenario(
    path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> AcceptanceScenario | CollisionCalibrationScenario:
    root = Path(repo_root or repository_root()).resolve(strict=True)
    target = Path(path or default_scenario_path()).resolve(strict=True)
    _require_within(target, root, "scenario")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("acceptance scenario must be a YAML object")
    schema_version = _string(raw, "schema_version")
    if schema_version == COLLISION_CALIBRATION_SCHEMA_VERSION:
        return _load_collision_calibration_scenario(raw, root)
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported acceptance scenario schema: {schema_version!r}"
        )
    scenario_type = _string(raw, "scenario_type")
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError(
            "scenario_type must be one of: "
            + ", ".join(sorted(SCENARIO_TYPES))
        )
    robot = _mapping(raw, "robot")
    mission = _mapping(raw, "mission")
    expected = _mapping(raw, "expected")
    assertions = _mapping(raw, "assertions")
    timeouts = _mapping(raw, "timeouts")
    readiness = _mapping(raw, "readiness")
    assets_raw = _mapping(raw, "assets")
    initial_pose = _pose(_mapping(robot, "initial_pose"), "robot.initial_pose")
    goal = _pose(_mapping(raw, "goal"), "goal")
    if initial_pose.frame_id != "map" or goal.frame_id != "map":
        raise ValueError("Gazebo acceptance poses must use frame_id='map'")
    if _string(expected, "action_name") != "/move_base":
        raise ValueError("Gazebo acceptance action server must be fixed to /move_base")

    topic_values = readiness.get("topics")
    if not isinstance(topic_values, list) or not topic_values:
        raise ValueError("readiness.topics must be a non-empty list")
    topics = tuple(_nonempty_string(item, "readiness topic") for item in topic_values)
    required_topics = {"/clock", "/scan", "/odom"}
    if not required_topics.issubset(topics):
        raise ValueError(
            "readiness.topics must include /clock, /scan, and /odom"
        )

    transform_values = readiness.get("transforms")
    if not isinstance(transform_values, list) or not transform_values:
        raise ValueError("readiness.transforms must be a non-empty list")
    transforms: list[tuple[str, str]] = []
    for index, item in enumerate(transform_values):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(
                f"readiness.transforms[{index}] must contain parent and child"
            )
        transforms.append(
            (
                _nonempty_string(item[0], "transform parent"),
                _nonempty_string(item[1], "transform child"),
            )
        )
    if ("map", "base_link") not in transforms:
        raise ValueError("readiness.transforms must include map -> base_link")

    assets = AcceptanceAssets(
        launch=_asset_path(assets_raw, "launch", root),
        robot_description=_asset_path(
            assets_raw,
            "robot_description",
            root,
        ),
        collision_monitor=_asset_path(
            assets_raw,
            "collision_monitor",
            root,
        ),
        world=_asset_path(assets_raw, "world", root),
        map=_asset_path(assets_raw, "map", root),
        map_image=_asset_path(assets_raw, "map_image", root),
        ros1_config=_asset_path(assets_raw, "ros1_config", root),
    )
    cancellation: CancellationExpectation | None = None
    timeout: TimeoutExpectation | None = None
    abort: AbortExpectation | None = None
    stall: StallExpectation | None = None
    if scenario_type == "cancel":
        cancellation_raw = _mapping(raw, "cancellation")
        cancellation = CancellationExpectation(
            minimum_feedback_count=_positive_integer(
                cancellation_raw,
                "minimum_feedback_count",
            ),
            minimum_displacement_m=_positive(
                cancellation_raw,
                "minimum_displacement_m",
            ),
            observation_timeout_seconds=_positive(
                cancellation_raw,
                "observation_timeout_seconds",
            ),
            acknowledgement_timeout_seconds=_positive(
                cancellation_raw,
                "acknowledgement_timeout_seconds",
            ),
            maximum_post_cancel_displacement_m=_positive(
                cancellation_raw,
                "maximum_post_cancel_displacement_m",
            ),
        )
    elif raw.get("cancellation") is not None:
        raise ValueError(
            "cancellation settings are only valid for cancel scenarios"
        )
    if scenario_type == "timeout":
        timeout_raw = _mapping(raw, "timeout")
        timeout = TimeoutExpectation(
            execution_timeout_seconds=_positive(
                timeout_raw,
                "execution_timeout_seconds",
            ),
            minimum_feedback_count=_positive_integer(
                timeout_raw,
                "minimum_feedback_count",
            ),
            minimum_displacement_m=_positive(
                timeout_raw,
                "minimum_displacement_m",
            ),
            observation_timeout_seconds=_positive(
                timeout_raw,
                "observation_timeout_seconds",
            ),
            acknowledgement_timeout_seconds=_positive(
                timeout_raw,
                "acknowledgement_timeout_seconds",
            ),
            maximum_post_timeout_displacement_m=_positive(
                timeout_raw,
                "maximum_post_timeout_displacement_m",
            ),
        )
    elif raw.get("timeout") is not None:
        raise ValueError(
            "timeout settings are only valid for timeout scenarios"
        )
    if scenario_type == "abort":
        abort_raw = _mapping(raw, "abort")
        abort = AbortExpectation(
            planner_patience_seconds=_positive(
                abort_raw,
                "planner_patience_seconds",
            ),
            recovery_behavior_enabled=_boolean(
                abort_raw,
                "recovery_behavior_enabled",
            ),
            minimum_feedback_count=_positive_integer(
                abort_raw,
                "minimum_feedback_count",
            ),
            terminal_timeout_seconds=_positive(
                abort_raw,
                "terminal_timeout_seconds",
            ),
            maximum_displacement_m=_positive(
                abort_raw,
                "maximum_displacement_m",
            ),
            required_status_text_substring=_string(
                abort_raw,
                "required_status_text_substring",
            ),
        )
    elif raw.get("abort") is not None:
        raise ValueError("abort settings are only valid for abort scenarios")
    if scenario_type in {"stall_recover", "stall_escalate"}:
        stall_raw = _mapping(raw, "stall")
        recovery_raw = stall_raw.get("recovery_parameters")
        if recovery_raw is not None and not isinstance(recovery_raw, Mapping):
            raise ValueError("stall.recovery_parameters must be an object")
        final_statuses_raw = stall_raw.get(
            "final_actionlib_terminal_statuses",
            [],
        )
        if not isinstance(final_statuses_raw, list) or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in final_statuses_raw
        ):
            raise ValueError(
                "stall.final_actionlib_terminal_statuses must be a list "
                "of integers"
            )
        finding_codes_raw = stall_raw.get(
            "required_diagnostic_finding_codes"
        )
        if not isinstance(finding_codes_raw, list) or not finding_codes_raw:
            raise ValueError(
                "stall.required_diagnostic_finding_codes must be a "
                "non-empty list"
            )
        escalation_reason = stall_raw.get("escalation_reason_code")
        if escalation_reason is not None:
            escalation_reason = _nonempty_string(
                escalation_reason,
                "stall.escalation_reason_code",
            )
        stall = StallExpectation(
            execution_timeout_seconds=_positive(
                stall_raw,
                "execution_timeout_seconds",
            ),
            diagnostics_timeout_seconds=_positive(
                stall_raw,
                "diagnostics_timeout_seconds",
            ),
            minimum_feedback_count=_positive_integer(
                stall_raw,
                "minimum_feedback_count",
            ),
            maximum_stall_displacement_m=_positive(
                stall_raw,
                "maximum_stall_displacement_m",
            ),
            terminal_timeout_seconds=_positive(
                stall_raw,
                "terminal_timeout_seconds",
            ),
            stalled_parameters=_dwa_velocity_parameters(
                _mapping(stall_raw, "stalled_parameters"),
                "stall.stalled_parameters",
            ),
            recovery_parameters=(
                _dwa_velocity_parameters(
                    recovery_raw,
                    "stall.recovery_parameters",
                )
                if isinstance(recovery_raw, Mapping)
                else None
            ),
            maximum_recovery_attempts=_integer(
                stall_raw,
                "maximum_recovery_attempts",
            ),
            first_actionlib_terminal_statuses=_integer_tuple(
                stall_raw,
                "first_actionlib_terminal_statuses",
            ),
            final_actionlib_terminal_statuses=tuple(final_statuses_raw),
            required_diagnostic_finding_codes=tuple(
                _nonempty_string(
                    item,
                    "stall.required_diagnostic_finding_codes item",
                )
                for item in finding_codes_raw
            ),
            escalation_reason_code=escalation_reason,
        )
    elif raw.get("stall") is not None:
        raise ValueError("stall settings are only valid for stall scenarios")
    scenario = AcceptanceScenario(
        schema_version=schema_version,
        scenario_version=_string(raw, "scenario_version"),
        scenario_id=_string(raw, "scenario_id"),
        scenario_type=scenario_type,
        description=_string(raw, "description"),
        seed=_integer(raw, "seed"),
        robot_id=_string(robot, "id"),
        robot_model=_string(robot, "model"),
        initial_pose=initial_pose,
        command=_string(mission, "command"),
        capability=_string(mission, "capability"),
        required_tool=_string(mission, "required_tool"),
        goal=goal,
        plugin_owner=_string(expected, "plugin_owner"),
        backend_class=_string(expected, "backend_class"),
        action_name=_string(expected, "action_name"),
        mission_authorization_status=_string(
            expected,
            "mission_authorization_status",
        ),
        mission_authorization_raw_status=_string(
            expected,
            "mission_authorization_raw_status",
        ),
        terminal_status=_string(expected, "terminal_status"),
        actionlib_terminal_statuses=_integer_tuple(
            expected,
            "actionlib_terminal_statuses",
        ),
        minimum_displacement_m=_positive(assertions, "minimum_displacement_m"),
        initial_position_tolerance_m=_positive(
            assertions,
            "initial_position_tolerance_m",
        ),
        initial_yaw_tolerance_rad=_positive(
            assertions,
            "initial_yaw_tolerance_rad",
        ),
        position_tolerance_m=_positive(assertions, "position_tolerance_m"),
        yaw_tolerance_rad=_positive(assertions, "yaw_tolerance_rad"),
        stopped_linear_velocity_mps=_nonnegative(
            assertions, "stopped_linear_velocity_mps"
        ),
        stopped_angular_velocity_rps=_nonnegative(
            assertions, "stopped_angular_velocity_rps"
        ),
        require_feedback=_boolean(assertions, "require_feedback"),
        ros_readiness_seconds=_positive(timeouts, "ros_readiness_seconds"),
        navigation_seconds=_positive(timeouts, "navigation_seconds"),
        mission_seconds=_positive(timeouts, "mission_seconds"),
        stopped_seconds=_positive(timeouts, "stopped_seconds"),
        readiness_topics=topics,
        readiness_transforms=tuple(transforms),
        cancellation=cancellation,
        timeout=timeout,
        abort=abort,
        stall=stall,
        assets=assets,
    )
    if scenario.planned_displacement_m < scenario.minimum_displacement_m:
        raise ValueError(
            "acceptance goal does not require the configured minimum displacement"
        )
    if scenario.navigation_seconds > scenario.mission_seconds:
        raise ValueError("mission timeout must not be shorter than navigation timeout")
    if scenario.mission_authorization_status != "awaiting_confirmation":
        raise ValueError(
            "Gazebo acceptance must exercise the operator authorization boundary"
        )
    if scenario.mission_authorization_raw_status != "awaiting_confirmation":
        raise ValueError(
            "Gazebo acceptance must first reach awaiting_confirmation"
        )
    if scenario.scenario_type == "success":
        if scenario.terminal_status != "completed":
            raise ValueError(
                "success acceptance terminal status must be completed"
            )
        if scenario.actionlib_terminal_statuses != (3,):
            raise ValueError(
                "success acceptance must require actionlib SUCCEEDED(3)"
            )
    elif scenario.scenario_type == "cancel":
        if scenario.terminal_status != "cancelled":
            raise ValueError(
                "cancel acceptance terminal status must be cancelled"
            )
        if scenario.actionlib_terminal_statuses != (2, 8):
            raise ValueError(
                "cancel acceptance must require PREEMPTED(2) or RECALLED(8)"
            )
        if (
            scenario.cancellation is None
            or scenario.cancellation.minimum_displacement_m
            >= scenario.planned_displacement_m
        ):
            raise ValueError(
                "cancel acceptance must trigger before the configured goal"
            )
    elif scenario.scenario_type == "timeout":
        if scenario.terminal_status != "timed_out":
            raise ValueError(
                "timeout acceptance terminal status must be timed_out"
            )
        if scenario.actionlib_terminal_statuses != (2, 8):
            raise ValueError(
                "timeout acceptance must require PREEMPTED(2) or RECALLED(8)"
            )
        if (
            scenario.timeout is None
            or scenario.timeout.minimum_displacement_m
            >= scenario.planned_displacement_m
        ):
            raise ValueError(
                "timeout acceptance must observe progress before the goal"
            )
        if (
            scenario.timeout.execution_timeout_seconds
            >= scenario.navigation_seconds
        ):
            raise ValueError(
                "physical execution timeout must be shorter than the "
                "navigation observation timeout"
            )
    elif scenario.scenario_type == "abort":
        if scenario.terminal_status != "failed":
            raise ValueError(
                "abort acceptance terminal status must be failed"
            )
        if scenario.actionlib_terminal_statuses != (4,):
            raise ValueError(
                "abort acceptance must require actionlib ABORTED(4)"
            )
        if scenario.abort is None:
            raise ValueError("abort acceptance settings are required")
        if (
            scenario.abort.planner_patience_seconds
            >= scenario.abort.terminal_timeout_seconds
        ):
            raise ValueError(
                "abort terminal timeout must exceed planner patience"
            )
        if (
            scenario.abort.terminal_timeout_seconds
            > scenario.navigation_seconds
        ):
            raise ValueError(
                "abort terminal timeout must not exceed navigation timeout"
            )
    else:
        if scenario.stall is None:
            raise ValueError("stall acceptance settings are required")
        stall = scenario.stall
        if stall.execution_timeout_seconds >= scenario.navigation_seconds:
            raise ValueError(
                "stall execution timeout must be shorter than the "
                "navigation timeout"
            )
        if stall.terminal_timeout_seconds > scenario.mission_seconds:
            raise ValueError(
                "stall terminal timeout must not exceed mission timeout"
            )
        if (
            stall.maximum_stall_displacement_m
            >= scenario.planned_displacement_m
        ):
            raise ValueError(
                "stall displacement bound must be smaller than the planned "
                "goal displacement"
            )
        if stall.stalled_parameters != {
            "max_vel_x": 0.0,
            "min_vel_x": 0.0,
        }:
            raise ValueError(
                "stall acceptance must inject zero DWA forward velocity bounds"
            )
        if stall.first_actionlib_terminal_statuses != (2, 8):
            raise ValueError(
                "stall first action must require PREEMPTED(2) or RECALLED(8)"
            )
        if "navigation_action_failed" not in (
            stall.required_diagnostic_finding_codes
        ):
            raise ValueError(
                "stall acceptance must require navigation_action_failed "
                "diagnostic evidence"
            )
        if scenario.scenario_type == "stall_recover":
            if scenario.terminal_status != "completed":
                raise ValueError(
                    "stall-recover terminal status must be completed"
                )
            if scenario.actionlib_terminal_statuses != (2, 3, 8):
                raise ValueError(
                    "stall-recover must require PREEMPTED/RECALLED then "
                    "SUCCEEDED actionlib evidence"
                )
            if stall.maximum_recovery_attempts != 1:
                raise ValueError(
                    "stall-recover must permit exactly one recovery attempt"
                )
            if stall.recovery_parameters is None:
                raise ValueError(
                    "stall-recover requires bounded recovery parameters"
                )
            if stall.recovery_parameters["max_vel_x"] <= 0:
                raise ValueError(
                    "stall-recover max_vel_x must restore forward motion"
                )
            if stall.final_actionlib_terminal_statuses != (3,):
                raise ValueError(
                    "stall-recover final action must require SUCCEEDED(3)"
                )
            if stall.escalation_reason_code is not None:
                raise ValueError(
                    "stall-recover must not predeclare an escalation reason"
                )
        else:
            if scenario.terminal_status != "escalated":
                raise ValueError(
                    "stall-escalate terminal status must be escalated"
                )
            if scenario.actionlib_terminal_statuses != (2, 8):
                raise ValueError(
                    "stall-escalate must require PREEMPTED(2) or RECALLED(8)"
                )
            if stall.maximum_recovery_attempts != 0:
                raise ValueError(
                    "stall-escalate must prohibit autonomous recovery"
                )
            if stall.recovery_parameters is not None:
                raise ValueError(
                    "stall-escalate must not declare recovery parameters"
                )
            if stall.final_actionlib_terminal_statuses:
                raise ValueError(
                    "stall-escalate must not declare a retry terminal status"
                )
            if stall.escalation_reason_code is None:
                raise ValueError(
                    "stall-escalate requires a structured escalation reason"
                )
    return scenario


def _load_collision_calibration_scenario(
    raw: Mapping[str, Any],
    root: Path,
) -> CollisionCalibrationScenario:
    if _string(raw, "scenario_type") != COLLISION_CALIBRATION_SCENARIO_TYPE:
        raise ValueError(
            "collision calibration schema requires "
            "scenario_type='collision_calibration'"
        )
    if _boolean(raw, "simulation_only") is not True:
        raise ValueError("collision calibration must be simulation_only")
    if _boolean(raw, "excluded_from_task_metrics") is not True:
        raise ValueError(
            "collision calibration must be excluded from task metrics"
        )
    seed = _integer(raw, "seed")
    if seed < 0:
        raise ValueError("collision calibration seed must be non-negative")

    robot = _mapping(raw, "robot")
    initial_pose = _pose(
        _mapping(robot, "initial_pose"),
        "robot.initial_pose",
    )
    if initial_pose.frame_id != "map":
        raise ValueError("collision calibration initial pose must use map")
    robot_model = _string(robot, "model")
    if robot_model != "burger":
        raise ValueError("collision calibration is fixed to TurtleBot3 Burger")

    readiness = _mapping(raw, "readiness")
    action_name = _string(readiness, "action_server")
    if action_name != "/move_base":
        raise ValueError(
            "collision calibration readiness action must be /move_base"
        )
    topic_values = readiness.get("topics")
    if not isinstance(topic_values, list) or not topic_values:
        raise ValueError("readiness.topics must be a non-empty list")
    topics = tuple(
        _nonempty_string(item, "readiness topic")
        for item in topic_values
    )
    if not {"/clock", "/scan", "/odom"}.issubset(topics):
        raise ValueError(
            "readiness.topics must include /clock, /scan, and /odom"
        )
    transform_values = readiness.get("transforms")
    if not isinstance(transform_values, list) or not transform_values:
        raise ValueError("readiness.transforms must be a non-empty list")
    transforms: list[tuple[str, str]] = []
    for index, item in enumerate(transform_values):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(
                f"readiness.transforms[{index}] must contain parent and child"
            )
        transforms.append((
            _nonempty_string(item[0], "transform parent"),
            _nonempty_string(item[1], "transform child"),
        ))
    if ("map", "base_link") not in transforms:
        raise ValueError("readiness.transforms must include map -> base_link")

    assertions = _mapping(raw, "assertions")
    timeouts = _mapping(raw, "timeouts")
    calibration_raw = _mapping(raw, "calibration")
    injection = _mapping(calibration_raw, "injection")
    if _string(injection, "method") != "gazebo_spawn_sdf_model_static_overlap":
        raise ValueError("collision calibration injection method is fixed")
    model_name = _string(injection, "model_name")
    if (
        not _GAZEBO_MODEL_NAME_RE.fullmatch(model_name)
        or model_name != _COLLISION_CALIBRATION_MODEL_NAME
    ):
        raise ValueError("collision calibration model_name is fixed")
    if _string(injection, "reference_frame") != "world":
        raise ValueError("collision calibration reference_frame must be world")
    if _string(injection, "asset_label") != "collision_probe":
        raise ValueError("collision calibration asset_label is fixed")
    world_pose = _pose3d(
        _mapping(injection, "world_pose"),
        "calibration.injection.world_pose",
    )
    expected_probe_pose = Pose3D(
        x=initial_pose.x + 0.05,
        y=initial_pose.y,
        z=0.08,
        yaw=initial_pose.yaw,
    )
    if any(
        abs(actual - expected) > 1e-9
        for actual, expected in (
            (world_pose.x, expected_probe_pose.x),
            (world_pose.y, expected_probe_pose.y),
            (world_pose.z, expected_probe_pose.z),
            (world_pose.yaw, expected_probe_pose.yaw),
        )
    ):
        raise ValueError(
            "collision calibration probe must use the fixed shallow-overlap pose"
        )
    detection_timeout = _positive(
        timeouts,
        "collision_detection_seconds",
    )
    if detection_timeout > 15.0:
        raise ValueError("collision calibration detection timeout is too large")
    maximum_displacement = _positive(
        assertions,
        "maximum_robot_displacement_m",
    )
    if maximum_displacement > 0.2:
        raise ValueError(
            "collision calibration displacement bound must not exceed 0.2 m"
        )

    assets_raw = _mapping(raw, "assets")
    assets = AcceptanceAssets(
        launch=_asset_path(assets_raw, "launch", root),
        robot_description=_asset_path(
            assets_raw,
            "robot_description",
            root,
        ),
        collision_monitor=_asset_path(
            assets_raw,
            "collision_monitor",
            root,
        ),
        world=_asset_path(assets_raw, "world", root),
        map=_asset_path(assets_raw, "map", root),
        map_image=_asset_path(assets_raw, "map_image", root),
        ros1_config=_asset_path(assets_raw, "ros1_config", root),
        collision_probe=_asset_path(
            assets_raw,
            "collision_probe",
            root,
        ),
    )
    scenario = CollisionCalibrationScenario(
        schema_version=COLLISION_CALIBRATION_SCHEMA_VERSION,
        scenario_version=_string(raw, "scenario_version"),
        scenario_id=_string(raw, "scenario_id"),
        scenario_type=COLLISION_CALIBRATION_SCENARIO_TYPE,
        description=_string(raw, "description"),
        seed=seed,
        robot_id=_string(robot, "id"),
        robot_model=robot_model,
        initial_pose=initial_pose,
        action_name=action_name,
        initial_position_tolerance_m=_positive(
            assertions,
            "initial_position_tolerance_m",
        ),
        initial_yaw_tolerance_rad=_positive(
            assertions,
            "initial_yaw_tolerance_rad",
        ),
        stopped_linear_velocity_mps=_nonnegative(
            assertions,
            "stopped_linear_velocity_mps",
        ),
        stopped_angular_velocity_rps=_nonnegative(
            assertions,
            "stopped_angular_velocity_rps",
        ),
        ros_readiness_seconds=_positive(
            timeouts,
            "ros_readiness_seconds",
        ),
        stopped_seconds=_positive(timeouts, "stopped_seconds"),
        readiness_topics=topics,
        readiness_transforms=tuple(transforms),
        calibration=CollisionCalibrationExpectation(
            model_name=model_name,
            world_pose=world_pose,
            detection_timeout_seconds=detection_timeout,
            minimum_prohibited_contact_states=_positive_integer(
                assertions,
                "minimum_prohibited_contact_states",
            ),
            minimum_collision_episodes=_positive_integer(
                assertions,
                "minimum_collision_episodes",
            ),
            maximum_robot_displacement_m=maximum_displacement,
        ),
        assets=assets,
    )
    if scenario.calibration.minimum_collision_episodes > 8:
        raise ValueError("collision calibration episode minimum is too large")
    if scenario.calibration.minimum_prohibited_contact_states > 100:
        raise ValueError("collision calibration state minimum is too large")
    return scenario


def _dwa_velocity_parameters(
    raw: Mapping[str, Any],
    label: str,
) -> dict[str, float]:
    expected = {"max_vel_x", "min_vel_x"}
    if set(raw) != expected:
        raise ValueError(
            f"{label} must contain exactly max_vel_x and min_vel_x"
        )
    values = {
        key: _finite(raw, key, label)
        for key in sorted(expected)
    }
    if values["min_vel_x"] > values["max_vel_x"]:
        raise ValueError(f"{label} min_vel_x must not exceed max_vel_x")
    return values


def _pose(raw: Mapping[str, Any], label: str) -> Pose2D:
    return Pose2D(
        frame_id=_string(raw, "frame_id"),
        x=_finite(raw, "x", label),
        y=_finite(raw, "y", label),
        yaw=_finite(raw, "yaw", label),
    )


def _pose3d(raw: Mapping[str, Any], label: str) -> Pose3D:
    return Pose3D(
        x=_finite(raw, "x", label),
        y=_finite(raw, "y", label),
        z=_finite(raw, "z", label),
        yaw=_finite(raw, "yaw", label),
    )


def _pose_dict(pose: Pose2D) -> dict[str, Any]:
    return {
        "frame_id": pose.frame_id,
        "x": pose.x,
        "y": pose.y,
        "yaw": pose.yaw,
    }


def _mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _string(raw: Mapping[str, Any], key: str) -> str:
    return _nonempty_string(raw.get(key), key)


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _positive_integer(raw: Mapping[str, Any], key: str) -> int:
    value = _integer(raw, key)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _integer_tuple(raw: Mapping[str, Any], key: str) -> tuple[int, ...]:
    values = raw.get(key)
    if not isinstance(values, list) or not values:
        raise ValueError(f"{key} must be a non-empty integer list")
    result: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be a non-empty integer list")
        if value < 0 or value > 9:
            raise ValueError(f"{key} contains an invalid actionlib status")
        if value in result:
            raise ValueError(f"{key} must not contain duplicate statuses")
        result.append(value)
    return tuple(result)


def _finite(raw: Mapping[str, Any], key: str, label: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}.{key} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{label}.{key} must be finite")
    return result


def _positive(raw: Mapping[str, Any], key: str) -> float:
    value = _finite(raw, key, "value")
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _nonnegative(raw: Mapping[str, Any], key: str) -> float:
    value = _finite(raw, key, "value")
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
    return value


def _boolean(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _asset_path(raw: Mapping[str, Any], key: str, root: Path) -> Path:
    value = _string(raw, key)
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"assets.{key} must be repository-relative")
    resolved = (root / relative).resolve(strict=True)
    _require_within(resolved, root, f"assets.{key}")
    if not resolved.is_file():
        raise ValueError(f"assets.{key} must reference a file")
    return resolved


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the repository") from exc


__all__ = [
    "AbortExpectation",
    "AcceptanceAssets",
    "AcceptanceScenario",
    "CancellationExpectation",
    "COLLISION_CALIBRATION_SCHEMA_VERSION",
    "COLLISION_CALIBRATION_SCENARIO_TYPE",
    "CollisionCalibrationExpectation",
    "CollisionCalibrationScenario",
    "Pose2D",
    "Pose3D",
    "default_scenario_path",
    "load_acceptance_scenario",
    "repository_root",
]
