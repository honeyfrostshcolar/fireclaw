"""Tests for FriendlyError registry, resolution, and CLI formatting."""
from __future__ import annotations
import unittest


class TestFriendlyErrorTemplate(unittest.TestCase):
    def test_template_fields_exist(self):
        from fireclaw_core.errors.friendly_errors import FriendlyErrorTemplate
        t = FriendlyErrorTemplate(
            error_code="test_code",
            severity="warning",
            what_happened="Something happened: {detail}.",
            robot_safe_status="Robot is safe.",
            action_taken="Stopped.",
            next_steps="Retry.",
            suggested_actions=[{"label": "Retry", "action": "retry"}],
        )
        self.assertEqual(t.error_code, "test_code")
        self.assertEqual(t.severity, "warning")

    def test_resolve_known_error_code(self):
        from fireclaw_core.errors.friendly_errors import (
            UNVERIFIED_ACTION_STATUS,
            UNVERIFIED_ROBOT_STATUS,
            resolve_friendly_error,
        )
        resp = resolve_friendly_error("ros_master_unreachable", {"master_uri": "http://localhost:11311", "timeout": "2.0"})
        self.assertEqual(resp.error_code, "ros_master_unreachable")
        self.assertEqual(resp.severity, "critical")
        self.assertIn("localhost:11311", resp.what_happened)
        self.assertIn("2.0", resp.what_happened)
        self.assertEqual(resp.robot_safe_status, UNVERIFIED_ROBOT_STATUS)
        self.assertEqual(resp.action_taken, UNVERIFIED_ACTION_STATUS)
        self.assertNotIn("绝对静止", resp.robot_safe_status)
        self.assertNotIn("电机使能已关闭", resp.robot_safe_status)
        self.assertTrue(len(resp.next_steps) > 0)

    def test_resolve_unknown_code_uses_fallback(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("totally_unknown_xyz_123")
        self.assertEqual(resp.severity, "warning")
        self.assertIn("totally_unknown_xyz_123", resp.what_happened)
        self.assertTrue(len(resp.robot_safe_status) > 0)

    def test_resolve_with_technical_details(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("sensor_no_lidar_data", {"topic_name": "/scan"}, technical_details="Traceback ...")
        self.assertEqual(resp.technical_details, "Traceback ...")
        self.assertIn("/scan", resp.what_happened)

    def test_to_dict_contains_all_fields(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("gateway_connection_failed", {"gateway_url": "http://127.0.0.1:8765"})
        d = resp.to_dict()
        for key in ("error_code", "severity", "what_happened", "robot_safe_status", "action_taken", "next_steps", "suggested_actions"):
            self.assertIn(key, d)

    def test_format_cli_output_has_four_sections(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error, format_friendly_error_cli
        resp = resolve_friendly_error("authorization_missing")
        text = format_friendly_error_cli(resp, verbose=False)
        self.assertIn("发生了什么", text)
        self.assertIn("机器人安全证据", text)
        self.assertIn("自动处置回执", text)
        self.assertIn("建议下一步", text)
        self.assertNotIn("Traceback", text)

    def test_format_cli_verbose_includes_technical(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error, format_friendly_error_cli
        resp = resolve_friendly_error("config_save_failed", {"profile_path": "/tmp/test.toml"}, technical_details="PermissionError: [Errno 13]")
        text = format_friendly_error_cli(resp, verbose=True)
        self.assertIn("PermissionError", text)

    def test_registry_has_minimum_coverage(self):
        from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY
        self.assertGreaterEqual(len(FRIENDLY_ERROR_REGISTRY), 30)

    def test_registry_never_exposes_static_robot_state_or_action_claims(self):
        from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY
        from fireclaw_core.errors.friendly_errors import (
            UNVERIFIED_ACTION_STATUS,
            UNVERIFIED_ROBOT_STATUS,
        )

        for template in FRIENDLY_ERROR_REGISTRY.values():
            self.assertEqual(template.robot_safe_status, UNVERIFIED_ROBOT_STATUS)
            self.assertEqual(template.action_taken, UNVERIFIED_ACTION_STATUS)

    def test_safe_interpolation_missing_key(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("ros_master_unreachable", {})
        # Should not raise KeyError, missing keys remain as {key}
        self.assertIsNotNone(resp.what_happened)

    def test_severity_values_valid(self):
        from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY
        for code, tpl in FRIENDLY_ERROR_REGISTRY.items():
            self.assertIn(tpl.severity, ("critical", "warning", "info"), f"Invalid severity for {code}")

    def test_all_categories_represented(self):
        from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY
        expected_categories_prefixes = [
            "sensor_",
            "ros_",
            "gateway_",
            "network_",
            "authorization_",
            "safety_",
            "planner_",
            "skill_",
            "config_",
            "disk_",
            "robot_",
        ]
        registered_keys = set(FRIENDLY_ERROR_REGISTRY.keys())
        for prefix in expected_categories_prefixes:
            matching = [k for k in registered_keys if k.startswith(prefix)]
            self.assertTrue(len(matching) > 0, f"No error codes starting with prefix {prefix}")

    def test_module_exports(self):
        import fireclaw_core.errors as errors
        self.assertTrue(hasattr(errors, "FriendlyErrorTemplate"))
        self.assertTrue(hasattr(errors, "FriendlyErrorResponse"))
        self.assertTrue(hasattr(errors, "FALLBACK_TEMPLATE"))
        self.assertTrue(hasattr(errors, "FRIENDLY_ERROR_REGISTRY"))
        self.assertTrue(hasattr(errors, "resolve_friendly_error"))
        self.assertTrue(hasattr(errors, "format_friendly_error_cli"))


if __name__ == "__main__":
    unittest.main()
