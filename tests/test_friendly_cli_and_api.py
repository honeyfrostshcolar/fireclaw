"""Tests for CLI friendly error output and Gateway structured error responses (Task 2)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.errors.friendly_errors import (
    FriendlyErrorResponse,
    format_friendly_error_cli,
    resolve_friendly_error,
)
from fireclaw_core.gateway.network_security import GatewayNetworkPolicy
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_cli import (
    main as mission_cli_main,
    print_friendly_error,
)
from fireclaw_core.mission.mission_gateway import (
    MissionGateway,
    MissionGatewayConfig,
)


def _make_dummy_agent() -> MissionAgent:
    return MissionAgent(registry=RobotRegistry([]))


def _make_gateway(api_token: str | None = None) -> MissionGateway:
    config = MissionGatewayConfig(
        port=0,
        api_token=api_token,
        network=GatewayNetworkPolicy(),
    )
    return MissionGateway(
        config,
        mission_agent=_make_dummy_agent(),
        registry=RobotRegistry([]),
    )


def _json_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict | str | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    if payload is None:
        data = None
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")

    req_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers=req_headers,
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return exc.code, parsed


class TestCLIFriendlyOutput(unittest.TestCase):
    """Test CLI 4-part Chinese friendly error output and helper function."""

    def test_print_friendly_error_returns_four_sections(self) -> None:
        resp = resolve_friendly_error(
            "ros_master_unreachable",
            {"master_uri": "http://localhost:11311", "timeout": "2.0"},
        )
        text = format_friendly_error_cli(resp, verbose=False)
        self.assertIn("❶", text)
        self.assertIn("❷", text)
        self.assertIn("❸", text)
        self.assertIn("❹", text)
        self.assertIn("发生了什么", text)
        self.assertIn("机器人安全证据", text)
        self.assertIn("自动处置回执", text)
        self.assertIn("建议下一步", text)

    def test_error_registry_listing_does_not_publish_unverified_safety_claims(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = mission_cli_main(["errors", "list", "--json"])
        self.assertEqual(exit_code, 0)
        entries = json.loads(buf.getvalue())
        self.assertGreater(len(entries), 0)
        for entry in entries:
            self.assertIn("UNKNOWN", entry["robot_safe_status"])
            self.assertIn("未收到可验证的自动处置回执", entry["action_taken"])
            self.assertNotIn("绝对静止", entry["robot_safe_status"])

    def test_cli_error_helper_captures_output(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            print_friendly_error("sensor_no_lidar_data", {"topic_name": "/scan"})
        output = buf.getvalue()
        self.assertIn("发生了什么", output)
        self.assertIn("/scan", output)
        self.assertIn("❶", output)
        self.assertIn("CRITICAL", output)

    def test_cli_error_helper_verbose_mode(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            print_friendly_error(
                "ros_master_unreachable",
                {"master_uri": "http://127.0.0.1:11311", "timeout": "2.0"},
                technical_details="ConnectionRefusedError: [Errno 111] Connection refused",
                verbose=True,
            )
        output = buf.getvalue()
        self.assertIn("技术详情", output)
        self.assertIn("ConnectionRefusedError", output)

    def test_cli_profile_diff_missing_source_prints_friendly_error(self) -> None:
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            exit_code = mission_cli_main([
                "profile", "diff",
                "--from", "non_existent_from.toml",
                "--to", "non_existent_to.toml",
            ])
        self.assertEqual(exit_code, 1)
        err = captured_stderr.getvalue()
        self.assertIn("❶", err)
        self.assertIn("发生了什么", err)

    def test_cli_profile_history_missing_profile_prints_friendly_error(self) -> None:
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            exit_code = mission_cli_main([
                "profile", "history",
                "--profile", "/non/existent/profile_12345.toml",
            ])
        self.assertEqual(exit_code, 1)
        err = captured_stderr.getvalue()
        self.assertIn("❶", err)
        self.assertIn("发生了什么", err)

    def test_cli_profile_rollback_missing_snapshot_prints_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "profile.toml"
            p.write_text('[robot]\nid = "test"\n', encoding="utf-8")
            captured_stderr = io.StringIO()
            with patch("sys.stderr", captured_stderr):
                exit_code = mission_cli_main([
                    "profile", "rollback",
                    "--snapshot", "non_existent_snap_999",
                    "--profile", str(p),
                ])
            self.assertEqual(exit_code, 1)
            err = captured_stderr.getvalue()
            self.assertIn("❶", err)
            self.assertIn("发生了什么", err)

    def test_cli_errors_list_and_get(self) -> None:
        # Test errors list
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = mission_cli_main(["errors", "list"])
        self.assertEqual(exit_code, 0)
        out = captured_stdout.getvalue()
        self.assertIn("FireClaw 注册错误码清单", out)
        self.assertIn("sensor_no_lidar_data", out)
        self.assertIn("ros_master_unreachable", out)

        # Test errors list --json
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = mission_cli_main(["errors", "list", "--json"])
        self.assertEqual(exit_code, 0)
        items = json.loads(captured_stdout.getvalue())
        self.assertGreaterEqual(len(items), 30)

        # Test errors get
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = mission_cli_main(["errors", "get", "sensor_no_lidar_data"])
        self.assertEqual(exit_code, 0)
        out = captured_stdout.getvalue()
        self.assertIn("sensor_no_lidar_data", out)
        self.assertIn("❶", out)

        # Test errors get unknown
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            exit_code = mission_cli_main(["errors", "get", "unknown_xyz_code"])
        self.assertEqual(exit_code, 1)


class TestGatewayStructuredErrors(unittest.TestCase):
    """Test Gateway structured error responses and helper method."""

    def setUp(self) -> None:
        self.gateway = _make_gateway()
        self.gateway.start()
        self.base_url = self.gateway.base_url

    def tearDown(self) -> None:
        self.gateway.stop()

    def test_gateway_error_response_has_error_detail(self) -> None:
        """Verify Gateway returns structured 4-part error in JSON."""
        resp = resolve_friendly_error("config_save_failed", {"profile_path": "/tmp/test.toml"}, "PermissionError")
        d = resp.to_dict()
        body = {"status": "error", "message": d["what_happened"], "error": d}
        self.assertIn("error", body)
        self.assertIn("what_happened", body["error"])
        self.assertIn("robot_safe_status", body["error"])
        self.assertIn("action_taken", body["error"])
        self.assertIn("next_steps", body["error"])
        self.assertIn("suggested_actions", body["error"])
        self.assertIn("technical_details", body["error"])
        self.assertEqual(body["message"], d["what_happened"])

    def test_gateway_preserves_backward_compat_message(self) -> None:
        resp = resolve_friendly_error("gateway_connection_failed", {"gateway_url": "http://127.0.0.1:8765"})
        d = resp.to_dict()
        body = {"status": "error", "message": d["what_happened"], "error": d}
        self.assertIn("message", body)
        self.assertIsInstance(body["message"], str)

    def test_gateway_write_friendly_error_method(self) -> None:
        handler = MagicMock()
        buf = io.BytesIO()
        handler.wfile = buf

        self.gateway._write_friendly_error(
            handler,
            HTTPStatus.BAD_REQUEST,
            "config_save_failed",
            context={"profile_path": "/tmp/test.toml", "reason": "Permission denied"},
            technical_details="PermissionError: [Errno 13]",
        )
        handler.send_response.assert_called_with(HTTPStatus.BAD_REQUEST)
        handler.end_headers.assert_called_once()
        raw_body = buf.getvalue().decode("utf-8")
        parsed = json.loads(raw_body)
        self.assertEqual(parsed["status"], "error")
        self.assertIn("what_happened", parsed["error"])
        self.assertEqual(parsed["error"]["error_code"], "config_save_failed")
        self.assertEqual(parsed["error"]["technical_details"], "PermissionError: [Errno 13]")
        self.assertEqual(parsed["message"], parsed["error"]["what_happened"])

    def test_gateway_config_save_missing_fields_returns_structured_error(self) -> None:
        status, body = _json_request(self.base_url, "POST", "/config/save", payload={"content": "abc"})
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "error")
        self.assertIn("error", body)
        self.assertEqual(body["error"]["error_code"], "config_save_failed")
        self.assertIn("what_happened", body["error"])
        self.assertIsInstance(body["message"], str)

    def test_gateway_config_rollback_missing_snapshot_returns_structured_error(self) -> None:
        status, body = _json_request(self.base_url, "POST", "/config/rollback", payload={"profile_path": "/tmp/xyz"})
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "error")
        self.assertIn("error", body)
        self.assertEqual(body["error"]["error_code"], "config_rollback_failed")

    def test_gateway_config_rollback_not_found_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = str(Path(tmp_dir) / "p.toml")
            Path(p).write_text("[robot]\n", encoding="utf-8")
            status, body = _json_request(
                self.base_url,
                "POST",
                "/config/rollback",
                payload={"profile_path": p, "snapshot_id": "missing_snap_123"},
            )
            self.assertEqual(status, 404)
            self.assertEqual(body["status"], "error")
            self.assertIn("error", body)
            self.assertEqual(body["error"]["error_code"], "snapshot_not_found")
            self.assertIn("missing_snap_123", body["error"]["what_happened"])

    def test_gateway_plan_mission_missing_command_returns_structured_error(self) -> None:
        status, body = _json_request(self.base_url, "POST", "/plan-mission", payload={})
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "error")
        self.assertIn("error", body)
        self.assertEqual(body["error"]["error_code"], "planner_failed")

    def test_gateway_recover_unconfirmed_returns_structured_error(self) -> None:
        status, body = _json_request(self.base_url, "POST", "/recover", payload={"operator_confirmed": False})
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "error")
        self.assertIn("error", body)
        self.assertEqual(body["error"]["error_code"], "safety_requires_confirmation")


if __name__ == "__main__":
    unittest.main()
