"""Deterministic simulation bundle builder and integrity inspector."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile
from typing import Any, Mapping

DETERMINISTIC_MTIME = 1700000000  # 2023-11-14 22:13:20 UTC
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_PROVENANCE_EVIDENCE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class BundleResult:
    archive_path: Path
    sha256: str
    file_count: int
    compressed_bytes: int
    unpacked_bytes: int
    manifest: dict[str, Any]


@dataclass(frozen=True)
class BundleInspection:
    valid: bool
    errors: list[str]
    sha256: str
    file_count: int
    compressed_bytes: int
    unpacked_bytes: int
    manifest: dict[str, Any]


def _compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_path_forbidden(
    rel_posix: str,
    forbidden_segments: set[str],
    forbidden_suffixes: set[str],
) -> tuple[bool, str]:
    parts = set(rel_posix.split("/"))
    matched_segments = parts.intersection(forbidden_segments)
    if matched_segments:
        return True, f"contains forbidden segment: {matched_segments}"

    for suff in forbidden_suffixes:
        if rel_posix.endswith(suff):
            return True, f"ends with forbidden suffix: {suff}"

    return False, ""


def _is_safe_archive_path(name: str) -> bool:
    if not name or name != name.strip() or name.startswith("/") or "\\" in name or "\x00" in name:
        return False
    if name.endswith("/") or "//" in name:
        return False
    parsed_path = PurePosixPath(name)
    parts = parsed_path.parts
    return (
        bool(parts)
        and parsed_path.as_posix() == name
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _path_is_included(path: str, include_paths: list[str]) -> bool:
    return any(path == included or (included.endswith("/") and path.startswith(included)) for included in include_paths)


def _assert_no_symlink_components(repo_root: Path, candidate: Path, declared_path: str) -> None:
    try:
        relative = candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Include path '{declared_path}' escapes repository root") from exc
    current = repo_root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"Symbolic link is forbidden in simulation bundle include path: {declared_path}")


def _normalized_mode(path: Path) -> int:
    permissions = path.stat().st_mode
    return 0o755 if permissions & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else 0o644


def _validate_catalog(catalog: Mapping[str, Any]) -> None:
    from fireclaw_core.resources import validate_simulation_bundle_catalog

    validate_simulation_bundle_catalog(catalog)


def build_simulation_bundle(
    repo_root: Path,
    output_dir: Path,
    catalog: Mapping[str, Any],
) -> BundleResult:
    """Build a deterministic tar.gz simulation bundle strictly adhering to catalog allowlist."""
    _validate_catalog(catalog)
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    if not repo_root.is_dir():
        raise FileNotFoundError(f"Repository root does not exist or is not a directory: {repo_root}")
    output_dir.mkdir(parents=True, exist_ok=True)

    bundle_id = str(catalog["bundle_id"])
    bundle_version = str(catalog["bundle_version"])
    include_paths = list(catalog["include_paths"])
    required_paths = list(catalog["required_paths"])
    provenance_paths = list(catalog["provenance_required_paths"])
    forbidden_segments = set(catalog.get("forbidden_path_segments", []))
    forbidden_suffixes = set(catalog.get("forbidden_file_suffixes", []))
    max_compressed = int(catalog["size_budget_compressed_bytes"])
    max_unpacked = int(catalog["size_budget_unpacked_bytes"])

    files_to_pack: dict[str, Path] = {}

    for inc in include_paths:
        canonical_inc = inc[:-1] if inc.endswith("/") else inc
        forbidden, reason = _is_path_forbidden(canonical_inc, forbidden_segments, forbidden_suffixes)
        if forbidden:
            raise ValueError(f"Include path '{inc}' is forbidden ({reason})")

        target_declared = repo_root.joinpath(*PurePosixPath(canonical_inc).parts)
        _assert_no_symlink_components(repo_root, target_declared, inc)
        if not target_declared.exists():
            raise FileNotFoundError(f"Declared include path does not exist: {inc} ({target_declared})")
        target = target_declared.resolve(strict=True)
        try:
            target.relative_to(repo_root)
        except ValueError:
            raise ValueError(f"Include path '{inc}' resolves outside repository root: {target}")

        if target.is_file():
            if not stat.S_ISREG(target.stat().st_mode):
                raise ValueError(f"Special files are forbidden in simulation bundle: {inc}")
            rel_posix = PurePosixPath(canonical_inc).as_posix()
            forbidden, reason = _is_path_forbidden(rel_posix, forbidden_segments, forbidden_suffixes)
            if forbidden:
                raise ValueError(f"Include file '{inc}' is forbidden ({reason})")
            files_to_pack[rel_posix] = target

        elif target.is_dir():
            for root, dirs, filenames in os.walk(target, followlinks=False):
                root_path = Path(root)
                kept_dirs: list[str] = []
                for directory_name in sorted(dirs):
                    directory_path = root_path / directory_name
                    rel_posix = PurePosixPath(directory_path.relative_to(repo_root)).as_posix()
                    if directory_path.is_symlink():
                        raise ValueError(f"Symbolic link directory is forbidden in simulation bundle: {rel_posix}")
                    forbidden, _ = _is_path_forbidden(rel_posix, forbidden_segments, forbidden_suffixes)
                    if not forbidden:
                        kept_dirs.append(directory_name)
                dirs[:] = kept_dirs

                for filename in sorted(filenames):
                    file_path = root_path / filename
                    rel_posix = PurePosixPath(file_path.relative_to(repo_root)).as_posix()
                    if file_path.is_symlink():
                        raise ValueError(f"Symbolic link file is forbidden in simulation bundle: {rel_posix}")
                    if not stat.S_ISREG(file_path.stat().st_mode):
                        raise ValueError(f"Special file is forbidden in simulation bundle: {rel_posix}")
                    forbidden, _ = _is_path_forbidden(rel_posix, forbidden_segments, forbidden_suffixes)
                    if not forbidden:
                        files_to_pack[rel_posix] = file_path
        else:
            raise ValueError(f"Include path is neither a regular file nor directory: {inc}")

    missing_required = [req for req in required_paths + provenance_paths if req not in files_to_pack]
    if missing_required:
        raise ValueError(f"Simulation bundle missing required or provenance paths: {missing_required}")

    sorted_paths = sorted(files_to_pack.keys())
    manifest_files: dict[str, dict[str, Any]] = {}
    payload_unpacked_bytes = 0
    source_fingerprints: dict[str, tuple[int, int]] = {}

    for rel_posix in sorted_paths:
        src_path = files_to_pack[rel_posix]
        before = src_path.stat()
        file_size = before.st_size
        payload_unpacked_bytes += file_size
        if payload_unpacked_bytes > max_unpacked:
            raise ValueError(
                f"Simulation bundle payload size {payload_unpacked_bytes} exceeds budget {max_unpacked} bytes."
            )
        file_sha256 = _compute_file_sha256(src_path)
        after = src_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"Source file changed while hashing simulation bundle: {rel_posix}")
        source_fingerprints[rel_posix] = (after.st_size, after.st_mtime_ns)
        norm_mode = _normalized_mode(src_path)

        manifest_files[rel_posix] = {
            "sha256": file_sha256,
            "size": file_size,
            "mode": oct(norm_mode),
        }

    manifest_payload = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "bundle_version": bundle_version,
        "total_files": len(sorted_paths),
        "unpacked_bytes": payload_unpacked_bytes,
        "files": manifest_files,
    }
    manifest_bytes = (json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    unpacked_total = payload_unpacked_bytes + len(manifest_bytes)

    if unpacked_total > max_unpacked:
        raise ValueError(
            f"Simulation bundle unpacked size {unpacked_total} exceeds budget {max_unpacked} bytes."
        )

    archive_name = f"fireclaw-sim-{bundle_id}.tar.gz"
    archive_path = output_dir / archive_name
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{archive_name}.", suffix=".tmp", dir=output_dir)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("wb") as f_out:
            with gzip.GzipFile(filename="", mode="wb", fileobj=f_out, mtime=DETERMINISTIC_MTIME) as gz_out:
                with tarfile.TarFile(mode="w", fileobj=gz_out, format=tarfile.PAX_FORMAT) as tar:
                    manifest_info = tarfile.TarInfo(name="manifest.json")
                    manifest_info.size = len(manifest_bytes)
                    manifest_info.mtime = DETERMINISTIC_MTIME
                    manifest_info.mode = 0o644
                    manifest_info.uid = 0
                    manifest_info.gid = 0
                    manifest_info.uname = "root"
                    manifest_info.gname = "root"
                    tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

                    for rel_posix in sorted_paths:
                        src_path = files_to_pack[rel_posix]
                        current = src_path.stat()
                        if source_fingerprints[rel_posix] != (current.st_size, current.st_mtime_ns):
                            raise RuntimeError(f"Source file changed while writing simulation bundle: {rel_posix}")
                        t_info = tarfile.TarInfo(name=rel_posix)
                        t_info.size = current.st_size
                        t_info.mtime = DETERMINISTIC_MTIME
                        t_info.mode = int(manifest_files[rel_posix]["mode"], 8)
                        t_info.uid = 0
                        t_info.gid = 0
                        t_info.uname = "root"
                        t_info.gname = "root"
                        with src_path.open("rb") as source_handle:
                            tar.addfile(t_info, source_handle)

        compressed_size = temporary_path.stat().st_size
        if compressed_size > max_compressed:
            raise ValueError(
                f"Simulation bundle compressed size {compressed_size} exceeds budget {max_compressed} bytes."
            )
        archive_sha256 = _compute_file_sha256(temporary_path)
        os.replace(temporary_path, archive_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return BundleResult(
        archive_path=archive_path,
        sha256=archive_sha256,
        file_count=len(sorted_paths),
        compressed_bytes=compressed_size,
        unpacked_bytes=unpacked_total,
        manifest=manifest_payload,
    )


def inspect_simulation_bundle(
    path: Path,
    catalog: Mapping[str, Any],
) -> BundleInspection:
    """Inspect and verify simulation bundle archive against catalog rules and internal manifest."""
    errors: list[str] = []
    path = path.resolve()

    try:
        _validate_catalog(catalog)
    except (TypeError, ValueError) as exc:
        errors.append(f"Invalid simulation bundle catalog: {exc}")

    if not path.is_file():
        return BundleInspection(
            valid=False,
            errors=errors + [f"Bundle file not found: {path}"],
            sha256="",
            file_count=0,
            compressed_bytes=0,
            unpacked_bytes=0,
            manifest={},
        )

    if errors:
        return BundleInspection(
            valid=False,
            errors=errors,
            sha256="",
            file_count=0,
            compressed_bytes=path.stat().st_size,
            unpacked_bytes=0,
            manifest={},
        )

    compressed_bytes = path.stat().st_size

    max_compressed = int(catalog.get("size_budget_compressed_bytes", 1))
    max_unpacked = int(catalog.get("size_budget_unpacked_bytes", 1))
    include_paths = list(catalog.get("include_paths", []))
    required_paths = set(catalog.get("required_paths", [])) | set(catalog.get("provenance_required_paths", []))
    provenance_evidence_paths = {
        path
        for source in catalog.get("upstream_sources", [])
        for path in source.get("evidence_paths", [])
    }
    forbidden_segments = set(catalog.get("forbidden_path_segments", []))
    forbidden_suffixes = set(catalog.get("forbidden_file_suffixes", []))

    expected_name = f"fireclaw-sim-{catalog.get('bundle_id', '')}.tar.gz"
    if path.name != expected_name:
        errors.append(f"Bundle filename mismatch: expected {expected_name}, got {path.name}")

    if compressed_bytes > max_compressed:
        errors.append(f"Compressed size {compressed_bytes} exceeds budget {max_compressed}")
        return BundleInspection(
            valid=False,
            errors=errors,
            sha256="",
            file_count=0,
            compressed_bytes=compressed_bytes,
            unpacked_bytes=0,
            manifest={},
        )

    archive_sha256 = _compute_file_sha256(path)

    manifest: dict[str, Any] = {}
    member_metadata: dict[str, dict[str, Any]] = {}
    manifest_bytes: bytes | None = None
    provenance_evidence: dict[str, bytes] = {}
    declared_unpacked_bytes = 0
    declared_evidence_bytes = 0
    observed_member_names: list[str] = []

    try:
        with tarfile.open(path, "r|gz") as tar:
            seen_names: set[str] = set()
            member_count = 0
            for member in tar:
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    errors.append(f"Archive member count exceeds limit {MAX_ARCHIVE_MEMBERS}")
                    break

                name = member.name
                duplicate = name in seen_names
                if duplicate:
                    errors.append(f"Duplicate archive member name: {name}")
                seen_names.add(name)
                observed_member_names.append(name)

                if member.size < 0:
                    errors.append(f"Archive member has negative size: {name}")
                    continue
                if declared_unpacked_bytes + member.size > max_unpacked:
                    errors.append(
                        f"Unpacked size would exceed budget {max_unpacked} while reading {name}"
                    )
                    break
                declared_unpacked_bytes += member.size

                if member.mtime != DETERMINISTIC_MTIME:
                    errors.append(
                        f"Non-deterministic mtime for {name}: expected {DETERMINISTIC_MTIME}, got {member.mtime}"
                    )
                if member.uid != 0 or member.gid != 0 or member.uname != "root" or member.gname != "root":
                    errors.append(f"Non-deterministic ownership metadata for archive member: {name}")

                if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}:
                    errors.append(f"Forbidden member type in archive: {name} (type={member.type})")
                    continue
                if not _is_safe_archive_path(name):
                    errors.append(f"Unsafe path in archive member: {name}")
                    continue
                if name == "manifest.json" and member.size > min(MAX_MANIFEST_BYTES, max_unpacked):
                    errors.append(f"manifest.json exceeds maximum size {min(MAX_MANIFEST_BYTES, max_unpacked)}")
                    continue
                if name in provenance_evidence_paths:
                    if declared_evidence_bytes + member.size > MAX_PROVENANCE_EVIDENCE_BYTES:
                        errors.append(
                            "Provenance evidence exceeds capture limit "
                            f"{MAX_PROVENANCE_EVIDENCE_BYTES} while reading {name}"
                        )
                        continue
                    declared_evidence_bytes += member.size

                extracted = tar.extractfile(member)
                if extracted is None:
                    errors.append(f"Could not read member: {name}")
                    continue
                digest = hashlib.sha256()
                chunks: list[bytes] | None = (
                    [] if name == "manifest.json" or name in provenance_evidence_paths else None
                )
                actual_size = 0
                while True:
                    chunk = extracted.read(min(1024 * 1024, member.size - actual_size + 1))
                    if not chunk:
                        break
                    actual_size += len(chunk)
                    if actual_size > member.size:
                        errors.append(f"Archive member yielded more bytes than declared: {name}")
                        break
                    digest.update(chunk)
                    if chunks is not None:
                        chunks.append(chunk)
                if actual_size != member.size:
                    errors.append(
                        f"Archive member size mismatch for {name}: header {member.size}, read {actual_size}"
                    )
                if not duplicate:
                    member_metadata[name] = {
                        "sha256": digest.hexdigest(),
                        "size": actual_size,
                        "mode": oct(member.mode & 0o777),
                    }
                    if chunks is not None:
                        captured = b"".join(chunks)
                        if name == "manifest.json":
                            manifest_bytes = captured
                        elif name in provenance_evidence_paths:
                            provenance_evidence[name] = captured
    except Exception as exc:
        return BundleInspection(
            valid=False,
            errors=errors + [f"Failed to read tar.gz archive: {exc}"],
            sha256=archive_sha256,
            file_count=0,
            compressed_bytes=compressed_bytes,
            unpacked_bytes=declared_unpacked_bytes,
            manifest={},
        )

    if "manifest.json" not in member_metadata or manifest_bytes is None:
        errors.append("Archive missing root manifest.json")
    else:
        try:
            def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                parsed: dict[str, Any] = {}
                for key, value in pairs:
                    if key in parsed:
                        raise ValueError(f"duplicate key: {key}")
                    parsed[key] = value
                return parsed

            parsed_manifest = json.loads(
                manifest_bytes.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
            )
            if not isinstance(parsed_manifest, dict):
                raise ValueError("manifest root must be an object")
            manifest = parsed_manifest
        except Exception as exc:
            errors.append(f"Invalid manifest.json JSON: {exc}")

    member_names = set(member_metadata) - {"manifest.json"}
    expected_member_order = ["manifest.json", *sorted(member_names)]
    if observed_member_names != expected_member_order:
        errors.append(
            "Archive member order is not deterministic: expected manifest.json followed by sorted payload paths"
        )
    manifest_header = member_metadata.get("manifest.json")
    if manifest_header is not None and manifest_header.get("mode") != "0o644":
        errors.append(
            f"Mode mismatch for manifest.json: expected 0o644, got {manifest_header.get('mode')}"
        )
    manifest_files_raw = manifest.get("files", {})
    if not isinstance(manifest_files_raw, dict):
        errors.append("Manifest field 'files' must be an object")
        manifest_files: dict[str, Any] = {}
    else:
        manifest_files = manifest_files_raw

    if manifest:
        if manifest.get("schema_version") != 1 or isinstance(manifest.get("schema_version"), bool):
            errors.append("Manifest schema_version must be integer 1")
        if manifest.get("bundle_id") != catalog.get("bundle_id"):
            errors.append(
                f"Manifest bundle_id mismatch: expected {catalog.get('bundle_id')}, got {manifest.get('bundle_id')}"
            )
        if manifest.get("bundle_version") != catalog.get("bundle_version"):
            errors.append(
                f"Manifest bundle_version mismatch: expected {catalog.get('bundle_version')}, got {manifest.get('bundle_version')}"
            )
        declared_total_files = manifest.get("total_files")
        if not isinstance(declared_total_files, int) or isinstance(declared_total_files, bool) or declared_total_files != len(member_names):
            errors.append(
                f"Manifest total_files mismatch: expected {len(member_names)}, got {declared_total_files}"
            )
        payload_bytes = sum(metadata["size"] for name, metadata in member_metadata.items() if name != "manifest.json")
        declared_unpacked = manifest.get("unpacked_bytes")
        if not isinstance(declared_unpacked, int) or isinstance(declared_unpacked, bool) or declared_unpacked != payload_bytes:
            errors.append(
                f"Manifest unpacked_bytes mismatch: expected {payload_bytes}, got {declared_unpacked}"
            )

    manifest_names = set(manifest_files)
    for undeclared in sorted(member_names - manifest_names):
        errors.append(f"Archive member not declared in manifest: {undeclared}")
    for missing in sorted(manifest_names - member_names):
        errors.append(f"Manifest declared file missing in archive: {missing}")

    for rel_path in sorted(manifest_names & member_names):
        file_meta = manifest_files[rel_path]
        if not isinstance(rel_path, str) or not _is_safe_archive_path(rel_path) or rel_path == "manifest.json":
            errors.append(f"Manifest contains unsafe file path: {rel_path!r}")
            continue
        if not isinstance(file_meta, Mapping):
            errors.append(f"Manifest metadata for {rel_path} must be an object")
            continue
        actual = member_metadata[rel_path]
        expected_sha256 = file_meta.get("sha256")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or actual["sha256"] != expected_sha256:
            errors.append(
                f"Checksum mismatch for {rel_path}: expected {expected_sha256}, got {actual['sha256']}"
            )
        expected_size = file_meta.get("size")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or actual["size"] != expected_size:
            errors.append(
                f"Size mismatch for {rel_path}: expected {expected_size}, got {actual['size']}"
            )
        expected_mode = file_meta.get("mode")
        if expected_mode not in {"0o644", "0o755"} or actual["mode"] != expected_mode:
            errors.append(
                f"Mode mismatch for {rel_path}: expected {expected_mode}, got {actual['mode']}"
            )

    for name in member_names:
        if not _path_is_included(name, include_paths):
            errors.append(f"Archive member is outside catalog include_paths: {name}")
        forbidden, reason = _is_path_forbidden(name, forbidden_segments, forbidden_suffixes)
        if forbidden:
            errors.append(f"Archive member '{name}' violates forbidden rules: {reason}")

    missing_reqs = required_paths - member_names
    if missing_reqs:
        errors.append(f"Archive missing required paths: {sorted(missing_reqs)}")

    for evidence_path in sorted(provenance_evidence_paths):
        evidence_bytes = provenance_evidence.get(evidence_path)
        if evidence_bytes is not None and not evidence_bytes.strip():
            errors.append(f"Provenance evidence file is empty: {evidence_path}")

    for source in catalog.get("upstream_sources", []):
        source_id = source["source_id"]
        evidence_blobs = [
            provenance_evidence[path]
            for path in source["evidence_paths"]
            if path in provenance_evidence
        ]
        combined_evidence = b"\n".join(evidence_blobs)
        revision = source["revision"].encode("ascii")
        repository = source["repository"].encode("utf-8")
        if revision not in combined_evidence:
            errors.append(
                f"Upstream revision for {source_id} is not present in declared evidence: {source['revision']}"
            )
        if repository not in combined_evidence:
            errors.append(
                f"Upstream repository for {source_id} is not present in declared evidence: {source['repository']}"
            )

    return BundleInspection(
        valid=len(errors) == 0,
        errors=errors,
        sha256=archive_sha256,
        file_count=len(member_names),
        compressed_bytes=compressed_bytes,
        unpacked_bytes=declared_unpacked_bytes,
        manifest=manifest,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="FireClaw simulation bundle builder CLI.")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--output-dir", required=True, help="Output directory for generated bundle")
    parser.add_argument("--bundle-id", default="turtlebot3-burger-v1", help="Bundle ID from package resources")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    source_root = repo_root / "src"
    if source_root.is_dir() and str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from fireclaw_core.resources import load_simulation_bundle_catalog

    catalog = load_simulation_bundle_catalog(args.bundle_id)
    result = build_simulation_bundle(
        repo_root=repo_root,
        output_dir=Path(args.output_dir),
        catalog=catalog,
    )
    print("Bundle built successfully:")
    print(f"  Path:        {result.archive_path}")
    print(f"  SHA-256:     {result.sha256}")
    print(f"  Files:       {result.file_count}")
    print(f"  Compressed:  {result.compressed_bytes} bytes ({result.compressed_bytes / (1024*1024):.2f} MiB)")
    print(f"  Unpacked:    {result.unpacked_bytes} bytes ({result.unpacked_bytes / (1024*1024):.2f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
