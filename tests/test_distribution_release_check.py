"""Tests for release distribution checker and installed smoke verification."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import venv
import zipfile

from fireclaw_core.resources import load_simulation_bundle_catalog


def _write_mock_wheel(directory: Path) -> Path:
    wheel_path = directory / "fireclaw-0.1.0-py3-none-any.whl"
    catalog = load_simulation_bundle_catalog("turtlebot3-burger-v1")
    with zipfile.ZipFile(wheel_path, "w") as z:
        z.writestr("fireclaw_core/__init__.py", "")
        z.writestr("fireclaw_core/__main__.py", "def main(): pass")
        z.writestr("fireclaw_core/web_console/index.html", "<html></html>")
        z.writestr("fireclaw_core/web_console/style.css", "body {}")
        z.writestr("fireclaw_core/web_console/app.js", "console.log('ok')")
        z.writestr("fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml", "id='test'")
        z.writestr(
            "fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json",
            json.dumps(catalog),
        )
        z.writestr(
            "fireclaw-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: fireclaw\nVersion: 0.1.0\n",
        )
        z.writestr(
            "fireclaw-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nfireclaw = fireclaw_core.__main__:main\n",
        )
    return wheel_path


class TestDistributionReleaseCheck(unittest.TestCase):
    def test_inspect_wheel_validates_required_and_forbidden_paths(self):
        from tools.release.distribution_check import inspect_wheel

        with tempfile.TemporaryDirectory() as tmp_dir:
            wheel_path = _write_mock_wheel(Path(tmp_dir))

            inspection = inspect_wheel(wheel_path)
            self.assertTrue(inspection.valid, f"Inspection failed with: {inspection.errors}")
            self.assertEqual(len(inspection.errors), 0)
            self.assertEqual(inspection.version, "0.1.0")

    def test_inspect_wheel_rejects_missing_required_files(self):
        from tools.release.distribution_check import inspect_wheel

        with tempfile.TemporaryDirectory() as tmp_dir:
            wheel_path = Path(tmp_dir) / "fireclaw-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel_path, "w") as z:
                z.writestr("fireclaw_core/__init__.py", "")
                # Missing web_console assets and setup templates!

            inspection = inspect_wheel(wheel_path)
            self.assertFalse(inspection.valid)
            self.assertTrue(any("missing" in err.lower() for err in inspection.errors))

    def test_inspect_wheel_rejects_forbidden_files(self):
        from tools.release.distribution_check import inspect_wheel

        with tempfile.TemporaryDirectory() as tmp_dir:
            wheel_path = Path(tmp_dir) / "fireclaw-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel_path, "w") as z:
                z.writestr("fireclaw_core/__init__.py", "")
                z.writestr("fireclaw_core/ros_ws/build/test.o", "binary")
                z.writestr("openclaw/secret.token", "secret")

            inspection = inspect_wheel(wheel_path)
            self.assertFalse(inspection.valid)
            self.assertTrue(any("forbidden" in err.lower() for err in inspection.errors))

    def test_full_check_fails_closed_when_bundle_or_smoke_is_omitted(self):
        from tools.release.distribution_check import run_full_distribution_check

        with tempfile.TemporaryDirectory() as tmp_dir:
            wheel_path = _write_mock_wheel(Path(tmp_dir))
            report = run_full_distribution_check(
                wheel_path,
                simulation_bundle_path=None,
                run_smoke=False,
            )

        self.assertFalse(report.all_passed)
        self.assertTrue(any("simulation bundle" in error.lower() for error in report.gate_errors))
        self.assertTrue(any("smoke" in error.lower() for error in report.gate_errors))

    def test_full_check_does_not_skip_bundle_when_catalog_argument_is_omitted(self):
        from tools.release.distribution_check import run_full_distribution_check

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            wheel_path = _write_mock_wheel(root)
            missing_bundle = root / "fireclaw-sim-turtlebot3-burger-v1.tar.gz"
            report = run_full_distribution_check(
                wheel_path,
                simulation_bundle_path=missing_bundle,
                catalog=None,
                run_smoke=False,
            )

        self.assertFalse(report.all_passed)
        self.assertIsNotNone(report.simulation_bundle)
        self.assertFalse(report.simulation_bundle.valid)

    def test_distribution_cli_bootstraps_repo_source_in_clean_venv(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            venv_dir = root / "venv"
            venv.create(venv_dir, with_pip=False)
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [
                    str(venv_dir / "bin" / "python"),
                    "-m",
                    "tools.release.distribution_check",
                    "--repo-root",
                    str(repo_root),
                    "--wheel",
                    str(root / "missing.whl"),
                    "--simulation-bundle",
                    str(root / "missing.tar.gz"),
                    "--skip-smoke",
                ],
                cwd=repo_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertIn("Verdict:", result.stdout)
