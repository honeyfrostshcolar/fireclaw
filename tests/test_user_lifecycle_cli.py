"""Tests for FireClaw user lifecycle CLI subcommands: start, stop, open."""

from __future__ import annotations

import argparse
from io import StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fireclaw_core.__main__ import KNOWN_SUBCOMMANDS, main as main_entrypoint
from fireclaw_core.infra.daemon_manager import (
    DaemonRuntimeManager,
    DaemonState,
    FireClawDaemonError,
)
from fireclaw_core.infra.user_setup import (
    FireClawSetupError,
    setup_fireclaw,
)
from fireclaw_core.mission.mission_cli import (
    handle_open,
    handle_start,
    handle_stop,
    main as mission_main,
)

SOURCE_ROOT = REPO_ROOT


class _DummyPlan:
    fingerprint = "cli-lifecycle-test-fingerprint"

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


class TestUserLifecycleCli(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_known_subcommands_includes_lifecycle_commands(self) -> None:
        self.assertIn("start", KNOWN_SUBCOMMANDS)
        self.assertIn("stop", KNOWN_SUBCOMMANDS)
        self.assertIn("open", KNOWN_SUBCOMMANDS)

    def test_handle_start_json_success(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        out = StringIO()

        # Mock DaemonRuntimeManager
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.start_daemon.return_value = {
            "status": "running",
            "daemon": {
                "pid": 99999,
                "pgid": 99999,
                "profile_path": str(profile_path),
                "mode": "simulation",
                "started_at": "2026-08-14T00:00:00+00:00",
                "gateway_url": "http://127.0.0.1:8766",
                "log_path": str(runtime_root / "logs" / "runtime-daemon.log"),
                "status": "running",
                "robot_id": "gazebo_turtlebot3",
            },
            "health_verified": True,
            "message": "FireClaw daemon started successfully.",
        }

        args = argparse.Namespace(
            command_name="start",
            profile=profile_path,
            foreground=False,
            timeout=15.0,
            runtime_root=runtime_root,
            json=True,
        )

        code = handle_start(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["daemon"]["pid"], 99999)
        mock_manager.start_daemon.assert_called_once_with(
            profile_path=profile_path,
            foreground=False,
            timeout=15.0,
        )

    def test_handle_start_human_output_success(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        out = StringIO()

        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.start_daemon.return_value = {
            "status": "running",
            "daemon": {
                "pid": 12345,
                "pgid": 12345,
                "profile_path": str(profile_path),
                "mode": "simulation",
                "started_at": "2026-08-14T00:00:00+00:00",
                "gateway_url": "http://127.0.0.1:8766",
                "log_path": str(runtime_root / "logs" / "runtime-daemon.log"),
                "status": "running",
                "robot_id": "gazebo_turtlebot3",
            },
            "health_verified": True,
            "message": "FireClaw daemon started successfully.",
        }

        args = argparse.Namespace(
            command_name="start",
            profile=profile_path,
            foreground=False,
            timeout=15.0,
            runtime_root=runtime_root,
            json=False,
        )

        code = handle_start(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        output_str = out.getvalue()
        self.assertIn("FireClaw 守护进程已启动", output_str)
        self.assertIn("12345", output_str)
        self.assertIn("http://127.0.0.1:8766", output_str)
        self.assertIn("fireclaw open", output_str)

    def test_handle_start_foreground_mode(self) -> None:
        runtime_root, profile_path = _setup_simulation_environment(self.tmp_path)
        out = StringIO()

        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.start_daemon.return_value = {
            "status": "stopped",
            "message": "Supervisor stopped.",
        }

        args = argparse.Namespace(
            command_name="start",
            profile=profile_path,
            foreground=True,
            timeout=15.0,
            runtime_root=runtime_root,
            json=True,
        )

        code = handle_start(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        mock_manager.start_daemon.assert_called_once_with(
            profile_path=profile_path,
            foreground=True,
            timeout=15.0,
        )

    def test_handle_start_error_handling(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.start_daemon.side_effect = FireClawDaemonError(
            "Hardware safety failed.",
            code="hardware_safety_preflight_blocked",
            operator_action="运行 fireclaw hardware-safety preflight 排查。",
        )

        args = argparse.Namespace(
            command_name="start",
            profile=None,
            foreground=False,
            timeout=15.0,
            runtime_root=None,
            json=True,
        )

        code = handle_start(args, out=out, manager=mock_manager)
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["code"], "hardware_safety_preflight_blocked")
        self.assertIn("Hardware safety failed", payload["message"])

        # Test human error output
        out_human = StringIO()
        args.json = False
        code_human = handle_start(args, out=out_human, manager=mock_manager)
        self.assertEqual(code_human, 2)
        output_str = out_human.getvalue()
        self.assertIn("FireClaw 启动未完成", output_str)
        self.assertIn("发生了什么：Hardware safety failed.", output_str)
        self.assertIn("下一步：运行 fireclaw hardware-safety preflight 排查。", output_str)

    def test_handle_stop_json_success(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.stop_daemon.return_value = {
            "status": "stopped",
            "pid": 12345,
            "message": "Daemon (PID 12345) stopped.",
        }

        args = argparse.Namespace(
            command_name="stop",
            profile=None,
            timeout=10.0,
            force=False,
            runtime_root=None,
            json=True,
        )

        code = handle_stop(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "stopped")
        self.assertEqual(payload["pid"], 12345)
        mock_manager.stop_daemon.assert_called_once_with(
            profile_path=None,
            timeout=10.0,
            force=False,
        )

    def test_handle_stop_human_output_success_and_not_running(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.stop_daemon.return_value = {
            "status": "stopped",
            "pid": 54321,
            "message": "Daemon (PID 54321) stopped.",
        }

        args = argparse.Namespace(
            command_name="stop",
            profile=None,
            timeout=10.0,
            force=True,
            runtime_root=None,
            json=False,
        )

        code = handle_stop(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        self.assertIn("FireClaw 守护进程已停止", out.getvalue())
        self.assertIn("54321", out.getvalue())
        self.assertIn("机器人物理状态：UNKNOWN", out.getvalue())
        self.assertIn("不构成物理停止证据", out.getvalue())
        self.assertNotIn("机器人状态：安全停机", out.getvalue())

        # Test not_running output
        out_not_running = StringIO()
        mock_manager.stop_daemon.return_value = {
            "status": "not_running",
            "message": "FireClaw daemon is not running.",
        }
        code_nr = handle_stop(args, out=out_not_running, manager=mock_manager)
        self.assertEqual(code_nr, 0)
        self.assertIn("FireClaw 守护进程未在运行", out_not_running.getvalue())

    def test_handle_stop_error_handling(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.stop_daemon.side_effect = FireClawDaemonError(
            "Stop permission error.",
            code="daemon_stop_failed",
            operator_action="使用 kill -9 手动终止进程。",
        )

        args = argparse.Namespace(
            command_name="stop",
            profile=None,
            timeout=10.0,
            force=False,
            runtime_root=None,
            json=True,
        )

        code = handle_stop(args, out=out, manager=mock_manager)
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["code"], "daemon_stop_failed")

    def test_handle_open_json_and_no_browser(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.open_console.return_value = {
            "status": "ready",
            "url": "http://127.0.0.1:8766",
            "browser_opened": False,
            "pid": 12345,
            "robot_id": "gazebo_turtlebot3",
            "message": "Console ready at http://127.0.0.1:8766",
        }

        args = argparse.Namespace(
            command_name="open",
            profile=None,
            browser=False,
            runtime_root=None,
            json=True,
        )

        code = handle_open(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["url"], "http://127.0.0.1:8766")
        self.assertFalse(payload["browser_opened"])
        mock_manager.open_console.assert_called_once_with(
            profile_path=None,
            browser=False,
            browser_opener=None,
        )

    def test_handle_open_human_output_and_browser_opened(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.open_console.return_value = {
            "status": "opened",
            "url": "http://127.0.0.1:8766",
            "browser_opened": True,
            "pid": 12345,
            "robot_id": "gazebo_turtlebot3",
            "message": "Console ready at http://127.0.0.1:8766",
        }

        args = argparse.Namespace(
            command_name="open",
            profile=None,
            browser=True,
            runtime_root=None,
            json=False,
        )

        code = handle_open(args, out=out, manager=mock_manager)
        self.assertEqual(code, 0)
        output_str = out.getvalue()
        self.assertIn("FireClaw 控制台已就绪", output_str)
        self.assertIn("http://127.0.0.1:8766", output_str)
        self.assertIn("已在默认浏览器中打开", output_str)

    def test_handle_open_error_when_daemon_not_running(self) -> None:
        out = StringIO()
        mock_manager = MagicMock(spec=DaemonRuntimeManager)
        mock_manager.open_console.side_effect = FireClawDaemonError(
            "FireClaw daemon is not running.",
            code="daemon_not_running",
            operator_action="先运行 fireclaw start 启动守护进程。",
        )

        args = argparse.Namespace(
            command_name="open",
            profile=None,
            browser=True,
            runtime_root=None,
            json=True,
        )

        code = handle_open(args, out=out, manager=mock_manager)
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["code"], "daemon_not_running")
        self.assertIn("先运行 fireclaw start", payload["operator_action"])

    def test_mission_cli_subparsers_integration(self) -> None:
        """Test that mission_cli main parses start, stop, open with full arguments."""
        with patch("sys.argv", ["fireclaw", "start", "--json", "--timeout", "5.0"]):
            with patch("fireclaw_core.mission.mission_cli.handle_start", return_value=0) as mock_handle_start:
                code = mission_main()
                self.assertEqual(code, 0)
                mock_handle_start.assert_called_once()
                args = mock_handle_start.call_args[0][0]
                self.assertEqual(args.command_name, "start")
                self.assertTrue(args.json)
                self.assertEqual(args.timeout, 5.0)

        with patch("sys.argv", ["fireclaw", "stop", "--force", "--json"]):
            with patch("fireclaw_core.mission.mission_cli.handle_stop", return_value=0) as mock_handle_stop:
                code = mission_main()
                self.assertEqual(code, 0)
                mock_handle_stop.assert_called_once()
                args = mock_handle_stop.call_args[0][0]
                self.assertEqual(args.command_name, "stop")
                self.assertTrue(args.force)
                self.assertTrue(args.json)

        with patch("sys.argv", ["fireclaw", "open", "--no-browser", "--json"]):
            with patch("fireclaw_core.mission.mission_cli.handle_open", return_value=0) as mock_handle_open:
                code = mission_main()
                self.assertEqual(code, 0)
                mock_handle_open.assert_called_once()
                args = mock_handle_open.call_args[0][0]
                self.assertEqual(args.command_name, "open")
                self.assertFalse(args.browser)
                self.assertTrue(args.json)

    def test_main_entrypoint_routes_lifecycle_subcommands(self) -> None:
        """Test that __main__.py routes start, stop, open to mission_cli."""
        for cmd in ("start", "stop", "open"):
            with patch("sys.argv", ["fireclaw", cmd, "--help"]):
                with patch("fireclaw_core.__main__._mission_main", return_value=0) as mock_mm:
                    code = main_entrypoint()
                    self.assertEqual(code, 0)
                    mock_mm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
