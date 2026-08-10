from __future__ import annotations

import os
from pathlib import Path

import pytest

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
) -> ArtifactBundle:
    bundle = ArtifactBundle.from_environment(repo_root)
    bundle.write_json(
        "system-versions.json",
        ros_gazebo_runtime_snapshot(),
    )
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
