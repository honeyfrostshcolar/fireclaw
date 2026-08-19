"""Package-owned distribution resources for FireClaw."""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

try:
    import importlib.resources as importlib_resources
except ImportError:
    import importlib_resources  # type: ignore

__all__ = [
    "load_setup_template",
    "load_simulation_bundle_catalog",
    "validate_simulation_bundle_catalog",
]

_SETUP_TEMPLATE_FILES = {
    "gazebo_turtlebot3": "gazebo_turtlebot3.toml",
}

_SIMULATION_BUNDLE_CATALOG_FILES = {
    "turtlebot3-burger-v1": "turtlebot3-burger-v1.json",
}

_BUNDLE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:[a-z0-9_-]*[a-z0-9])?$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _resource_filename(resource_id: str, known_files: Mapping[str, str], kind: str) -> str:
    if not isinstance(resource_id, str) or resource_id not in known_files:
        raise FileNotFoundError(f"Unknown {kind} ID: {resource_id!r}")
    return known_files[resource_id]


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key in simulation bundle catalog: {key}")
        result[key] = value
    return result


def _validate_relative_path(value: Any, *, field: str, allow_trailing_slash: bool) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Catalog field '{field}' must contain non-empty POSIX paths")
    if value != value.strip() or "\\" in value or "\x00" in value:
        raise ValueError(f"Catalog field '{field}' contains a non-canonical path: {value!r}")
    if value.startswith("/") or ":" in value.split("/", 1)[0]:
        raise ValueError(f"Catalog field '{field}' contains an absolute path: {value!r}")
    if value.endswith("/") and not allow_trailing_slash:
        raise ValueError(f"Catalog field '{field}' requires file paths, not directories: {value!r}")

    path_without_suffix = value[:-1] if value.endswith("/") else value
    parsed_path = PurePosixPath(path_without_suffix)
    parts = parsed_path.parts
    if (
        not parts
        or parsed_path.as_posix() != path_without_suffix
        or any(part in {"", ".", ".."} for part in parts)
        or "//" in value
    ):
        raise ValueError(f"Catalog field '{field}' contains an unsafe path: {value!r}")
    if path_without_suffix == "manifest.json":
        raise ValueError("Catalog paths must not claim the bundle-owned manifest.json")
    return value


def _validate_path_list(
    catalog: Mapping[str, Any],
    field: str,
    *,
    allow_trailing_slash: bool,
    allow_empty: bool = False,
) -> list[str]:
    values = catalog.get(field)
    if not isinstance(values, list) or (not values and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"Catalog field '{field}' must be {qualifier}")
    normalized = [
        _validate_relative_path(value, field=field, allow_trailing_slash=allow_trailing_slash)
        for value in values
    ]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"Catalog field '{field}' contains duplicate paths")
    return normalized


def _path_is_included(path: str, include_paths: list[str]) -> bool:
    return any(path == included or (included.endswith("/") and path.startswith(included)) for included in include_paths)


def validate_simulation_bundle_catalog(
    catalog: Mapping[str, Any],
    *,
    expected_bundle_id: str | None = None,
) -> Mapping[str, Any]:
    """Validate the release catalog before it can influence filesystem or archive access."""
    if not isinstance(catalog, Mapping):
        raise ValueError("Simulation bundle catalog must be a JSON object")
    if catalog.get("schema_version") != 1 or isinstance(catalog.get("schema_version"), bool):
        raise ValueError("Catalog 'schema_version' must be integer 1")

    bundle_id = catalog.get("bundle_id")
    if not isinstance(bundle_id, str) or not _BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ValueError("Catalog 'bundle_id' must be a canonical lowercase hyphenated ID")
    if expected_bundle_id is not None and bundle_id != expected_bundle_id:
        raise ValueError(
            f"Catalog bundle_id mismatch: expected {expected_bundle_id!r}, got {bundle_id!r}"
        )

    bundle_version = catalog.get("bundle_version")
    if not isinstance(bundle_version, str) or not _SEMVER_RE.fullmatch(bundle_version):
        raise ValueError("Catalog 'bundle_version' must be a semantic version")
    compatibility = catalog.get("compatible_fireclaw_versions")
    if not isinstance(compatibility, str) or not compatibility.strip():
        raise ValueError("Catalog 'compatible_fireclaw_versions' must be a non-empty string")

    include_paths = _validate_path_list(catalog, "include_paths", allow_trailing_slash=True)
    required_paths = _validate_path_list(catalog, "required_paths", allow_trailing_slash=False)
    provenance_paths = _validate_path_list(
        catalog,
        "provenance_required_paths",
        allow_trailing_slash=False,
    )
    for field, paths in (
        ("required_paths", required_paths),
        ("provenance_required_paths", provenance_paths),
    ):
        uncovered = [path for path in paths if not _path_is_included(path, include_paths)]
        if uncovered:
            raise ValueError(f"Catalog field '{field}' contains paths outside include_paths: {uncovered}")

    forbidden_segments = catalog.get("forbidden_path_segments")
    if not isinstance(forbidden_segments, list) or not forbidden_segments:
        raise ValueError("Catalog 'forbidden_path_segments' must be a non-empty list")
    if any(
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        for value in forbidden_segments
    ):
        raise ValueError("Catalog 'forbidden_path_segments' contains an invalid segment")
    if len(forbidden_segments) != len(set(forbidden_segments)):
        raise ValueError("Catalog 'forbidden_path_segments' contains duplicates")

    forbidden_suffixes = catalog.get("forbidden_file_suffixes")
    if not isinstance(forbidden_suffixes, list) or not forbidden_suffixes:
        raise ValueError("Catalog 'forbidden_file_suffixes' must be a non-empty list")
    if any(not isinstance(value, str) or not value or "/" in value or "\\" in value for value in forbidden_suffixes):
        raise ValueError("Catalog 'forbidden_file_suffixes' contains an invalid suffix")
    if len(forbidden_suffixes) != len(set(forbidden_suffixes)):
        raise ValueError("Catalog 'forbidden_file_suffixes' contains duplicates")

    sources = catalog.get("upstream_sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Catalog 'upstream_sources' must be a non-empty list")
    source_ids: set[str] = set()
    referenced_evidence: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise ValueError(f"Catalog upstream_sources[{index}] must be an object")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not _SOURCE_ID_RE.fullmatch(source_id):
            raise ValueError(f"Catalog upstream_sources[{index}].source_id is invalid")
        if source_id in source_ids:
            raise ValueError(f"Catalog upstream_sources contains duplicate source_id: {source_id}")
        source_ids.add(source_id)
        repository = source.get("repository")
        if not isinstance(repository, str) or not repository.startswith("https://"):
            raise ValueError(f"Catalog upstream_sources[{index}].repository must use https://")
        revision = source.get("revision")
        if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
            raise ValueError(f"Catalog upstream_sources[{index}].revision must be a 40-character Git commit")
        license_id = source.get("license")
        if not isinstance(license_id, str) or not license_id.strip():
            raise ValueError(f"Catalog upstream_sources[{index}].license must be declared")
        evidence = source.get("evidence_paths")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"Catalog upstream_sources[{index}].evidence_paths must be a non-empty list")
        checked_evidence = [
            _validate_relative_path(
                value,
                field=f"upstream_sources[{index}].evidence_paths",
                allow_trailing_slash=False,
            )
            for value in evidence
        ]
        if len(checked_evidence) != len(set(checked_evidence)):
            raise ValueError(f"Catalog upstream_sources[{index}].evidence_paths contains duplicates")
        unknown = set(checked_evidence) - set(provenance_paths)
        if unknown:
            raise ValueError(
                f"Catalog upstream_sources[{index}].evidence_paths are not provenance_required_paths: {sorted(unknown)}"
            )
        referenced_evidence.update(checked_evidence)
    unreferenced = set(provenance_paths) - referenced_evidence
    if unreferenced:
        raise ValueError(f"Catalog provenance_required_paths are not attributed to an upstream source: {sorted(unreferenced)}")

    budgets: dict[str, int] = {}
    for field in ("size_budget_compressed_bytes", "size_budget_unpacked_bytes"):
        value = catalog.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"Catalog '{field}' must be a positive integer")
        budgets[field] = value
    if budgets["size_budget_compressed_bytes"] > budgets["size_budget_unpacked_bytes"]:
        raise ValueError("Compressed size budget must not exceed unpacked size budget")

    return catalog


def load_setup_template(template_id: str) -> str:
    """Load a package-owned setup template by ID (e.g. 'gazebo_turtlebot3')."""
    filename = _resource_filename(template_id, _SETUP_TEMPLATE_FILES, "setup template")

    try:
        resource_dir = importlib_resources.files("fireclaw_core.resources.setup_templates")
        target_file = resource_dir.joinpath(filename)
        if not target_file.is_file():
            raise FileNotFoundError(f"Setup template '{template_id}' ({filename}) not found in package resources.")
        return target_file.read_text(encoding="utf-8")
    except (TypeError, AttributeError, ModuleNotFoundError) as exc:
        raise FileNotFoundError(f"Could not load setup template '{template_id}': {exc}") from exc


def load_simulation_bundle_catalog(bundle_id: str) -> Mapping[str, Any]:
    """Load a simulation bundle catalog JSON by bundle ID (e.g. 'turtlebot3-burger-v1')."""
    filename = _resource_filename(
        bundle_id,
        _SIMULATION_BUNDLE_CATALOG_FILES,
        "simulation bundle catalog",
    )

    try:
        resource_dir = importlib_resources.files("fireclaw_core.resources.simulation_bundles")
        target_file = resource_dir.joinpath(filename)
        if not target_file.is_file():
            raise FileNotFoundError(f"Simulation bundle catalog '{bundle_id}' ({filename}) not found in package resources.")
        raw_text = target_file.read_text(encoding="utf-8")
        catalog = json.loads(raw_text, object_pairs_hook=_reject_duplicate_json_keys)
        return validate_simulation_bundle_catalog(catalog, expected_bundle_id=bundle_id)
    except (TypeError, AttributeError, ModuleNotFoundError) as exc:
        raise FileNotFoundError(f"Could not load simulation bundle catalog '{bundle_id}': {exc}") from exc
