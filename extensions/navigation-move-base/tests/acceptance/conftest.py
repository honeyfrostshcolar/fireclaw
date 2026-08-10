from __future__ import annotations

import os
from pathlib import Path

import pytest

from fireclaw_core.devtools.gazebo_acceptance_freeze import (
    FROZEN_SELECTION_SCHEMA_VERSION,
    sha256_file,
    verify_frozen_selection,
)
from fireclaw_core.evaluation.provenance import ros_gazebo_runtime_snapshot

from .artifacts import ArtifactBundle
from .ros_harness import RosHarness
from .scenario import load_acceptance_scenario, repository_root


@pytest.fixture(scope="session", autouse=True)
def require_explicit_gazebo_acceptance_opt_in() -> None:
    if os.getenv("FIRECLAW_RUN_GAZEBO_ACCEPTANCE") != "1":
        pytest.skip(
            "set FIRECLAW_RUN_GAZEBO_ACCEPTANCE=1 to run live Gazebo acceptance"
        )


@pytest.fixture(scope="session")
def repo_root(
    require_explicit_gazebo_acceptance_opt_in: None,
) -> Path:
    return repository_root()


@pytest.fixture(scope="session")
def acceptance_scenario(
    require_explicit_gazebo_acceptance_opt_in: None,
    repo_root: Path,
):
    configured = os.getenv("FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO")
    return load_acceptance_scenario(configured, repo_root=repo_root)


@pytest.fixture(scope="session")
def artifact_bundle(
    require_explicit_gazebo_acceptance_opt_in: None,
    repo_root: Path,
    acceptance_scenario,
) -> ArtifactBundle:
    bundle = ArtifactBundle.from_environment(repo_root)
    bundle.write_json(
        "system-versions.json",
        ros_gazebo_runtime_snapshot(),
    )
    suite_path = (
        repo_root
        / "extensions/navigation-move-base/config/acceptance/frozen-suite.yaml"
    )
    configured = os.getenv("FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO")
    scenario_path = Path(configured) if configured else (
        repo_root
        / "extensions/navigation-move-base/config/acceptance/success.yaml"
    )
    if not scenario_path.is_absolute():
        scenario_path = repo_root / scenario_path
    scenario_path = scenario_path.resolve(strict=True)
    split = os.getenv("FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT", "development")
    if (
        split in {"validation", "test"}
        and acceptance_scenario.scenario_type != "collision_calibration"
    ):
        frozen = verify_frozen_selection(
            suite_path,
            scenario_path,
            split=split,
            repo_root=repo_root,
        )
    elif acceptance_scenario.scenario_type == "collision_calibration":
        frozen = {
            "schema_version": FROZEN_SELECTION_SCHEMA_VERSION,
            "status": "not_applicable",
            "reason": "collision calibration is simulation-only and excluded from task metrics",
            "split": split,
            "suite_path": suite_path.relative_to(repo_root).as_posix(),
            "suite_sha256": sha256_file(suite_path),
            "scenario_id": acceptance_scenario.scenario_id,
        }
    else:
        frozen = {
            "schema_version": FROZEN_SELECTION_SCHEMA_VERSION,
            "status": "development_not_frozen",
            "split": split,
            "suite_path": suite_path.relative_to(repo_root).as_posix(),
            "suite_sha256": sha256_file(suite_path),
            "scenario_id": acceptance_scenario.scenario_id,
        }
    bundle.write_json("frozen-suite.json", frozen)
    return bundle


@pytest.fixture(scope="session")
def ros_harness(
    acceptance_scenario,
    artifact_bundle: ArtifactBundle,
) -> RosHarness:
    # Depend on artifact_bundle so even readiness failures have a stable run
    # directory selected by the trusted runner.
    _ = artifact_bundle
    return RosHarness(acceptance_scenario)
