import json
from pathlib import Path
import subprocess
import sys

from fireclaw_core.security.audit import run_security_audit


def _write_config(path: Path, content: str) -> None:
    path.write_text(content.lstrip(), encoding="utf-8")
    path.chmod(0o600)


def test_security_audit_reports_remote_gateway_auth_and_tls_gaps(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fireclaw.toml"
    _write_config(
        config_path,
        """
[runtime]
root_dir = "runtime"

[server]
host = "0.0.0.0"

[robot_gateway]
host = "192.0.2.10"

[network]
allowed_hosts = ["mission.example", "robot.example"]

[deployment]
mode = "real"
""",
    )

    report = run_security_audit(
        config_path=config_path,
        environ={},
    )
    check_ids = {finding.check_id for finding in report.findings}

    assert report.summary.critical == 4
    assert "gateway.mission.remote_without_auth" in check_ids
    assert "gateway.mission.remote_without_tls" in check_ids
    assert "gateway.robot.remote_without_auth" in check_ids
    assert "gateway.robot.remote_without_tls" in check_ids


def test_security_audit_accepts_loopback_and_validates_plugin_provenance(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fireclaw.toml"
    _write_config(
        config_path,
        """
[runtime]
root_dir = "runtime"

[server]
host = "127.0.0.1"

[robot_gateway]
host = "127.0.0.1"

[deployment]
mode = "real"
""",
    )
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    descriptor = plugin_dir / "inspect.plugin.json"
    descriptor.write_text(
        json.dumps(
            {
                "plugin_id": "inspect",
                "capabilities": ["inspect"],
                "preconditions": ["ready"],
                "risk_level": "low",
            }
        ),
        encoding="utf-8",
    )
    descriptor.chmod(0o600)

    report = run_security_audit(
        config_path=config_path,
        plugin_dirs=(plugin_dir,),
        environ={},
    )
    check_ids = {finding.check_id for finding in report.findings}

    assert report.summary.critical == 0
    assert "plugin.provenance_untracked" in check_ids


def test_security_audit_rejects_executable_artifact_in_descriptor_directory(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fireclaw.toml"
    _write_config(
        config_path,
        """
[runtime]
root_dir = "runtime"
""",
    )
    plugin_dir = tmp_path / "plugins"
    nested = plugin_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "plugin.py").write_text(
        "raise RuntimeError('must never be imported')\n",
        encoding="utf-8",
    )

    report = run_security_audit(
        config_path=config_path,
        plugin_dirs=(plugin_dir,),
        environ={},
        deep=True,
    )

    finding = next(
        item
        for item in report.findings
        if item.check_id == "plugin.executable_artifact_unadmitted"
    )
    assert finding.severity == "critical"


def test_security_audit_rejects_group_writable_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fireclaw.toml"
    _write_config(
        config_path,
        """
[runtime]
root_dir = "runtime"
""",
    )
    config_path.chmod(0o620)

    report = run_security_audit(config_path=config_path, environ={})

    finding = next(
        item
        for item in report.findings
        if item.check_id == "filesystem.config_writable_by_others"
    )
    assert finding.severity == "critical"


def test_security_audit_is_available_from_main_cli(tmp_path: Path) -> None:
    config_path = tmp_path / "fireclaw.toml"
    _write_config(
        config_path,
        """
[runtime]
root_dir = "runtime"

[server]
host = "127.0.0.1"
""",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "security-audit",
            "--config",
            str(config_path),
            "--deep",
            "--fail-on",
            "never",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    report = json.loads(completed.stdout)
    assert report["summary"]["critical"] == 0
    assert report["runtime_root"] == str(tmp_path / "runtime")
    assert report["deep"] is True
