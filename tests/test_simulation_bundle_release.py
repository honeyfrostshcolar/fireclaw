"""Tests for deterministic simulation bundle building and validation."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import venv

from fireclaw_core.resources import load_simulation_bundle_catalog


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _small_catalog() -> dict[str, object]:
    return {
        "schema_version": 1,
        "bundle_id": "test-bundle-v1",
        "bundle_version": "1.0.0",
        "compatible_fireclaw_versions": ">=0.1.0",
        "description": "test fixture",
        "include_paths": ["payload/"],
        "required_paths": ["payload/required.txt"],
        "provenance_required_paths": [
            "payload/LICENSE",
            "payload/UPSTREAM.md",
        ],
        "upstream_sources": [
            {
                "source_id": "fixture",
                "repository": "https://example.invalid/fixture.git",
                "revision": "0123456789abcdef0123456789abcdef01234567",
                "license": "MIT",
                "evidence_paths": ["payload/LICENSE", "payload/UPSTREAM.md"],
            }
        ],
        "forbidden_path_segments": ["build", ".git", "__pycache__"],
        "forbidden_file_suffixes": [".pyc", ".so"],
        "size_budget_compressed_bytes": 1024 * 1024,
        "size_budget_unpacked_bytes": 2 * 1024 * 1024,
    }


def _write_test_bundle(
    directory: Path,
    *,
    catalog: dict[str, object] | None = None,
    files: dict[str, bytes] | None = None,
    manifest_updates: dict[str, object] | None = None,
    undeclared_files: dict[str, bytes] | None = None,
    duplicate_name: str | None = None,
    symlink_name: str | None = None,
) -> Path:
    catalog = catalog or _small_catalog()
    files = files or {
        "payload/required.txt": b"required\n",
        "payload/LICENSE": b"fixture license\n",
        "payload/UPSTREAM.md": b"fixture provenance\n",
    }
    manifest_files = {
        name: {"sha256": _sha256(data), "size": len(data), "mode": "0o644"}
        for name, data in files.items()
    }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "bundle_id": catalog["bundle_id"],
        "bundle_version": catalog["bundle_version"],
        "total_files": len(files),
        "unpacked_bytes": sum(len(data) for data in files.values()),
        "files": manifest_files,
    }
    if manifest_updates:
        manifest.update(manifest_updates)

    path = directory / f"fireclaw-sim-{catalog['bundle_id']}.tar.gz"
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=1700000000) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                manifest_data = json.dumps(manifest, sort_keys=True).encode("utf-8")
                info = tarfile.TarInfo("manifest.json")
                info.size = len(manifest_data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(manifest_data))
                archive_files = dict(files)
                archive_files.update(undeclared_files or {})
                for name, data in archive_files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
                if duplicate_name:
                    data = archive_files[duplicate_name]
                    info = tarfile.TarInfo(duplicate_name)
                    info.size = len(data)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
                if symlink_name:
                    info = tarfile.TarInfo(symlink_name)
                    info.type = tarfile.SYMTYPE
                    info.linkname = "payload/required.txt"
                    archive.addfile(info)
    return path


class TestSimulationBundleRelease(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.catalog = load_simulation_bundle_catalog("turtlebot3-burger-v1")

    def test_build_and_inspect_simulation_bundle(self):
        from tools.release.simulation_bundle import (
            build_simulation_bundle,
            inspect_simulation_bundle,
        )

        with tempfile.TemporaryDirectory() as tmp_out1:
            out_dir1 = Path(tmp_out1)
            result1 = build_simulation_bundle(
                repo_root=self.repo_root,
                output_dir=out_dir1,
                catalog=self.catalog,
            )

            self.assertTrue(result1.archive_path.is_file())
            self.assertEqual(result1.archive_path.name, "fireclaw-sim-turtlebot3-burger-v1.tar.gz")
            self.assertGreater(result1.file_count, 10)
            self.assertGreater(result1.compressed_bytes, 0)
            self.assertLessEqual(result1.compressed_bytes, self.catalog["size_budget_compressed_bytes"])
            self.assertLessEqual(result1.unpacked_bytes, self.catalog["size_budget_unpacked_bytes"])

            # Test inspection of the built bundle
            inspection = inspect_simulation_bundle(
                path=result1.archive_path,
                catalog=self.catalog,
            )
            self.assertTrue(inspection.valid, f"Inspection failed with errors: {inspection.errors}")
            self.assertEqual(len(inspection.errors), 0)
            self.assertEqual(inspection.sha256, result1.sha256)
            self.assertEqual(inspection.file_count, result1.file_count)

    def test_deterministic_build_produces_identical_sha256(self):
        from tools.release.simulation_bundle import build_simulation_bundle

        with tempfile.TemporaryDirectory() as tmp_out1, tempfile.TemporaryDirectory() as tmp_out2:
            result1 = build_simulation_bundle(
                repo_root=self.repo_root,
                output_dir=Path(tmp_out1),
                catalog=self.catalog,
            )
            result2 = build_simulation_bundle(
                repo_root=self.repo_root,
                output_dir=Path(tmp_out2),
                catalog=self.catalog,
            )

            self.assertEqual(
                result1.sha256,
                result2.sha256,
                "Consecutive builds from identical source must produce identical SHA-256 digests.",
            )
            self.assertEqual(result1.compressed_bytes, result2.compressed_bytes)
            self.assertEqual(result1.unpacked_bytes, result2.unpacked_bytes)

    def test_bundle_rejects_forbidden_segments_and_extensions(self):
        from tools.release.simulation_bundle import build_simulation_bundle

        with tempfile.TemporaryDirectory() as tmp_repo, tempfile.TemporaryDirectory() as tmp_out:
            repo = Path(tmp_repo)
            payload = repo / "payload"
            payload.mkdir()
            (payload / "required.txt").write_text("required", encoding="utf-8")
            (payload / "LICENSE").write_text("license", encoding="utf-8")
            (payload / "UPSTREAM.md").write_text("upstream", encoding="utf-8")
            forbidden = repo / "payload" / "build" / "generated.o"
            forbidden.parent.mkdir(parents=True)
            forbidden.write_bytes(b"binary")
            bad_catalog = _small_catalog()
            bad_catalog["include_paths"] = [
                "payload/required.txt",
                "payload/LICENSE",
                "payload/UPSTREAM.md",
                "payload/build/generated.o",
            ]

            with self.assertRaisesRegex(ValueError, "forbidden"):
                build_simulation_bundle(repo, Path(tmp_out), bad_catalog)

    def test_builder_rejects_symbolic_links_in_included_tree(self):
        from tools.release.simulation_bundle import build_simulation_bundle

        with tempfile.TemporaryDirectory() as tmp_repo, tempfile.TemporaryDirectory() as tmp_out:
            repo = Path(tmp_repo)
            payload = repo / "payload"
            payload.mkdir()
            (payload / "required.txt").write_text("required", encoding="utf-8")
            (payload / "LICENSE").write_text("license", encoding="utf-8")
            (payload / "UPSTREAM.md").write_text("upstream", encoding="utf-8")
            os.symlink(payload / "required.txt", payload / "linked.txt")

            with self.assertRaisesRegex(ValueError, "Symbolic link"):
                build_simulation_bundle(repo, Path(tmp_out), _small_catalog())

    def test_inspection_rejects_undeclared_archive_member(self):
        from tools.release.simulation_bundle import inspect_simulation_bundle

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_test_bundle(
                Path(tmp_dir),
                undeclared_files={"payload/not-in-manifest.txt": b"surprise"},
            )
            inspection = inspect_simulation_bundle(path, _small_catalog())

        self.assertFalse(inspection.valid)
        self.assertTrue(any("not declared" in error.lower() for error in inspection.errors))

    def test_inspection_rejects_duplicate_and_link_members(self):
        from tools.release.simulation_bundle import inspect_simulation_bundle

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_test_bundle(
                Path(tmp_dir),
                duplicate_name="payload/required.txt",
                symlink_name="payload/linked.txt",
            )
            inspection = inspect_simulation_bundle(path, _small_catalog())

        self.assertFalse(inspection.valid)
        self.assertTrue(any("duplicate" in error.lower() for error in inspection.errors))
        self.assertTrue(any("member type" in error.lower() for error in inspection.errors))

    def test_inspection_rejects_manifest_metadata_mismatch(self):
        from tools.release.simulation_bundle import inspect_simulation_bundle

        files = {
            "payload/required.txt": b"required\n",
            "payload/LICENSE": b"fixture license\n",
            "payload/UPSTREAM.md": b"fixture provenance\n",
        }
        bad_files = {
            name: {"sha256": _sha256(data), "size": len(data), "mode": "0o644"}
            for name, data in files.items()
        }
        bad_files["payload/required.txt"]["size"] = 999
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_test_bundle(
                Path(tmp_dir),
                files=files,
                manifest_updates={
                    "total_files": 99,
                    "unpacked_bytes": 999,
                    "files": bad_files,
                },
            )
            inspection = inspect_simulation_bundle(path, _small_catalog())

        self.assertFalse(inspection.valid)
        joined = "\n".join(inspection.errors).lower()
        self.assertIn("total_files", joined)
        self.assertIn("unpacked_bytes", joined)
        self.assertIn("size mismatch", joined)

    def test_inspection_rejects_unsubstantiated_upstream_metadata(self):
        from tools.release.simulation_bundle import inspect_simulation_bundle

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_test_bundle(Path(tmp_dir))
            inspection = inspect_simulation_bundle(path, _small_catalog())

        self.assertFalse(inspection.valid)
        joined = "\n".join(inspection.errors).lower()
        self.assertIn("upstream revision", joined)
        self.assertIn("upstream repository", joined)

    def test_builder_cli_bootstraps_repo_source_in_clean_venv(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            venv_dir = root / "venv"
            output_dir = root / "output"
            venv.create(venv_dir, with_pip=False)
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [
                    str(venv_dir / "bin" / "python"),
                    "-m",
                    "tools.release.simulation_bundle",
                    "--repo-root",
                    str(self.repo_root),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=self.repo_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("ModuleNotFoundError", result.stderr)
            self.assertTrue((output_dir / "fireclaw-sim-turtlebot3-burger-v1.tar.gz").is_file())
