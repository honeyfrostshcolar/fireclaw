"""Tests for FireClaw top-level CLI help progressive disclosure (grouping & --all)."""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fireclaw_core.mission.mission_cli import (
    CORE_COMMAND_NAMES,
    ADVANCED_COMMAND_NAMES,
    build_parser,
    main as mission_main,
)


class TestCliHelpGrouping(unittest.TestCase):
    """Test progressive disclosure and grouping in top-level CLI help."""

    def test_core_and_advanced_command_sets(self) -> None:
        """Verify the partition of core vs advanced commands."""
        expected_core = {"setup", "start", "open", "status", "stop"}
        self.assertEqual(set(CORE_COMMAND_NAMES), expected_core)

        expected_advanced_subset = {
            "approval", "cancel", "corrections", "deploy", "doctor", "events",
            "fault-test", "hardware-safety", "lifecycle-check", "memory",
            "mission", "plan-mission", "recover", "replay", "robot-gateway",
            "robot-profile", "security-audit", "serve", "submit-subtask", "trace",
        }
        self.assertTrue(expected_advanced_subset.issubset(set(ADVANCED_COMMAND_NAMES)))

    def test_default_help_shows_grouped_core_commands(self) -> None:
        """Default `fireclaw --help` highlights Core Commands and collapses advanced tools."""
        parser = build_parser(show_all=False)
        help_text = parser.format_help()

        # Core Commands section must exist
        self.assertIn("Core Commands", help_text)
        for cmd in CORE_COMMAND_NAMES:
            self.assertIn(cmd, help_text)

        # Advanced Commands should be listed in a collapsed summary section, not individual full descriptions
        self.assertIn("Advanced", help_text)
        self.assertIn("--all", help_text)

        # Subcommand individual usage blocks for advanced commands shouldn't dominate default help
        # For example, detailed help phrases like "Start a persistent MissionGateway server" should only be in --all
        self.assertNotIn("Start a persistent MissionGateway server.", help_text)
        self.assertNotIn("Prepare and run real-robot hardware safety acceptance.", help_text)

    def test_help_all_shows_all_subcommands_with_descriptions(self) -> None:
        """`fireclaw --help --all` displays all subcommands with full descriptions."""
        parser = build_parser(show_all=True)
        help_text = parser.format_help()

        self.assertIn("Core Commands", help_text)
        self.assertIn("Advanced", help_text)

        # In full help mode, advanced command descriptions appear
        self.assertIn("Start a persistent MissionGateway server.", help_text)
        self.assertIn("Prepare and run real-robot hardware safety acceptance.", help_text)
        self.assertIn("Run fleet diagnostics through the Mission Gateway.", help_text)
        self.assertIn("Plan, apply, or inspect Plugin Runtime deployment.", help_text)

        for cmd in CORE_COMMAND_NAMES:
            self.assertIn(cmd, help_text)
        for cmd in ADVANCED_COMMAND_NAMES:
            self.assertIn(cmd, help_text)

    def test_cli_invocation_default_help_via_main(self) -> None:
        """Test invoking main() with --help produces grouped default help."""
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["fireclaw", "--help"]):
            with patch("sys.stdout", stdout_capture):
                with self.assertRaises(SystemExit) as cm:
                    mission_main()
                self.assertEqual(cm.exception.code, 0)

        output = stdout_capture.getvalue()
        self.assertIn("Core Commands", output)
        self.assertIn("setup", output)
        self.assertIn("start", output)
        self.assertIn("open", output)
        self.assertIn("status", output)
        self.assertIn("stop", output)
        self.assertIn("--all", output)

    def test_cli_invocation_help_all_via_main(self) -> None:
        """Test invoking main() with --help --all produces full help."""
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["fireclaw", "--help", "--all"]):
            with patch("sys.stdout", stdout_capture):
                with self.assertRaises(SystemExit) as cm:
                    mission_main()
                self.assertEqual(cm.exception.code, 0)

        output = stdout_capture.getvalue()
        self.assertIn("Core Commands", output)
        self.assertIn("Start a persistent MissionGateway server.", output)
        self.assertIn("doctor", output)
        self.assertIn("recover", output)

    def test_cli_invocation_all_flag_via_main(self) -> None:
        """Test invoking main() with --all alone displays full help."""
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["fireclaw", "--all"]):
            with patch("sys.stdout", stdout_capture):
                with self.assertRaises(SystemExit) as cm:
                    mission_main()
                self.assertEqual(cm.exception.code, 0)

        output = stdout_capture.getvalue()
        self.assertIn("Core Commands", output)
        self.assertIn("Start a persistent MissionGateway server.", output)

    def test_cli_invocation_help_subcommand(self) -> None:
        """Test `fireclaw help` and `fireclaw help --all` and `fireclaw help <cmd>`."""
        # fireclaw help
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["fireclaw", "help"]):
            with patch("sys.stdout", stdout_capture):
                code = mission_main()
                self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Core Commands", output)

        # fireclaw help --all
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["fireclaw", "help", "--all"]):
            with patch("sys.stdout", stdout_capture):
                code = mission_main()
                self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Start a persistent MissionGateway server.", output)

        # fireclaw help start
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["fireclaw", "help", "start"]):
            with patch("sys.stdout", stdout_capture):
                code = mission_main()
                self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("--foreground", output)

    def test_subcommand_direct_help_unaffected(self) -> None:
        """Subcommands like `fireclaw start --help` show their specific options."""
        for cmd in ("start", "stop", "setup", "open", "status", "doctor", "recover", "deploy"):
            stdout_capture = io.StringIO()
            with patch("sys.argv", ["fireclaw", cmd, "--help"]):
                with patch("sys.stdout", stdout_capture):
                    with self.assertRaises(SystemExit) as cm:
                        mission_main()
                    self.assertEqual(cm.exception.code, 0)
            output = stdout_capture.getvalue()
            self.assertIn(f"usage: fireclaw {cmd}", output)

    def test_subprocess_module_help(self) -> None:
        """Test `python3 -m fireclaw_core --help` in a clean subprocess."""
        proc = subprocess.run(
            [sys.executable, "-m", "fireclaw_core", "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Core Commands", proc.stdout)
        self.assertIn("setup", proc.stdout)
        self.assertIn("start", proc.stdout)
        self.assertIn("open", proc.stdout)
        self.assertIn("status", proc.stdout)
        self.assertIn("stop", proc.stdout)
        self.assertIn("--all", proc.stdout)

    def test_subprocess_module_help_all(self) -> None:
        """Test `python3 -m fireclaw_core --help --all` in a clean subprocess."""
        proc = subprocess.run(
            [sys.executable, "-m", "fireclaw_core", "--help", "--all"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Core Commands", proc.stdout)
        self.assertIn("Advanced", proc.stdout)
        self.assertIn("Start a persistent MissionGateway server.", proc.stdout)


if __name__ == "__main__":
    unittest.main()
