"""OpenClaw-style release distribution inspector and installed smoke validator."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import venv
import zipfile
from typing import Any, Mapping

from tools.release.simulation_bundle import inspect_simulation_bundle, BundleInspection

WHEEL_SIZE_BUDGET_BYTES = 4 * 1024 * 1024  # 4 MiB max for core wheel
WHEEL_UNPACKED_SIZE_BUDGET_BYTES = 16 * 1024 * 1024
MAX_WHEEL_MEMBERS = 10_000
MAX_WHEEL_METADATA_BYTES = 1024 * 1024

REQUIRED_WHEEL_FILES = [
    "fireclaw_core/__init__.py",
    "fireclaw_core/__main__.py",
    "fireclaw_core/mission/mission_gateway_client.py",
    "fireclaw_core/mission/plan_artifact.py",
    "fireclaw_core/web_console/index.html",
    "fireclaw_core/web_console/style.css",
    "fireclaw_core/web_console/app.js",
    "fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml",
    "fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json",
]

FORBIDDEN_WHEEL_PATTERNS = [
    "build/",
    "devel/",
    "logs/",
    "data/robots/",
    "openclaw/",
    ".git/",
    ".codegraph/",
    ".superpowers/",
    ".pyc",
    ".so",
    ".o",
    ".a",
    "credentials",
    "secret.token",
]


@dataclass(frozen=True)
class WheelInspection:
    valid: bool
    errors: list[str]
    sha256: str
    file_count: int
    compressed_bytes: int
    version: str
    members: list[str]


@dataclass(frozen=True)
class SmokeResult:
    success: bool
    command_outputs: dict[str, str]
    errors: list[str]


@dataclass(frozen=True)
class DistributionCheckReport:
    wheel: WheelInspection
    simulation_bundle: BundleInspection | None
    smoke: SmokeResult | None
    gate_errors: list[str]
    all_passed: bool


def _compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_wheel_path(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name or "//" in name:
        return False
    canonical = name[:-1] if name.endswith("/") else name
    parsed = PurePosixPath(canonical)
    return (
        bool(parsed.parts)
        and parsed.as_posix() == canonical
        and all(part not in {"", ".", ".."} for part in parsed.parts)
    )


def inspect_wheel(path: Path) -> WheelInspection:
    """Inspect and validate built wheel against distribution requirements and forbidden patterns."""
    errors: list[str] = []
    path = path.resolve()

    if not path.is_file() or not path.name.endswith(".whl"):
        return WheelInspection(
            valid=False,
            errors=[f"Invalid or missing wheel file: {path}"],
            sha256="",
            file_count=0,
            compressed_bytes=0,
            version="",
            members=[],
        )

    compressed_bytes = path.stat().st_size

    if compressed_bytes > WHEEL_SIZE_BUDGET_BYTES:
        return WheelInspection(
            valid=False,
            errors=[
                f"Wheel size {compressed_bytes} bytes exceeds budget {WHEEL_SIZE_BUDGET_BYTES} bytes"
            ],
            sha256="",
            file_count=0,
            compressed_bytes=compressed_bytes,
            version="",
            members=[],
        )
    wheel_sha256 = _compute_file_sha256(path)

    members: list[str] = []
    metadata_version = ""
    has_entry_points = False
    uncompressed_bytes = 0

    try:
        with zipfile.ZipFile(path, "r") as z:
            infos = z.infolist()
            if len(infos) > MAX_WHEEL_MEMBERS:
                errors.append(
                    f"Wheel contains {len(infos)} members, exceeding limit {MAX_WHEEL_MEMBERS}"
                )
            seen_names: set[str] = set()
            for info in infos[:MAX_WHEEL_MEMBERS]:
                name = info.filename
                members.append(name)
                uncompressed_bytes += info.file_size

                if name in seen_names:
                    errors.append(f"Duplicate wheel member name: {name}")
                seen_names.add(name)

                encoded_mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(encoded_mode)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    errors.append(f"Forbidden wheel member type for {name}: mode={oct(encoded_mode)}")
                if info.flag_bits & 0x1:
                    errors.append(f"Encrypted wheel member is forbidden: {name}")

                if not _is_safe_wheel_path(name):
                    errors.append(f"Unsafe archive member name: {name}")

                # Check forbidden patterns
                for forbidden in FORBIDDEN_WHEEL_PATTERNS:
                    if forbidden in name:
                        errors.append(f"Forbidden path pattern '{forbidden}' found in wheel: {name}")

                # Check entry_points
                if name.endswith(".dist-info/entry_points.txt"):
                    if info.file_size > MAX_WHEEL_METADATA_BYTES:
                        errors.append(f"Wheel entry_points.txt exceeds {MAX_WHEEL_METADATA_BYTES} bytes")
                    else:
                        ep_text = z.read(info).decode("utf-8")
                        for line in ep_text.splitlines():
                            if "=" not in line:
                                continue
                            command, target = line.split("=", 1)
                            if command.strip() == "fireclaw" and target.strip() == "fireclaw_core.__main__:main":
                                has_entry_points = True

                # Check METADATA
                if name.endswith(".dist-info/METADATA"):
                    if info.file_size > MAX_WHEEL_METADATA_BYTES:
                        errors.append(f"Wheel METADATA exceeds {MAX_WHEEL_METADATA_BYTES} bytes")
                    else:
                        meta_text = z.read(info).decode("utf-8")
                        match = re.search(r"^Version:\s*([^\s]+)", meta_text, re.MULTILINE)
                        if match:
                            metadata_version = match.group(1).strip()

            if uncompressed_bytes <= WHEEL_UNPACKED_SIZE_BUDGET_BYTES:
                corrupt_member = z.testzip()
                if corrupt_member is not None:
                    errors.append(f"Wheel member failed CRC verification: {corrupt_member}")
    except Exception as exc:
        return WheelInspection(
            valid=False,
            errors=[f"Failed to read wheel as zip archive: {exc}"],
            sha256=wheel_sha256,
            file_count=0,
            compressed_bytes=compressed_bytes,
            version="",
            members=[],
        )

    if uncompressed_bytes > WHEEL_UNPACKED_SIZE_BUDGET_BYTES:
        errors.append(
            f"Wheel unpacked size {uncompressed_bytes} bytes exceeds budget {WHEEL_UNPACKED_SIZE_BUDGET_BYTES} bytes"
        )

    # Verify required files
    member_set = set(members)
    for req in REQUIRED_WHEEL_FILES:
        if req not in member_set:
            errors.append(f"Missing required file in wheel: {req}")

    if not has_entry_points:
        errors.append("Wheel missing console_scripts entry point 'fireclaw = fireclaw_core.__main__:main'")

    # Verify version consistency with filename
    if metadata_version:
        expected_prefix = f"fireclaw-{metadata_version}-"
        if not path.name.startswith(expected_prefix):
            errors.append(
                f"Wheel filename '{path.name}' does not match METADATA version '{metadata_version}'"
            )
    else:
        errors.append("Wheel missing Version in .dist-info/METADATA")

    return WheelInspection(
        valid=len(errors) == 0,
        errors=errors,
        sha256=wheel_sha256,
        file_count=len(members),
        compressed_bytes=compressed_bytes,
        version=metadata_version,
        members=members,
    )


def load_simulation_catalog_from_wheel(
    wheel_path: Path,
    bundle_id: str,
) -> Mapping[str, Any]:
    """Read and validate the bundle catalog shipped in the wheel under test."""
    from fireclaw_core.resources import validate_simulation_bundle_catalog

    resource_name = f"fireclaw_core/resources/simulation_bundles/{bundle_id}.json"

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError(f"Duplicate JSON key in wheel catalog: {key}")
            parsed[key] = value
        return parsed

    try:
        with zipfile.ZipFile(wheel_path, "r") as archive:
            matches = [info for info in archive.infolist() if info.filename == resource_name]
            if len(matches) != 1:
                raise ValueError(
                    f"Wheel must contain exactly one {resource_name}; found {len(matches)}"
                )
            if matches[0].file_size > MAX_WHEEL_METADATA_BYTES:
                raise ValueError(
                    f"Wheel simulation catalog exceeds {MAX_WHEEL_METADATA_BYTES} bytes"
                )
            raw_catalog = archive.read(matches[0])
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"Could not read simulation catalog from wheel: {exc}") from exc

    try:
        catalog = json.loads(raw_catalog.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid simulation catalog JSON in wheel: {exc}") from exc
    return validate_simulation_bundle_catalog(catalog, expected_bundle_id=bundle_id)


def run_installed_smoke(
    wheel_path: Path,
    work_root: Path,
    *,
    simulation_bundle_path: Path | None = None,
) -> SmokeResult:
    """Perform isolated installation of wheel in clean venv and execute smoke verification."""
    errors: list[str] = []
    command_outputs: dict[str, str] = {}
    work_root = work_root.resolve()
    venv_dir = work_root / "smoke_venv"
    home_dir = work_root / "smoke_home"
    home_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create temporary venv with system_site_packages so test dependencies (numpy, PyYAML, httpx) are available offline
    try:
        venv.create(venv_dir, system_site_packages=True, clear=True)
    except Exception as exc:
        return SmokeResult(
            success=False,
            command_outputs={},
            errors=[f"Failed to create temporary venv: {exc}"],
        )

    python_bin = venv_dir / "bin" / "python"
    fireclaw_bin = venv_dir / "bin" / "fireclaw"

    # Environment isolation: strip PYTHONPATH, set FIRECLAW_HOME
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["FIRECLAW_HOME"] = str(home_dir)

    # 2. Install wheel with --no-deps
    install_cmd = [str(python_bin), "-m", "pip", "install", "--no-deps", str(wheel_path.resolve())]
    try:
        proc = subprocess.run(
            install_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(work_root),
            check=True,
        )
        command_outputs["pip_install"] = proc.stdout
    except subprocess.CalledProcessError as exc:
        return SmokeResult(
            success=False,
            command_outputs={"pip_install_err": exc.stderr},
            errors=[f"pip install --no-deps failed: {exc.stderr}"],
        )

    # 3. Run CLI help smoke commands
    smoke_commands = [
        ("fireclaw_help", [str(fireclaw_bin), "--help"]),
        ("fireclaw_setup_help", [str(fireclaw_bin), "setup", "--help"]),
        ("fireclaw_start_help", [str(fireclaw_bin), "start", "--help"]),
        ("fireclaw_open_help", [str(fireclaw_bin), "open", "--help"]),
        ("fireclaw_status_help", [str(fireclaw_bin), "status", "--help"]),
        ("fireclaw_stop_help", [str(fireclaw_bin), "stop", "--help"]),
        (
            "package_resources_smoke",
            [
                str(python_bin),
                "-c",
                (
                    "from fireclaw_core.resources import load_setup_template, load_simulation_bundle_catalog;\n"
                    "from fireclaw_core.web_console import read_web_console_asset;\n"
                    "tmpl = load_setup_template('gazebo_turtlebot3'); assert len(tmpl) > 100;\n"
                    "cat = load_simulation_bundle_catalog('turtlebot3-burger-v1'); assert cat['bundle_id'] == 'turtlebot3-burger-v1';\n"
                    "html, mime = read_web_console_asset('index.html'); assert len(html) > 100;\n"
                    "print('PACKAGE_RESOURCES_OK')\n"
                ),
            ],
        ),
    ]

    for name, cmd in smoke_commands:
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(work_root),
                timeout=15,
                check=True,
            )
            command_outputs[name] = res.stdout
        except subprocess.CalledProcessError as exc:
            errors.append(f"Command '{' '.join(cmd)}' failed (code {exc.returncode}): {exc.stderr}")
        except Exception as exc:
            errors.append(f"Command '{' '.join(cmd)}' failed with error: {exc}")

    # 4. Exercise the installed first-use boundary without starting ROS,
    # Gazebo, a browser, or any real-robot action. The real companion archive
    # is fully verified/materialized; only Catkin and daemon effects are
    # replaced with deterministic test doubles at their typed boundaries.
    if simulation_bundle_path is None:
        errors.append("Installed first-use E2E requires a companion simulation bundle")
    else:
        env["FIRECLAW_SIMULATION_BUNDLE_E2E"] = str(
            simulation_bundle_path.resolve()
        )
        first_use_program = r'''
import json
import os
from pathlib import Path

from fireclaw_core.deployment.command import DeploymentCommandResult
from fireclaw_core.infra.simulation_runtime import prepare_simulation_runtime
from fireclaw_core.infra.user_setup import (
    complete_simulation_first_use,
    setup_fireclaw,
)

runtime_root = Path(os.environ["FIRECLAW_HOME"])
bundle_path = Path(os.environ["FIRECLAW_SIMULATION_BUNDLE_E2E"])
ros_setup = runtime_root / "test-boundary" / "ros-noetic-setup.bash"
ros_setup.parent.mkdir(parents=True, exist_ok=True)
ros_setup.write_text("export ROS_DISTRO=noetic\n", encoding="utf-8")

class Runner:
    def __init__(self):
        self.calls = []

    def run(self, argv, *, timeout_seconds, max_output_bytes, env=None, cwd=None):
        command = tuple(str(value) for value in argv)
        self.calls.append(command)
        if command[:2] == ("/bin/bash", "-c"):
            return DeploymentCommandResult(
                argv=command,
                exit_code=0,
                output="ROS_DISTRO=noetic\x00PATH=/usr/bin\x00",
                duration_seconds=0.01,
            )
        install_setup = Path(cwd) / "install" / "setup.bash"
        install_setup.parent.mkdir(parents=True, exist_ok=True)
        install_setup.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return DeploymentCommandResult(
            argv=command,
            exit_code=0,
            output="installed first-use boundary build\n",
            duration_seconds=0.01,
        )

class Plan:
    fingerprint = "installed-first-use-plan"

    def to_dict(self):
        return {"status": "ready", "fingerprint": self.fingerprint}

def build_plan(_profile_path):
    return Plan()

def apply_plan(plan):
    return {"status": "installed", "fingerprint": plan.fingerprint, "reused": False}

runner = Runner()

def prepare(**kwargs):
    return prepare_simulation_runtime(
        **kwargs,
        workspace_runner=runner,
        catkin_executable="/test-boundary/catkin_make",
        ros_setup=ros_setup,
    )

def configure():
    return setup_fireclaw(
        mode="simulation",
        runtime_root=runtime_root,
        source_root=None,
        simulation_bundle_path=bundle_path,
        deploy=True,
        plan_builder=build_plan,
        deployment_applier=apply_plan,
        simulation_preparer=prepare,
    )

first = configure()
second = configure()
assert first["status"] == "ready_to_start"
assert second["status"] == "ready_to_start"
assert first["profile_path"] == second["profile_path"]
assert first["profile_created"] is True
assert second["profile_created"] is False
assert second["simulation_runtime"]["bundle"]["reused"] is True
assert second["simulation_runtime"]["workspace"]["reused"] is True
assert len(runner.calls) == 2

profile_text = Path(first["profile_path"]).read_text(encoding="utf-8")
assert str(runtime_root.resolve()) in profile_text
assert "/devel/setup.bash" not in profile_text
assert "/current/" not in profile_text

class Manager:
    def __init__(self):
        self.starts = 0
        self.opens = 0

    def start_daemon(self, **kwargs):
        self.starts += 1
        return {
            "status": "running" if self.starts == 1 else "already_running",
            "health_verified": True,
            "daemon": {"pid": 4242},
        }

    def open_console(self, **kwargs):
        self.opens += 1
        return {
            "status": "ready",
            "url": "http://127.0.0.1:8766",
            "browser_opened": False,
        }

manager = Manager()
ready = complete_simulation_first_use(
    first,
    runtime_root=runtime_root,
    manager=manager,
    timeout=1.0,
    browser=False,
)
resumed = complete_simulation_first_use(
    second,
    runtime_root=runtime_root,
    manager=manager,
    timeout=1.0,
    browser=False,
)
assert ready["status"] == "ready"
assert resumed["status"] == "ready"
assert ready["simulation_runtime_started"] is True
assert resumed["lifecycle"]["start"]["status"] == "already_running"
assert manager.starts == 2 and manager.opens == 2
assert ready["real_robot_action_started"] is False

print(json.dumps({
    "status": "ok",
    "profile_path": first["profile_path"],
    "bundle_reused": second["simulation_runtime"]["bundle"]["reused"],
    "workspace_reused": second["simulation_runtime"]["workspace"]["reused"],
    "repeat_start_status": resumed["lifecycle"]["start"]["status"],
}, sort_keys=True))
'''
        try:
            result = subprocess.run(
                [str(python_bin), "-c", first_use_program],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(work_root),
                timeout=90,
                check=True,
            )
            command_outputs["installed_first_use_e2e"] = result.stdout
        except subprocess.CalledProcessError as exc:
            errors.append(
                "Installed first-use E2E failed "
                f"(code {exc.returncode}): {exc.stderr}"
            )
        except Exception as exc:
            errors.append(f"Installed first-use E2E failed with error: {exc}")

    return SmokeResult(
        success=len(errors) == 0,
        command_outputs=command_outputs,
        errors=errors,
    )


def run_full_distribution_check(
    wheel_path: Path,
    simulation_bundle_path: Path | None = None,
    catalog: Mapping[str, Any] | None = None,
    run_smoke: bool = True,
    bundle_id: str = "turtlebot3-burger-v1",
) -> DistributionCheckReport:
    """Run comprehensive wheel inspection, simulation bundle check, and isolated smoke."""
    wheel_inspection = inspect_wheel(wheel_path)
    gate_errors: list[str] = []

    bundle_inspection: BundleInspection | None = None
    if simulation_bundle_path is None:
        gate_errors.append("Simulation bundle check was omitted; a full release verdict requires it")
    else:
        try:
            wheel_catalog = load_simulation_catalog_from_wheel(wheel_path, bundle_id)
            if catalog is not None:
                from fireclaw_core.resources import validate_simulation_bundle_catalog

                validate_simulation_bundle_catalog(catalog, expected_bundle_id=bundle_id)
                if json.dumps(catalog, sort_keys=True, separators=(",", ":")) != json.dumps(
                    wheel_catalog,
                    sort_keys=True,
                    separators=(",", ":"),
                ):
                    raise ValueError("Caller-provided catalog does not match the catalog shipped in the wheel")
            bundle_inspection = inspect_simulation_bundle(simulation_bundle_path, wheel_catalog)
        except (OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            bundle_inspection = BundleInspection(
                valid=False,
                errors=[f"Simulation bundle gate could not load its authoritative catalog: {exc}"],
                sha256="",
                file_count=0,
                compressed_bytes=0,
                unpacked_bytes=0,
                manifest={},
            )

    smoke_result = None
    if not run_smoke:
        gate_errors.append("Isolated installed smoke was skipped; a full release verdict requires it")
    elif not wheel_inspection.valid:
        gate_errors.append("Isolated installed smoke was not run because wheel inspection failed")
    else:
        with tempfile.TemporaryDirectory(prefix="fireclaw-release-smoke-") as tmp_smoke_root:
            smoke_result = run_installed_smoke(
                wheel_path,
                Path(tmp_smoke_root),
                simulation_bundle_path=simulation_bundle_path,
            )

    all_passed = (
        not gate_errors
        and wheel_inspection.valid
        and bundle_inspection is not None
        and bundle_inspection.valid
        and smoke_result is not None
        and smoke_result.success
    )

    return DistributionCheckReport(
        wheel=wheel_inspection,
        simulation_bundle=bundle_inspection,
        smoke=smoke_result,
        gate_errors=gate_errors,
        all_passed=all_passed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="FireClaw distribution release check CLI.")
    parser.add_argument("--repo-root", default=".", help="Repository root used to locate release tooling source")
    parser.add_argument("--wheel", required=True, help="Path to built core wheel (.whl)")
    parser.add_argument("--simulation-bundle", required=True, help="Path to simulation bundle (.tar.gz)")
    parser.add_argument("--bundle-id", default="turtlebot3-burger-v1", help="Bundle ID for simulation catalog")
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip installed smoke for diagnostics; the release verdict will fail closed",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    source_root = repo_root / "src"
    if source_root.is_dir() and str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    report = run_full_distribution_check(
        wheel_path=Path(args.wheel),
        simulation_bundle_path=Path(args.simulation_bundle),
        catalog=None,
        run_smoke=not args.skip_smoke,
        bundle_id=args.bundle_id,
    )

    print("==================================================================")
    print("FireClaw Release Distribution Check Report")
    print("==================================================================")
    print(f"Wheel:             {args.wheel}")
    print(f"  Valid:           {'✅ PASS' if report.wheel.valid else '❌ FAIL'}")
    print(f"  Version:         {report.wheel.version}")
    print(f"  SHA-256:         {report.wheel.sha256}")
    print(f"  Files:           {report.wheel.file_count}")
    print(f"  Size:            {report.wheel.compressed_bytes} bytes ({report.wheel.compressed_bytes / 1024:.1f} KiB)")
    if report.wheel.errors:
        print("  Errors:")
        for err in report.wheel.errors:
            print(f"    - {err}")

    if report.simulation_bundle:
        print("------------------------------------------------------------------")
        print(f"Simulation Bundle: {args.simulation_bundle}")
        print(f"  Valid:           {'✅ PASS' if report.simulation_bundle.valid else '❌ FAIL'}")
        print(f"  SHA-256:         {report.simulation_bundle.sha256}")
        print(f"  Files:           {report.simulation_bundle.file_count}")
        print(
            f"  Compressed:      {report.simulation_bundle.compressed_bytes} bytes ({report.simulation_bundle.compressed_bytes / (1024*1024):.2f} MiB)"
        )
        print(
            f"  Unpacked:        {report.simulation_bundle.unpacked_bytes} bytes ({report.simulation_bundle.unpacked_bytes / (1024*1024):.2f} MiB)"
        )
        if report.simulation_bundle.errors:
            print("  Errors:")
            for err in report.simulation_bundle.errors:
                print(f"    - {err}")

    if report.smoke:
        print("------------------------------------------------------------------")
        print(f"Isolated Smoke:    {'✅ PASS' if report.smoke.success else '❌ FAIL'}")
        if report.smoke.errors:
            print("  Errors:")
            for err in report.smoke.errors:
                print(f"    - {err}")

    if report.gate_errors:
        print("------------------------------------------------------------------")
        print("Release Gate Errors:")
        for err in report.gate_errors:
            print(f"  - {err}")

    print("==================================================================")
    print(f"Verdict:           {'✅ ALL PASSED' if report.all_passed else '❌ FAILED'}")
    print("==================================================================")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
