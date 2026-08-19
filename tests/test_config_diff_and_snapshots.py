"""Tests for FireClaw Config Diff Engine, Snapshots, and Secrets Isolation (Task 2).

Covers:
1. ConfigDiffEngine:
   - Nested dictionary & TOML string comparisons
   - Change types: added, modified, removed
   - Domain impact evaluation rules (mode change critical, nav warning, sensor warning, actuator warning, info)
   - Unified diff text generation & dataclass serialization
2. ProfileSnapshotManager:
   - Snapshot creation with SHA256 hash8 & UTC timestamp ID
   - 20-version rotation & automatic pruning of older snapshots
   - Snapshot listing (sorted newest first) and retrieval by ID
   - Atomic rollback with audit snapshot creation
3. SecretManager:
   - Credentials storage with 0600 file permissions and 0700 directory permissions
   - Secret retrieval with environment variable fallback & precedence
   - Secret masking (sk-... and generic tokens)
   - Profile dictionary sanitization (stripping plaintext credentials for api_key_env references)
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fireclaw_core.config.diff_engine import (
    DiffField,
    ConfigDiffResult,
    ConfigDiffEngine,
)
from fireclaw_core.config.snapshots import (
    ConfigSnapshot,
    ProfileSnapshotManager,
)
from fireclaw_core.config.secrets import (
    SecretManager,
)
from fireclaw_core.infra import tomllib_compat as tomllib


class TestConfigDiffEngine(unittest.TestCase):
    """Test ConfigDiffEngine, DiffField, and ConfigDiffResult."""

    def setUp(self) -> None:
        self.engine = ConfigDiffEngine()

    def test_no_changes(self) -> None:
        old_config = {
            "robot": {"id": "bot_01", "mode": "simulation"},
            "sensors": {"laser": "/scan"},
        }
        new_config = {
            "robot": {"id": "bot_01", "mode": "simulation"},
            "sensors": {"laser": "/scan"},
        }
        result = self.engine.compare_configs(old_config, new_config)
        self.assertFalse(result.has_changes)
        self.assertEqual(len(result.diff_fields), 0)
        self.assertEqual(len(result.impact_summary_zh), 0)

    def test_mode_change_critical_impact(self) -> None:
        old_config = {"robot": {"mode": "simulation"}}
        new_config = {"robot": {"mode": "real"}}

        result = self.engine.compare_configs(old_config, new_config)
        self.assertTrue(result.has_changes)
        self.assertEqual(len(result.diff_fields), 1)

        diff = result.diff_fields[0]
        self.assertEqual(diff.path, "robot.mode")
        self.assertEqual(diff.old_val, "simulation")
        self.assertEqual(diff.new_val, "real")
        self.assertEqual(diff.change_type, "modified")
        self.assertEqual(diff.impact_level, "critical")
        self.assertIn("模式变更", diff.impact_description_zh)
        self.assertIn("安全门禁", diff.impact_description_zh)
        self.assertTrue(any("模式变更" in s for s in result.impact_summary_zh))

    def test_navigation_topic_change_warning_impact(self) -> None:
        old_config = {"robot": {"navigation_action": "/move_base", "cmd_vel_topic": "/cmd_vel"}}
        new_config = {"robot": {"navigation_action": "/nav2_action", "cmd_vel_topic": "/mobile_base/cmd_vel"}}

        result = self.engine.compare_configs(old_config, new_config)
        self.assertTrue(result.has_changes)
        self.assertEqual(len(result.diff_fields), 2)

        for diff in result.diff_fields:
            self.assertEqual(diff.change_type, "modified")
            self.assertEqual(diff.impact_level, "warning")
            self.assertIn("导航", diff.impact_description_zh)

    def test_sensor_topic_change_warning_impact(self) -> None:
        old_config = {
            "sensors": {
                "laser_scan_topic": "/scan",
                "thermal_camera_topic": "/thermal/image",
                "gas_sensor_topic": "/gas/data",
            }
        }
        new_config = {
            "sensors": {
                "laser_scan_topic": "/robot/scan",
                "thermal_camera_topic": "/robot/thermal/image",
                "gas_sensor_topic": "/robot/gas/data",
            }
        }

        result = self.engine.compare_configs(old_config, new_config)
        self.assertTrue(result.has_changes)
        self.assertEqual(len(result.diff_fields), 3)

        for diff in result.diff_fields:
            self.assertEqual(diff.impact_level, "warning")
            self.assertIn("传感器", diff.impact_description_zh)

    def test_actuator_change_warning_impact(self) -> None:
        old_config = {
            "actuators": {
                "water_cannon_topic": "/actuators/water_cannon",
                "gimbal_cmd_topic": "/gimbal/cmd",
            }
        }
        new_config = {
            "actuators": {
                "water_cannon_topic": "/robot/cannon/cmd",
                "gimbal_cmd_topic": "/robot/gimbal/cmd",
            }
        }

        result = self.engine.compare_configs(old_config, new_config)
        self.assertTrue(result.has_changes)
        self.assertEqual(len(result.diff_fields), 2)

        for diff in result.diff_fields:
            self.assertEqual(diff.impact_level, "warning")
            self.assertIn("执行机构", diff.impact_description_zh)

    def test_general_field_added_and_removed(self) -> None:
        old_config = {
            "robot": {"id": "bot_01", "old_param": 100}
        }
        new_config = {
            "robot": {"id": "bot_01", "new_param": 200}
        }

        result = self.engine.compare_configs(old_config, new_config)
        self.assertTrue(result.has_changes)
        self.assertEqual(len(result.diff_fields), 2)

        diff_map = {d.path: d for d in result.diff_fields}
        self.assertIn("robot.old_param", diff_map)
        self.assertEqual(diff_map["robot.old_param"].change_type, "removed")
        self.assertEqual(diff_map["robot.old_param"].old_val, 100)
        self.assertIsNone(diff_map["robot.old_param"].new_val)
        self.assertEqual(diff_map["robot.old_param"].impact_level, "info")

        self.assertIn("robot.new_param", diff_map)
        self.assertEqual(diff_map["robot.new_param"].change_type, "added")
        self.assertIsNone(diff_map["robot.new_param"].old_val)
        self.assertEqual(diff_map["robot.new_param"].new_val, 200)
        self.assertEqual(diff_map["robot.new_param"].impact_level, "info")

    def test_compare_toml_strings(self) -> None:
        old_toml = """[robot]
id = "bot_01"
mode = "simulation"
"""
        new_toml = """[robot]
id = "bot_01"
mode = "real"
"""
        result = self.engine.compare_toml_strings(old_toml, new_toml)
        self.assertTrue(result.has_changes)
        self.assertEqual(len(result.diff_fields), 1)
        self.assertEqual(result.diff_fields[0].path, "robot.mode")
        self.assertEqual(result.diff_fields[0].impact_level, "critical")
        self.assertTrue(len(result.raw_diff_text) > 0)
        self.assertIn("-mode = \"simulation\"", result.raw_diff_text)
        self.assertIn("+mode = \"real\"", result.raw_diff_text)

    def test_diff_serialization_to_dict(self) -> None:
        field = DiffField(
            path="robot.mode",
            old_val="simulation",
            new_val="real",
            change_type="modified",
            impact_level="critical",
            impact_description_zh="模式变更",
        )
        d = field.to_dict()
        self.assertEqual(d["path"], "robot.mode")
        self.assertEqual(d["impact_level"], "critical")

        result = ConfigDiffResult(
            has_changes=True,
            diff_fields=[field],
            impact_summary_zh=["模式变更"],
            raw_diff_text="--- old\n+++ new\n",
        )
        rd = result.to_dict()
        self.assertTrue(rd["has_changes"])
        self.assertEqual(len(rd["diff_fields"]), 1)
        self.assertEqual(rd["raw_diff_text"], "--- old\n+++ new\n")


class TestProfileSnapshotManager(unittest.TestCase):
    """Test ProfileSnapshotManager and ConfigSnapshot."""

    def test_create_snapshot_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_root = Path(tmp_dir) / ".history"
            manager = ProfileSnapshotManager(history_root=history_root, max_snapshots=5)

            profile_file = Path(tmp_dir) / "robot.toml"
            profile_file.write_text('[robot]\nid = "fire_bot_01"\nmode = "real"\n', encoding="utf-8")

            snapshot = manager.create_snapshot(profile_file, summary="初始配置生成")

            self.assertIsInstance(snapshot, ConfigSnapshot)
            self.assertEqual(snapshot.profile_name, "robot")
            self.assertEqual(snapshot.summary, "初始配置生成")
            self.assertEqual(len(snapshot.hash8), 8)
            self.assertTrue(snapshot.snapshot_id.startswith("robot."))
            self.assertTrue(snapshot.snapshot_id.endswith(f".{snapshot.hash8}"))
            self.assertTrue(Path(snapshot.file_path).exists())

            # Verify snapshot file content matches original
            content = Path(snapshot.file_path).read_text(encoding="utf-8")
            self.assertEqual(content, profile_file.read_text(encoding="utf-8"))

            # Test to_dict
            d = snapshot.to_dict()
            self.assertEqual(d["snapshot_id"], snapshot.snapshot_id)
            self.assertEqual(d["profile_name"], "robot")

    def test_list_snapshots_sorted_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_root = Path(tmp_dir) / ".history"
            manager = ProfileSnapshotManager(history_root=history_root, max_snapshots=10)

            profile_file = Path(tmp_dir) / "fire_bot.toml"

            # Create 3 snapshots sequentially
            profile_file.write_text("version = 1\n", encoding="utf-8")
            s1 = manager.create_snapshot(profile_file, summary="v1")

            profile_file.write_text("version = 2\n", encoding="utf-8")
            s2 = manager.create_snapshot(profile_file, summary="v2")

            profile_file.write_text("version = 3\n", encoding="utf-8")
            s3 = manager.create_snapshot(profile_file, summary="v3")

            snapshots = manager.list_snapshots("fire_bot")
            self.assertEqual(len(snapshots), 3)
            # Newest first
            self.assertEqual(snapshots[0].summary, "v3")
            self.assertEqual(snapshots[1].summary, "v2")
            self.assertEqual(snapshots[2].summary, "v1")

    def test_max_snapshots_rotation_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_root = Path(tmp_dir) / ".history"
            max_limit = 5
            manager = ProfileSnapshotManager(history_root=history_root, max_snapshots=max_limit)

            profile_file = Path(tmp_dir) / "robot.toml"

            # Create 8 snapshots
            for i in range(8):
                profile_file.write_text(f"version = {i}\n", encoding="utf-8")
                manager.create_snapshot(profile_file, summary=f"v{i}")

            snapshots = manager.list_snapshots("robot")
            self.assertEqual(len(snapshots), max_limit)
            # Should contain the latest versions 7, 6, 5, 4, 3
            summaries = [s.summary for s in snapshots]
            self.assertEqual(summaries, ["v7", "v6", "v5", "v4", "v3"])

    def test_get_snapshot_by_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_root = Path(tmp_dir) / ".history"
            manager = ProfileSnapshotManager(history_root=history_root, max_snapshots=5)

            profile_file = Path(tmp_dir) / "robot.toml"
            profile_file.write_text("param = 42\n", encoding="utf-8")
            created = manager.create_snapshot(profile_file, summary="checkpoint")

            retrieved = manager.get_snapshot(created.snapshot_id)
            self.assertIsNotNone(retrieved)
            assert retrieved is not None
            self.assertEqual(retrieved.snapshot_id, created.snapshot_id)
            self.assertEqual(retrieved.summary, "checkpoint")

            non_existent = manager.get_snapshot("non_existent.123.456")
            self.assertIsNone(non_existent)

    def test_rollback_to_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_root = Path(tmp_dir) / ".history"
            manager = ProfileSnapshotManager(history_root=history_root, max_snapshots=10)

            profile_file = Path(tmp_dir) / "robot.toml"
            profile_file.write_text('[robot]\nid = "original_v1"\n', encoding="utf-8")
            v1_snapshot = manager.create_snapshot(profile_file, summary="v1 original")

            # Modify profile to v2
            profile_file.write_text('[robot]\nid = "modified_v2"\n', encoding="utf-8")
            manager.create_snapshot(profile_file, summary="v2 modified")

            # Rollback to v1
            success, msg = manager.rollback_to_snapshot(v1_snapshot.snapshot_id, profile_file)
            self.assertTrue(success)
            self.assertIn("成功", msg)

            # Target file content should be restored to v1
            content = profile_file.read_text(encoding="utf-8")
            self.assertIn('id = "original_v1"', content)

            # An audit rollback snapshot should have been recorded
            snapshots = manager.list_snapshots("robot")
            self.assertTrue(any("Rollback to" in s.summary for s in snapshots))

    def test_rollback_nonexistent_snapshot_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_root = Path(tmp_dir) / ".history"
            manager = ProfileSnapshotManager(history_root=history_root, max_snapshots=5)
            profile_file = Path(tmp_dir) / "robot.toml"
            profile_file.write_text("init\n", encoding="utf-8")

            success, msg = manager.rollback_to_snapshot("invalid_snapshot_id", profile_file)
            self.assertFalse(success)
            self.assertIn("不存在", msg)


class TestSecretManager(unittest.TestCase):
    """Test SecretManager credentials isolation, permissions, masking, and profile sanitization."""

    def test_credentials_file_creation_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cred_path = Path(tmp_dir) / "secrets" / "credentials.json"
            manager = SecretManager(credentials_file=cred_path)

            manager.set_secret("OPENAI_API_KEY", "sk-proj-1234567890abcdef")

            self.assertTrue(cred_path.exists())
            # Verify file permission is 0600 (read/write only by owner)
            file_mode = stat.S_IMODE(cred_path.stat().st_mode)
            self.assertEqual(file_mode, 0o600)

            # Verify parent directory permission is 0700
            dir_mode = stat.S_IMODE(cred_path.parent.stat().st_mode)
            self.assertEqual(dir_mode, 0o700)

            # Verify retrieval
            val = manager.get_secret("OPENAI_API_KEY", env_fallback=False)
            self.assertEqual(val, "sk-proj-1234567890abcdef")

    def test_get_secret_from_env_and_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cred_path = Path(tmp_dir) / "credentials.json"
            manager = SecretManager(credentials_file=cred_path)

            manager.set_secret("TEST_SECRET_KEY", "value_from_file")

            # Without env var set
            self.assertEqual(manager.get_secret("TEST_SECRET_KEY"), "value_from_file")

            # With env var set -> env takes precedence
            os.environ["TEST_SECRET_KEY"] = "value_from_env"
            try:
                self.assertEqual(manager.get_secret("TEST_SECRET_KEY", env_fallback=True), "value_from_env")
                # When env_fallback is False, reads from file
                self.assertEqual(manager.get_secret("TEST_SECRET_KEY", env_fallback=False), "value_from_file")
            finally:
                del os.environ["TEST_SECRET_KEY"]

    def test_set_and_overwrite_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cred_path = Path(tmp_dir) / "credentials.json"
            manager = SecretManager(credentials_file=cred_path)

            manager.set_secret("KEY_A", "secret_a")
            manager.set_secret("KEY_B", "secret_b")
            self.assertEqual(manager.get_secret("KEY_A", env_fallback=False), "secret_a")
            self.assertEqual(manager.get_secret("KEY_B", env_fallback=False), "secret_b")

            # Overwrite KEY_A
            manager.set_secret("KEY_A", "new_secret_a")
            self.assertEqual(manager.get_secret("KEY_A", env_fallback=False), "new_secret_a")
            self.assertEqual(manager.get_secret("KEY_B", env_fallback=False), "secret_b")

    def test_mask_secret(self) -> None:
        manager = SecretManager()
        self.assertEqual(manager.mask_secret(""), "")
        self.assertEqual(manager.mask_secret("123"), "***")
        self.assertEqual(manager.mask_secret("123456"), "***")

        # OpenAI style key
        masked_openai = manager.mask_secret("sk-proj-abc123456789xyz")
        self.assertTrue(masked_openai.startswith("sk-"))
        self.assertTrue(masked_openai.endswith("xyz"))
        self.assertIn("***", masked_openai)
        self.assertNotIn("abc123456789", masked_openai)

        # Generic long token
        masked_generic = manager.mask_secret("my_super_secret_password_12345")
        self.assertTrue(masked_generic.startswith("my_"))
        self.assertTrue(masked_generic.endswith("345"))
        self.assertIn("***", masked_generic)

    def test_sanitize_profile_dict(self) -> None:
        manager = SecretManager()
        profile_dict = {
            "robot": {
                "id": "fire_bot_01",
                "api_key": "sk-raw-secret-key-12345",
                "adapter": "ros1",
            },
            "llm": {
                "provider": "openai",
                "auth_token": "bearer-token-secret-67890",
                "api_key_env": "FIRECLAW_API_KEY",
            },
            "system": {
                "password": "db-secret-password",
            },
        }

        sanitized = manager.sanitize_profile_dict(profile_dict)

        # Raw secrets should be stripped or replaced with api_key_env / masked
        self.assertNotIn("sk-raw-secret-key-12345", str(sanitized))
        self.assertNotIn("bearer-token-secret-67890", str(sanitized))
        self.assertNotIn("db-secret-password", str(sanitized))

        # Check that api_key is replaced with api_key_env
        self.assertIn("api_key_env", sanitized["robot"])
        self.assertNotIn("api_key", sanitized["robot"])
        self.assertEqual(sanitized["robot"]["api_key_env"], "ROBOT_API_KEY")

        # LLM already had api_key_env, auth_token replaced with auth_token_env
        self.assertEqual(sanitized["llm"]["api_key_env"], "FIRECLAW_API_KEY")
        self.assertIn("auth_token_env", sanitized["llm"])
        self.assertNotIn("auth_token", sanitized["llm"])

        # System password replaced with password_env
        self.assertIn("password_env", sanitized["system"])
        self.assertNotIn("password", sanitized["system"])


if __name__ == "__main__":
    unittest.main()
