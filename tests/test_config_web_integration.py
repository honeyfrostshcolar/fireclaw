"""Web console integration tests for zero-handwritten configuration (Task 4).

Validates:
1. index.html contains all zero-handwritten configuration controls and modals:
   - #template-select, #btn-apply-template, #btn-discover-ros
   - #schema-fields-container, #btn-preview-diff
   - #diff-modal, #diff-viewer, #diff-impact-alerts, #btn-confirm-save-profile, #btn-cancel-diff
   - #snapshot-select, #btn-rollback-snapshot
2. style.css contains styling for diff modal, impact badges (.impact-critical, .impact-warning, .impact-info),
   template selector, and inline test probe indicators.
3. app.js contains required configuration management methods:
   - loadTemplates, applySelectedTemplate, discoverRosTopics, renderDynamicSchemaForm,
     testField, openDiffModal, saveProfile, loadSnapshots, rollbackSnapshot.
4. Gateway REST API contract integration for Web Console configuration workflow:
   - GET /config/templates
   - GET /config/schema
   - POST /config/discover
   - POST /config/test-field
   - POST /config/diff
   - POST /config/save
   - GET /config/history
   - POST /config/rollback
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.gateway.network_security import GatewayNetworkPolicy
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig


WEB_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "src" / "fireclaw_core" / "web_console"
INDEX_HTML_PATH = WEB_CONSOLE_DIR / "index.html"
STYLE_CSS_PATH = WEB_CONSOLE_DIR / "style.css"
APP_JS_PATH = WEB_CONSOLE_DIR / "app.js"


class HTMLIDExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for attr, val in attrs:
            if attr == "id" and val:
                self.ids.add(val)
            elif attr == "class" and val:
                for cls in val.split():
                    self.classes.add(cls)


def _make_dummy_agent() -> MissionAgent:
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1",
            base_url="http://127.0.0.1:8765",
            capabilities=("victim_search",),
        )
    ])
    return MissionAgent(registry=registry)


def _make_gateway() -> MissionGateway:
    config = MissionGatewayConfig(
        port=0,
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
    payload: dict | None = None,
) -> tuple[int, dict]:
    from urllib import request
    from urllib.error import HTTPError

    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=5.0) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return exc.code, parsed


class TestWebConsoleHTMLStructure(unittest.TestCase):
    """Test HTML elements for Zero-Handwritten Configuration in Web Console."""

    @classmethod
    def setUpClass(cls):
        cls.html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        parser = HTMLIDExtractor()
        parser.feed(cls.html_content)
        cls.extracted_ids = parser.ids
        cls.extracted_classes = parser.classes

    def test_zero_config_settings_controls_exist(self):
        """Verify all zero-handwritten configuration controls exist in index.html."""
        required_controls = {
            "template-select",
            "btn-apply-template",
            "btn-discover-ros",
            "schema-fields-container",
            "btn-preview-diff",
            "snapshot-select",
            "btn-rollback-snapshot",
        }
        for elem_id in required_controls:
            self.assertIn(
                elem_id,
                self.extracted_ids,
                f"Required zero-config control #{elem_id} missing in index.html",
            )

    def test_diff_and_impact_modal_exists(self):
        """Verify diff and impact analysis modal dialog elements exist."""
        required_modal_elements = {
            "diff-modal",
            "diff-viewer",
            "diff-impact-alerts",
            "btn-confirm-save-profile",
            "btn-cancel-diff",
        }
        for elem_id in required_modal_elements:
            self.assertIn(
                elem_id,
                self.extracted_ids,
                f"Diff modal element #{elem_id} missing in index.html",
            )


class TestWebConsoleCSSStyles(unittest.TestCase):
    """Test CSS styles for Zero-Handwritten Configuration."""

    @classmethod
    def setUpClass(cls):
        cls.css_content = STYLE_CSS_PATH.read_text(encoding="utf-8")

    def test_impact_badges_styles_exist(self):
        """Verify CSS defines .impact-critical, .impact-warning, .impact-info."""
        self.assertIn(".impact-critical", self.css_content, "Missing .impact-critical style")
        self.assertIn(".impact-warning", self.css_content, "Missing .impact-warning style")
        self.assertIn(".impact-info", self.css_content, "Missing .impact-info style")

    def test_diff_modal_and_viewer_styles_exist(self):
        """Verify CSS contains styling for diff modal and diff viewer."""
        self.assertTrue(
            "#diff-modal" in self.css_content or ".diff-modal" in self.css_content or "diff-viewer" in self.css_content,
            "Missing diff modal/viewer CSS styles",
        )


class TestWebConsoleAppJSFunctions(unittest.TestCase):
    """Test JavaScript code structure for configuration workflows."""

    @classmethod
    def setUpClass(cls):
        cls.js_content = APP_JS_PATH.read_text(encoding="utf-8")

    def test_config_methods_defined(self):
        """Verify app.js defines the 9 required configuration methods."""
        required_methods = [
            "loadTemplates",
            "applySelectedTemplate",
            "discoverRosTopics",
            "renderDynamicSchemaForm",
            "testField",
            "openDiffModal",
            "saveProfile",
            "loadSnapshots",
            "rollbackSnapshot",
        ]
        for method_name in required_methods:
            self.assertIn(
                method_name,
                self.js_content,
                f"Required method '{method_name}' not defined in app.js",
            )


class TestWebConsoleGatewayAPIIntegration(unittest.TestCase):
    """Integration tests verifying Gateway /config/* endpoints for Web Console frontend."""

    def setUp(self):
        self.gateway = _make_gateway()
        self.gateway.start()
        self.base_url = self.gateway.base_url

    def tearDown(self):
        self.gateway.stop()

    def test_templates_api_for_dropdown(self):
        """GET /config/templates returns list of 5 pre-baked robot templates."""
        code, data = _json_request(self.base_url, "GET", "/config/templates")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ok")
        templates = data.get("templates", [])
        self.assertGreaterEqual(len(templates), 5)
        template_ids = {t["template_id"] for t in templates}
        self.assertIn("gazebo_turtlebot3_burger", template_ids)
        self.assertIn("real_firefighting_tracked", template_ids)

    def test_schema_api_for_dynamic_form(self):
        """GET /config/schema returns schemas for dynamic form rendering."""
        code, data = _json_request(self.base_url, "GET", "/config/schema")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ok")
        schemas = data.get("schemas", {})
        self.assertTrue("fireclaw.navigation.move_base" in schemas or "navigation" in schemas)
        nav_schema = schemas.get("fireclaw.navigation.move_base") or schemas.get("navigation")
        fields = {f["name"]: f for f in nav_schema["fields"]}
        self.assertIn("cmd_vel_topic", fields)
        self.assertIn("probe_action", fields["cmd_vel_topic"])

    def test_test_field_api_probe(self):
        """POST /config/test-field tests field connectivity."""
        code, data = _json_request(
            self.base_url,
            "POST",
            "/config/test-field",
            {
                "field_name": "cmd_vel_topic",
                "field_value": "/cmd_vel",
                "probe_action": "ros_topic",
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("field_name"), "cmd_vel_topic")
        self.assertIn("latency_ms", data)

    def test_diff_api_for_impact_modal(self):
        """POST /config/diff computes visual diff and categorized impact levels."""
        old_toml = """
[robot]
id = "test_bot"
base_url = "http://127.0.0.1:8765"
mode = "simulation"
"""
        new_toml = """
[robot]
id = "test_bot"
base_url = "http://127.0.0.1:8765"
mode = "real"
"""
        code, data = _json_request(
            self.base_url,
            "POST",
            "/config/diff",
            {"old_config": old_toml, "new_config": new_toml},
        )
        self.assertEqual(code, 200)
        self.assertTrue(data.get("has_changes"))
        diff_fields = data.get("diff_fields", [])
        self.assertGreater(len(diff_fields), 0)
        critical_items = [f for f in diff_fields if f.get("impact_level") == "critical"]
        self.assertGreater(len(critical_items), 0)

    def test_save_history_and_rollback_workflow(self):
        """POST /config/save creates snapshot, GET /config/history lists it, POST /config/rollback restores it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "robot_profile.toml"
            initial_content = '[robot]\nid = "bot_v1"\n'
            updated_content = '[robot]\nid = "bot_v2"\n'

            # 1. Initial Save
            code, save_resp = _json_request(
                self.base_url,
                "POST",
                "/config/save",
                {
                    "profile_path": str(profile_path),
                    "content": initial_content,
                    "reason": "Initial setup",
                    "create_snapshot": True,
                },
            )
            self.assertEqual(code, 200)
            initial_snap_id = save_resp.get("snapshot_id")
            self.assertIsNotNone(initial_snap_id)

            # 2. Update Save
            code, save_resp2 = _json_request(
                self.base_url,
                "POST",
                "/config/save",
                {
                    "profile_path": str(profile_path),
                    "content": updated_content,
                    "reason": "Updated to v2",
                    "create_snapshot": True,
                },
            )
            self.assertEqual(code, 200)
            self.assertEqual(profile_path.read_text(encoding="utf-8"), updated_content)

            # 3. History
            code, hist_resp = _json_request(
                self.base_url,
                "GET",
                f"/config/history?profile={profile_path}",
            )
            self.assertEqual(code, 200)
            snapshots = hist_resp.get("snapshots", [])
            self.assertGreaterEqual(len(snapshots), 2)

            # 4. Rollback
            code, roll_resp = _json_request(
                self.base_url,
                "POST",
                "/config/rollback",
                {
                    "profile_path": str(profile_path),
                    "snapshot_id": initial_snap_id,
                },
            )
            self.assertEqual(code, 200)
            self.assertEqual(roll_resp.get("status"), "rolled_back")
            self.assertEqual(profile_path.read_text(encoding="utf-8"), initial_content)


if __name__ == "__main__":
    unittest.main()
