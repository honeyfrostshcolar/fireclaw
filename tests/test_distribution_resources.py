"""Tests for package-owned distribution resources (setup templates and simulation catalogs)."""
from __future__ import annotations

from pathlib import Path
import unittest


RESOURCE_TRAVERSAL_IDS = (
    "../__init__",
    "../../../../pyproject",
    "/etc/passwd",
    "sub/directory",
    r"..\__init__",
    "",
    " gazebo_turtlebot3 ",
)

class TestDistributionResources(unittest.TestCase):
    def test_load_setup_template_reads_package_resource(self):
        from fireclaw_core.resources import load_setup_template

        content = load_setup_template("gazebo_turtlebot3")
        self.assertIn("[fireclaw.setup]", content)
        self.assertIn("gazebo-turtlebot3-burger-v1", content)
        self.assertIn("{{WORKSPACE_ROOT}}", content)

    def test_load_setup_template_unknown_id_fails_closed(self):
        from fireclaw_core.resources import load_setup_template

        with self.assertRaises(FileNotFoundError):
            load_setup_template("unknown_template_xyz")

    def test_load_setup_template_rejects_noncanonical_ids(self):
        from fireclaw_core.resources import load_setup_template

        for bad_id in RESOURCE_TRAVERSAL_IDS:
            with self.subTest(template_id=bad_id), self.assertRaises((FileNotFoundError, ValueError)):
                load_setup_template(bad_id)

    def test_package_setup_template_matches_compatibility_example(self):
        from fireclaw_core.resources import load_setup_template

        package_content = load_setup_template("gazebo_turtlebot3")
        example_path = Path(__file__).resolve().parents[1] / "examples" / "setup_templates" / "gazebo_turtlebot3.toml"
        if example_path.is_file():
            example_content = example_path.read_text(encoding="utf-8")
            self.assertEqual(package_content, example_content, "Package template and example mirror must be identical")

    def test_load_simulation_bundle_catalog_reads_json(self):
        from fireclaw_core.resources import load_simulation_bundle_catalog

        catalog = load_simulation_bundle_catalog("turtlebot3-burger-v1")
        self.assertEqual(catalog.get("schema_version"), 1)
        self.assertEqual(catalog.get("bundle_id"), "turtlebot3-burger-v1")
        self.assertIn("include_paths", catalog)
        self.assertIn("required_paths", catalog)
        self.assertIn("forbidden_path_segments", catalog)
        self.assertIn("size_budget_compressed_bytes", catalog)
        self.assertIn("size_budget_unpacked_bytes", catalog)
        self.assertIn("provenance_required_paths", catalog)
        self.assertIn("upstream_sources", catalog)

    def test_load_simulation_bundle_catalog_unknown_id_fails_closed(self):
        from fireclaw_core.resources import load_simulation_bundle_catalog

        with self.assertRaises(FileNotFoundError):
            load_simulation_bundle_catalog("nonexistent_bundle")

    def test_load_simulation_bundle_catalog_rejects_noncanonical_ids(self):
        from fireclaw_core.resources import load_simulation_bundle_catalog

        for bad_id in RESOURCE_TRAVERSAL_IDS:
            with self.subTest(bundle_id=bad_id), self.assertRaises((FileNotFoundError, ValueError)):
                load_simulation_bundle_catalog(bad_id)

    def test_catalog_validation_rejects_missing_provenance(self):
        from fireclaw_core.resources import (
            load_simulation_bundle_catalog,
            validate_simulation_bundle_catalog,
        )

        catalog = dict(load_simulation_bundle_catalog("turtlebot3-burger-v1"))
        catalog.pop("upstream_sources")
        with self.assertRaisesRegex(ValueError, "upstream_sources"):
            validate_simulation_bundle_catalog(catalog)

    def test_read_web_console_asset_allowed_files(self):
        from fireclaw_core.web_console import read_web_console_asset

        index_bytes, index_type = read_web_console_asset("index.html")
        self.assertIn(b"<title>FireClaw Web Console</title>", index_bytes)
        self.assertEqual(index_type, "text/html; charset=utf-8")

        css_bytes, css_type = read_web_console_asset("style.css")
        self.assertGreater(len(css_bytes), 0)
        self.assertEqual(css_type, "text/css; charset=utf-8")

        js_bytes, js_type = read_web_console_asset("app.js")
        self.assertGreater(len(js_bytes), 0)
        self.assertEqual(js_type, "application/javascript; charset=utf-8")

    def test_read_web_console_asset_forbidden_or_traversal_fails_closed(self):
        from fireclaw_core.web_console import read_web_console_asset

        for bad_name in ("../../etc/passwd", "../__init__.py", "nonexistent.png", "", "sub/dir.js"):
            with self.assertRaises(FileNotFoundError):
                read_web_console_asset(bad_name)
