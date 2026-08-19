from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import unittest
from unittest.mock import MagicMock

# Add src to sys.path if not present
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    import pytest
except ImportError:
    class _RaisesContext:
        def __init__(self, expected_exc: type[Exception] | tuple[type[Exception], ...]) -> None:
            self.expected_exc = expected_exc
            self.value: Any = None

        def __enter__(self) -> _RaisesContext:
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
            if exc_type is None:
                raise AssertionError(f"Expected exception {self.expected_exc} was not raised")
            if issubclass(exc_type, self.expected_exc):
                self.value = exc_val
                return True
            return False

    class _MockPytest:
        @staticmethod
        def raises(expected_exc: type[Exception] | tuple[type[Exception], ...]) -> _RaisesContext:
            return _RaisesContext(expected_exc)

    pytest = _MockPytest()  # type: ignore[assignment]

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment import load_runtime_deployment_profile
from fireclaw_core.infra.daemon_manager import (
    DAEMON_STATE_FILENAME,
    DaemonRuntimeManager,
    DaemonState,
    FireClawDaemonError,
)
from fireclaw_core.infra.user_setup import (
    FireClawSetupError,
    setup_fireclaw,
)

SOURCE_ROOT = REPO_ROOT


class _DummyPlan:
    fingerprint = "daemon-test-fingerprint"

    def to_dict(self) -> dict[str, object]:
        return {"status": "ready", "fingerprint": self.fingerprint}


def _dummy_planner(profile_path: str | Path) -> _DummyPlan:
    return _DummyPlan()


def _dummy_applier(plan: _DummyPlan) -> dict[str, object]:
    return {
        "status": "installed",
        "fingerprint": plan.fingerprint,
        "reused": False,
    }


def _setup_simulation_environment(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "fireclaw-home"
    result = setup_fireclaw(
        mode="simulation",
        runtime_root=runtime_root,
        source_root=SOURCE_ROOT,
        deploy=True,
        plan_builder=_dummy_planner,
        deployment_applier=_dummy_applier,
    )
    profile_path = Path(result["profile_path"])
    return runtime_root, profile_path


class TestDaemonManager(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_daemon_state_dataclass_serialization(self) -> None:
        state = DaemonState(
            pid=12345,
            pgid=12345,
            profile_path=Path("/tmp/test.toml"),
            mode="simulation",
            started_at="2026-08-14T00:00:00+00:00",
            gateway_url="http://127.0.0.1:8766",
            log_path=Path("/tmp/runtime-daemon.log"),
            status="running",
            robot_id="test-bot",
            schema_version=1,
        )
        data = state.to_dict()
        self.assertEqual(data["pid"], 12345)
        self.assertEqual(data["pgid"], 12345)
        self.assertEqual(data["profile_path"], "/tmp/test.toml")
        self.assertEqual(data["mode"], "simulation")
        self.assertEqual(data["gateway_url"], "http://127.0.0.1:8766")
        self.assertEqual(data["log_path"], "/tmp/runtime-daemon.log")
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["robot_id"], "test-bot")
        self.assertEqual(data["schema_version"], 1)

        restored = DaemonState.from_dict(data)
        self.assertEqual(restored, state)

    def test_start_daemon_fails_when_no_active_profile_and_no_profile_arg(self) -> None:
        runtime_root = self.tmp_path / "fireclaw-empty-home"
        runtime_root.mkdir(parents=True)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        with pytest.raises((FireClawSetupError, FireClawDaemonError)) as exc_info:
            manager.start_daemon(None)
        self.assertEqual(getattr(exc_info.value, "code", ""), "active_profile_missing")

    def test_start_daemon_success_spawns_process_and_writes_state_file(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        dummy_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            def fake_spawner(cmd: list[str], log_file: Path, cwd: Path) -> int:
                return dummy_proc.pid

            def fake_health_poller(url: str, timeout: float) -> bool:
                return True

            result = manager.start_daemon(
                profile_path,
                spawner=fake_spawner,
                health_poller=fake_health_poller,
                timeout=2.0,
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(result["daemon"]["pid"], dummy_proc.pid)
            self.assertEqual(result["daemon"]["mode"], "simulation")
            self.assertEqual(result["daemon"]["robot_id"], "gazebo_turtlebot3")

            state_file = runtime_root / "state" / DAEMON_STATE_FILENAME
            self.assertTrue(state_file.is_file())
            self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(state_file.parent.stat().st_mode), 0o700)

            logs_dir = runtime_root / "logs"
            self.assertTrue(logs_dir.is_dir())
            self.assertEqual(stat.S_IMODE(logs_dir.stat().st_mode), 0o700)

            status_result = manager.get_daemon_status(profile_path)
            self.assertEqual(status_result["status"], "running")
            self.assertEqual(status_result["daemon"]["pid"], dummy_proc.pid)
        finally:
            try:
                os.killpg(dummy_proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                dummy_proc.wait(timeout=2.0)
            except Exception:
                pass

    def test_start_daemon_idempotent_when_already_running(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        dummy_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            def fake_spawner(cmd: list[str], log_file: Path, cwd: Path) -> int:
                return dummy_proc.pid

            def fake_health_poller(url: str, timeout: float) -> bool:
                return True

            first = manager.start_daemon(
                profile_path,
                spawner=fake_spawner,
                health_poller=fake_health_poller,
            )
            self.assertEqual(first["status"], "running")

            second = manager.start_daemon(
                profile_path,
                spawner=fake_spawner,
                health_poller=fake_health_poller,
            )
            self.assertEqual(second["status"], "already_running")
            self.assertEqual(second["daemon"]["pid"], dummy_proc.pid)
        finally:
            try:
                os.killpg(dummy_proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                dummy_proc.wait(timeout=2.0)
            except Exception:
                pass

    def test_get_daemon_status_stale_pid_self_healing(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        dead_pid = 999999
        state_dir = runtime_root / "state"
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_file = state_dir / DAEMON_STATE_FILENAME

        stale_state = {
            "schema_version": 1,
            "pid": dead_pid,
            "pgid": dead_pid,
            "profile_path": str(profile_path),
            "mode": "simulation",
            "started_at": "2026-08-14T00:00:00+00:00",
            "gateway_url": "http://127.0.0.1:8766",
            "log_path": str(runtime_root / "logs" / "runtime-daemon.log"),
            "status": "running",
            "robot_id": "gazebo_turtlebot3",
        }
        state_file.write_text(json.dumps(stale_state), encoding="utf-8")
        state_file.chmod(0o600)

        status_result = manager.get_daemon_status(profile_path)
        self.assertEqual(status_result["status"], "not_running")
        self.assertTrue(status_result.get("stale_cleaned"))
        self.assertEqual(status_result.get("previous_pid"), dead_pid)
        self.assertFalse(state_file.exists())

    def test_stop_daemon_graceful_sigterm_and_cleanup(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        pid = proc.pid

        state_dir = runtime_root / "state"
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_file = state_dir / DAEMON_STATE_FILENAME

        state = {
            "schema_version": 1,
            "pid": pid,
            "pgid": pid,
            "profile_path": str(profile_path),
            "mode": "simulation",
            "started_at": "2026-08-14T00:00:00+00:00",
            "gateway_url": "http://127.0.0.1:8766",
            "log_path": str(runtime_root / "logs" / "runtime-daemon.log"),
            "status": "running",
            "robot_id": "gazebo_turtlebot3",
        }
        state_file.write_text(json.dumps(state), encoding="utf-8")
        state_file.chmod(0o600)

        stop_result = manager.stop_daemon(profile_path, timeout=5.0)
        self.assertEqual(stop_result["status"], "stopped")
        self.assertEqual(stop_result["pid"], pid)
        self.assertFalse(state_file.exists())

        proc.wait(timeout=2.0)
        self.assertIsNotNone(proc.poll())

    def test_stop_daemon_when_not_running_is_safe_noop(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        result = manager.stop_daemon(profile_path)
        self.assertEqual(result["status"], "not_running")

    def test_open_console_when_running_and_not_running(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        with pytest.raises(FireClawDaemonError) as exc_info:
            manager.open_console(profile_path, browser=False)
        self.assertEqual(exc_info.value.code, "daemon_not_running")

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            def fake_spawner(cmd: list[str], log_file: Path, cwd: Path) -> int:
                return proc.pid

            def fake_health_poller(url: str, timeout: float) -> bool:
                return True

            manager.start_daemon(
                profile_path,
                spawner=fake_spawner,
                health_poller=fake_health_poller,
            )

            opened = manager.open_console(profile_path, browser=False)
            self.assertEqual(opened["status"], "ready")
            self.assertEqual(opened["url"], "http://127.0.0.1:8766")
            self.assertFalse(opened["browser_opened"])

            mock_browser = MagicMock(return_value=True)
            opened_with_browser = manager.open_console(
                profile_path,
                browser=True,
                browser_opener=mock_browser,
            )
            self.assertEqual(opened_with_browser["status"], "opened")
            self.assertTrue(opened_with_browser["browser_opened"])
            mock_browser.assert_called_once_with("http://127.0.0.1:8766")
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait(timeout=2.0)

    def test_real_mode_requires_hardware_safety_preflight(self) -> None:
        runtime_root = self.tmp_path / "fireclaw-real-home"
        runtime_root.mkdir(parents=True)
        manager = DaemonRuntimeManager(runtime_root=runtime_root)

        profile_path = self.tmp_path / "real_robot.toml"
        profile_path.write_text(
            f"""
[fireclaw.setup]
template_id = "external-profile"

[robot]
id = "real_bot"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
data_dir = "robot-data"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[capability_skill_chains]
navigation = ["navigate_to_point"]

[deployment]
id = "real_bot"
mode = "real"
output_root = "deployments"
selected_plugin_ids = ["navigation-move-base"]

[deployment.ros1]
distro = "noetic"
setup_files = ["/opt/ros/noetic/setup.bash"]

[plugins]
paths = ["{REPO_ROOT / 'extensions'}"]
selected = ["navigation-move-base"]
""",
            encoding="utf-8",
        )

        with pytest.raises(FireClawDaemonError) as exc_info:
            manager.start_daemon(profile_path)
        self.assertEqual(exc_info.value.code, "hardware_safety_preflight_blocked")


if __name__ == "__main__":
    unittest.main()
