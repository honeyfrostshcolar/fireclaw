"""Shared contracts and artifact helpers for FireClaw evaluations."""

from fireclaw_core.evaluation.artifacts import EvaluationRunBundle, make_run_id
from fireclaw_core.evaluation.calibration import (
    CollisionCalibrationCaseResult,
    GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION,
    collect_gazebo_collision_calibration_case,
    score_gazebo_collision_calibration_case,
)
from fireclaw_core.evaluation.contracts import (
    EVALUATION_RUN_SCHEMA_VERSION,
    SCENARIO_SUITE_SCHEMA_VERSION,
    EvaluationScenario,
    EvaluationSuite,
    load_evaluation_suite,
)
from fireclaw_core.evaluation.planning import (
    PLANNING_EVALUATION_PROTOCOL_VERSION,
    evaluate_planning_case,
)
from fireclaw_core.evaluation.system import (
    ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
    SystemCaseResult,
    collect_ros_gazebo_case,
    score_ros_gazebo_case,
)

__all__ = [
    "EVALUATION_RUN_SCHEMA_VERSION",
    "GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION",
    "SCENARIO_SUITE_SCHEMA_VERSION",
    "EvaluationRunBundle",
    "EvaluationScenario",
    "EvaluationSuite",
    "CollisionCalibrationCaseResult",
    "PLANNING_EVALUATION_PROTOCOL_VERSION",
    "ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION",
    "SystemCaseResult",
    "collect_ros_gazebo_case",
    "collect_gazebo_collision_calibration_case",
    "evaluate_planning_case",
    "load_evaluation_suite",
    "make_run_id",
    "score_ros_gazebo_case",
    "score_gazebo_collision_calibration_case",
]
