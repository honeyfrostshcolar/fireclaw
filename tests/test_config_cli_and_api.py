"""Tests for CLI subcommands ('fireclaw profile') and Gateway REST API endpoints ('/config/*') (Task 3).

Covers:
1. CLI subcommands under 'fireclaw profile':
   - 'fireclaw profile list-templates [--json]'
   - 'fireclaw profile discover [--master-uri URI] [--timeout SEC] [--json]'
   - 'fireclaw profile diff --from FILE_OR_TEMPLATE --to FILE [--json]'
   - 'fireclaw profile history [--profile PATH] [--json]'
   - 'fireclaw profile rollback --snapshot SNAPSHOT_ID [--profile PATH] [--json]'
   - Subcommand registration in build_parser and __main__.py
   - Backward compatibility for existing 'robot-profile' subcommand
2. Gateway REST API endpoints under '/config/*':
   - GET /config/templates
   - POST /config/discover
   - GET /config/schema
   - POST /config/diff
   - POST /config/save
   - GET /config/history
   - POST /config/rollback
   - POST /config/test-field
   - Gateway error handling & authorization scopes
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.config import (
    RobotTemplate,
    TemplateManager,
    RosGraphDiscoverer,
    DiscoveryReport,
    ConfigDiffEngine,
    ProfileSnapshotManager,
    SecretManager,
)
from fireclaw_core.gateway.method_scopes import (
    ADMIN_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    authorize_method,
    resolve_required_scope,
)
from fireclaw_core.gateway.network_security import GatewayNetworkPolicy
from fireclaw_core.infra import tomllib_compat as tomllib
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_cli import build_parser, main as mission_cli_main
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _make_dummy_agent() -> MissionAgent:
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1",
            base_url="http://127.0.0.1:8765",
            capabilities=("victim_search",),
        )
    ])
    return MissionAgent(registry=registry)


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


# ---------------------------------------------------------------------------
# CLI Tests: fireclaw profile <subcommand>
# ---------------------------------------------------------------------------


class TestProfileCLI(unittest.TestCase):
    """Test CLI subcommands under 'fireclaw profile'."""

    def test_parser_contains_profile_subcommand(self) -> None:
        parser = build_parser(show_all=True)
        self.assertIn("profile", parser._subparsers_action.choices)
        self.assertIn("robot-profile", parser._subparsers_action.choices)

    def test_list_templates_json(self) -> None:
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = mission_cli_main(["profile", "list-templates", "--json"])
        self.assertEqual(exit_code, 0)
        output = json.loads(captured_stdout.getvalue())
        self.assertIsInstance(output, list)
        self.assertEqual(len(output), 5)
        template_ids = [t["template_id"] for t in output]
        self.assertIn("gazebo_turtlebot3_burger", template_ids)
        self.assertIn("real_firefighting_tracked", template_ids)

    def test_list_templates_human_readable(self) -> None:
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = mission_cli_main(["profile", "list-templates"])
        self.assertEqual(exit_code, 0)
        text = captured_stdout.getvalue()
        self.assertIn("gazebo_turtlebot3_burger", text)
        self.assertIn("real_firefighting_tracked", text)
        self.assertIn("TurtleBot3 Burger", text)

    def test_discover_json_offline(self) -> None:
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = mission_cli_main([
                "profile", "discover",
                "--master-uri", "http://127.0.0.1:11999",
                "--timeout", "0.2",
                "--json",
            ])
        self.assertEqual(exit_code, 0)
        output = json.loads(captured_stdout.getvalue())
        self.assertFalse(output["discovered"])
        self.assertEqual(output["master_uri"], "http://127.0.0.1:11999")

    def test_discover_json_mocked_online(self) -> None:
        mock_report = DiscoveryReport(
            discovered=True,
            master_uri="http://127.0.0.1:11311",
            active_topics=[("/scan", "sensor_msgs/LaserScan"), ("/odom", "nav_msgs/Odometry"), ("/thermal/image", "sensor_msgs/Image")],
            active_services=["/move_base/make_plan"],
            matched_topics={
                "laser_scan_topic": "/scan",
                "odometry_topic": "/odom",
                "thermal_camera_topic": "/thermal/image",
            },
            matched_actions={"navigation_action": "/move_base"},
        )
        with patch.object(RosGraphDiscoverer, "probe_ros_master", return_value=mock_report):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main([
                    "profile", "discover",
                    "--master-uri", "http://127.0.0.1:11311",
                    "--json",
                ])
            self.assertEqual(exit_code, 0)
            output = json.loads(captured_stdout.getvalue())
            self.assertTrue(output["discovered"])
            self.assertEqual(output["matched_topics"]["laser_scan_topic"], "/scan")

    def test_discover_human_readable(self) -> None:
        mock_report = DiscoveryReport(
            discovered=True,
            master_uri="http://127.0.0.1:11311",
            active_topics=[("/scan", "sensor_msgs/LaserScan"), ("/odom", "nav_msgs/Odometry")],
            active_services=[],
            matched_topics={"laser_scan_topic": "/scan"},
            matched_actions={},
        )
        with patch.object(RosGraphDiscoverer, "probe_ros_master", return_value=mock_report):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main(["profile", "discover"])
            self.assertEqual(exit_code, 0)
            text = captured_stdout.getvalue()
            self.assertIn("online", text.lower())
            self.assertIn("/scan", text)

    def test_diff_from_template_to_file_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "profile.toml"
            tmpl_mgr = TemplateManager()
            toml_content = tmpl_mgr.render_profile_toml(
                "gazebo_turtlebot3_burger",
                overrides={"robot": {"mode": "real"}},
            )
            profile_path.write_text(toml_content, encoding="utf-8")

            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main([
                    "profile", "diff",
                    "--from", "gazebo_turtlebot3_burger",
                    "--to", str(profile_path),
                    "--json",
                ])
            self.assertEqual(exit_code, 0)
            output = json.loads(captured_stdout.getvalue())
            self.assertTrue(output["has_changes"])
            paths = [d["path"] for d in output["diff_fields"]]
            self.assertIn("robot.mode", paths)

    def test_diff_human_readable_impact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            p1 = Path(tmp_dir) / "p1.toml"
            p2 = Path(tmp_dir) / "p2.toml"
            p1.write_text('[robot]\nmode = "simulation"\nlaser_scan_topic = "/scan"\n', encoding="utf-8")
            p2.write_text('[robot]\nmode = "real"\nlaser_scan_topic = "/laser_2"\n', encoding="utf-8")

            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main([
                    "profile", "diff",
                    "--from", str(p1),
                    "--to", str(p2),
                ])
            self.assertEqual(exit_code, 0)
            text = captured_stdout.getvalue()
            self.assertIn("robot.mode", text)
            self.assertIn("CRITICAL", text.upper())

    def test_history_and_rollback_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "profile.toml"
            profile_path.write_text('[robot]\nid = "test_bot_v1"\n', encoding="utf-8")

            snap_mgr = ProfileSnapshotManager(history_root=profile_path.parent / ".history")
            s1 = snap_mgr.create_snapshot(profile_path, summary="Initial version")

            profile_path.write_text('[robot]\nid = "test_bot_v2"\n', encoding="utf-8")
            s2 = snap_mgr.create_snapshot(profile_path, summary="Version 2 update")

            # Test history --json
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main([
                    "profile", "history",
                    "--profile", str(profile_path),
                    "--json",
                ])
            self.assertEqual(exit_code, 0)
            hist = json.loads(captured_stdout.getvalue())
            self.assertEqual(len(hist), 2)
            self.assertEqual(hist[0]["snapshot_id"], s2.snapshot_id)

            # Test history human-readable
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main([
                    "profile", "history",
                    "--profile", str(profile_path),
                ])
            self.assertEqual(exit_code, 0)
            text = captured_stdout.getvalue()
            self.assertIn(s1.snapshot_id, text)
            self.assertIn("Initial version", text)

            # Test rollback --json
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                exit_code = mission_cli_main([
                    "profile", "rollback",
                    "--snapshot", s1.snapshot_id,
                    "--profile", str(profile_path),
                    "--json",
                ])
            self.assertEqual(exit_code, 0)
            rb_out = json.loads(captured_stdout.getvalue())
            self.assertEqual(rb_out["status"], "rolled_back")
            self.assertIn('id = "test_bot_v1"', profile_path.read_text(encoding="utf-8"))

            # Test rollback non-existent snapshot
            captured_stderr = io.StringIO()
            with patch("sys.stderr", captured_stderr):
                exit_code = mission_cli_main([
                    "profile", "rollback",
                    "--snapshot", "non_existent_snap_id",
                    "--profile", str(profile_path),
                ])
            self.assertNotEqual(exit_code, 0)

    def test_main_subcommand_routing(self) -> None:
        from fireclaw_core.__main__ import KNOWN_SUBCOMMANDS
        self.assertIn("profile", KNOWN_SUBCOMMANDS)


# ---------------------------------------------------------------------------
# Gateway API Tests: /config/*
# ---------------------------------------------------------------------------


class TestGatewayConfigAPI(unittest.TestCase):
    """Test REST API endpoints mounted under '/config/*' on MissionGateway."""

    def setUp(self) -> None:
        self.gateway = _make_gateway()
        self.gateway.start()
        self.base_url = self.gateway.base_url

    def tearDown(self) -> None:
        self.gateway.stop()

    def test_method_scopes_registration(self) -> None:
        self.assertEqual(resolve_required_scope("GET /config/templates"), READ_SCOPE)
        self.assertEqual(resolve_required_scope("POST /config/discover"), READ_SCOPE)
        self.assertEqual(resolve_required_scope("GET /config/schema"), READ_SCOPE)
        self.assertEqual(resolve_required_scope("POST /config/diff"), READ_SCOPE)
        self.assertEqual(resolve_required_scope("POST /config/save"), WRITE_SCOPE)
        self.assertEqual(resolve_required_scope("GET /config/history"), READ_SCOPE)
        self.assertEqual(resolve_required_scope("POST /config/rollback"), WRITE_SCOPE)
        self.assertEqual(resolve_required_scope("POST /config/test-field"), READ_SCOPE)

    def test_get_templates(self) -> None:
        status, body = _json_request(self.base_url, "GET", "/config/templates")
        self.assertEqual(status, 200)
        self.assertIn("templates", body)
        self.assertEqual(len(body["templates"]), 5)
        template_ids = [t["template_id"] for t in body["templates"]]
        self.assertIn("gazebo_turtlebot3_burger", template_ids)
        self.assertIn("real_firefighting_tracked", template_ids)

    def test_post_discover(self) -> None:
        status, body = _json_request(
            self.base_url,
            "POST",
            "/config/discover",
            payload={"master_uri": "http://127.0.0.1:11998", "timeout": 0.2},
        )
        self.assertEqual(status, 200)
        self.assertIn("discovered", body)
        self.assertEqual(body["master_uri"], "http://127.0.0.1:11998")
        self.assertFalse(body["discovered"])

    def test_get_schema(self) -> None:
        status, body = _json_request(self.base_url, "GET", "/config/schema")
        self.assertEqual(status, 200)
        self.assertIn("schemas", body)
        schemas = body["schemas"]
        self.assertIn("navigation", schemas)
        self.assertIn("ros1_sensor", schemas)
        self.assertIn("fields", schemas["navigation"])

    def test_post_diff(self) -> None:
        old_config = '[robot]\nmode = "simulation"\nlaser_scan_topic = "/scan"\n'
        new_config = '[robot]\nmode = "real"\nlaser_scan_topic = "/scan"\n'
        status, body = _json_request(
            self.base_url,
            "POST",
            "/config/diff",
            payload={"old_config": old_config, "new_config": new_config},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["has_changes"])
        self.assertEqual(len(body["diff_fields"]), 1)
        self.assertEqual(body["diff_fields"][0]["path"], "robot.mode")
        self.assertEqual(body["diff_fields"][0]["impact_level"], "critical")

    def test_post_diff_json_dicts(self) -> None:
        old_cfg = {"robot": {"mode": "simulation"}}
        new_cfg = {"robot": {"mode": "simulation", "cmd_vel_topic": "/cmd_vel"}}
        status, body = _json_request(
            self.base_url,
            "POST",
            "/config/diff",
            payload={"old_config": old_cfg, "new_config": new_cfg},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["has_changes"])
        self.assertEqual(body["diff_fields"][0]["path"], "robot.cmd_vel_topic")

    def test_post_save_and_get_history_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_path = str(Path(tmp_dir) / "profile.toml")

            # Save initial profile
            save_payload = {
                "profile_path": profile_path,
                "content": '[robot]\nid = "firebot_v1"\nmode = "simulation"\n',
                "create_snapshot": True,
                "reason": "Initial web save",
            }
            status, body = _json_request(self.base_url, "POST", "/config/save", payload=save_payload)
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "saved")
            snap1_id = body.get("snapshot_id")
            self.assertIsNotNone(snap1_id)

            # Save updated profile
            save_payload2 = {
                "profile_path": profile_path,
                "content": '[robot]\nid = "firebot_v2"\nmode = "real"\n',
                "create_snapshot": True,
                "reason": "Upgrade to real bot",
            }
            status2, body2 = _json_request(self.base_url, "POST", "/config/save", payload=save_payload2)
            self.assertEqual(status2, 200)
            snap2_id = body2.get("snapshot_id")
            self.assertIsNotNone(snap2_id)

            # GET history
            hist_status, hist_body = _json_request(
                self.base_url,
                "GET",
                f"/config/history?profile={profile_path}",
            )
            self.assertEqual(hist_status, 200)
            self.assertIn("snapshots", hist_body)
            self.assertEqual(len(hist_body["snapshots"]), 2)

            # POST rollback to snap1
            rb_payload = {
                "profile_path": profile_path,
                "snapshot_id": snap1_id,
                "reason": "Reverting to v1",
            }
            rb_status, rb_body = _json_request(self.base_url, "POST", "/config/rollback", payload=rb_payload)
            self.assertEqual(rb_status, 200)
            self.assertEqual(rb_body["status"], "rolled_back")

            # Verify file content
            content = Path(profile_path).read_text(encoding="utf-8")
            self.assertIn('id = "firebot_v1"', content)

    def test_post_test_field(self) -> None:
        # Test valid ROS master offline probe
        payload = {
            "field_name": "robot.ros_master_uri",
            "field_value": "http://127.0.0.1:11997",
            "probe_action": "master",
        }
        status, body = _json_request(self.base_url, "POST", "/config/test-field", payload=payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "error")  # master offline
        self.assertIn("latency_ms", body)

        # Test simulated topic probe
        payload_topic = {
            "field_name": "robot.laser_scan_topic",
            "field_value": "/scan",
            "probe_action": "topic",
        }
        status, body = _json_request(self.base_url, "POST", "/config/test-field", payload=payload_topic)
        self.assertEqual(status, 200)
        self.assertIn("status", body)

    def test_error_handling_invalid_payloads(self) -> None:
        # Missing profile_path on save
        status, body = _json_request(self.base_url, "POST", "/config/save", payload={"content": "abc"})
        self.assertEqual(status, 400)

        # Missing snapshot_id on rollback
        status, body = _json_request(self.base_url, "POST", "/config/rollback", payload={"profile_path": "/tmp/xyz"})
        self.assertEqual(status, 400)

        # Non-existent snapshot rollback
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


if __name__ == "__main__":
    unittest.main()
