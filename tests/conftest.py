from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable

import pytest

from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile

_TEST_IMAGE_ID = "sha256:" + ("a" * 64)


class _TestSandboxedSkillExecutor:
    """Host-backed test double; production legacy skills never use this path."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, object]] = []

    def stage_skill(self, source_dir: str | Path, *, skill_name: str) -> str:
        destination = self.root / skill_name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source_dir, destination)
        return skill_name

    def execute_process(
        self,
        *,
        argv: list[str],
        cwd: str,
        stdin_text: str | None,
        timeout_seconds: float,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "stdin_text": stdin_text,
                "timeout_seconds": timeout_seconds,
            }
        )
        process = subprocess.Popen(
            argv,
            cwd=self.root / cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        deadline = time.monotonic() + timeout_seconds
        input_pending = True
        cancellation_requested = cancellation_requested or (lambda: False)
        while True:
            if cancellation_requested():
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "exit_code": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": False,
                    "cancelled": True,
                    "timeout_seconds": timeout_seconds,
                }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "exit_code": None,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                    "cancelled": False,
                    "timeout_seconds": timeout_seconds,
                }
            try:
                if input_pending:
                    stdout, stderr = process.communicate(
                        input=stdin_text,
                        timeout=min(0.02, remaining),
                    )
                    input_pending = False
                else:
                    stdout, stderr = process.communicate(
                        timeout=min(0.02, remaining)
                    )
                return {
                    "exit_code": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": False,
                    "cancelled": False,
                    "timeout_seconds": timeout_seconds,
                }
            except subprocess.TimeoutExpired:
                input_pending = False


@pytest.fixture
def legacy_skill_executor(tmp_path: Path) -> _TestSandboxedSkillExecutor:
    return _TestSandboxedSkillExecutor(
        tmp_path.parent / f"{tmp_path.name}-legacy-skill-sandbox"
    )


@pytest.fixture
def legacy_skill_profile(tmp_path: Path) -> DeploymentProfile:
    return DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=SandboxProfile(
            enabled=True,
            image="fireclaw-test-sandbox:latest",
            image_digest=_TEST_IMAGE_ID,
            workspace_root=tmp_path / "computer-sandbox",
        ),
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_ros1 = os.environ.get("FIRECLAW_RUN_ROS1_SMOKE") == "1"
    ros_available = shutil.which("roscore") is not None and shutil.which("rosrun") is not None
    skip_ros1 = pytest.mark.skip(
        reason=(
            "ROS1 smoke tests require FIRECLAW_RUN_ROS1_SMOKE=1 "
            "and ROS1 commands (roscore, rosrun) on PATH."
        )
    )
    for item in items:
        if "ros1_smoke" in item.keywords and not (run_ros1 and ros_available):
            item.add_marker(skip_ros1)
