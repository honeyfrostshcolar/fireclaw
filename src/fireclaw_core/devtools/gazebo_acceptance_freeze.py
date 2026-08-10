"""Validate the versioned validation/test Gazebo acceptance scenario set."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


FROZEN_SUITE_SCHEMA_VERSION = "fireclaw.gazebo-acceptance-suite/v1"
FROZEN_SELECTION_SCHEMA_VERSION = (
    "fireclaw.gazebo-frozen-selection/v1"
)
_SCENARIO_SCHEMA_VERSION = "fireclaw.gazebo-acceptance/v1"
_SCENARIO_TYPES = {
    "success",
    "cancel",
    "timeout",
    "abort",
    "stall_recover",
    "stall_escalate",
}
_REQUIRED_ASSETS = {
    "launch",
    "robot_description",
    "collision_monitor",
    "world",
    "map",
    "map_image",
    "ros1_config",
}
_EXPECTED_SCENARIO_IDS = {
    "turtlebot3-move-base-success",
    "turtlebot3-move-base-cancel",
    "turtlebot3-move-base-timeout",
    "turtlebot3-move-base-abort",
    "turtlebot3-move-base-stall-recover",
    "turtlebot3-move-base-stall-escalate",
}


def load_frozen_suite(
    suite_path: str | Path,
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=True)
    suite = _read_yaml(Path(suite_path), root, "frozen suite")
    if suite.get("schema_version") != FROZEN_SUITE_SCHEMA_VERSION:
        raise ValueError("unsupported frozen Gazebo acceptance suite schema")
    if suite.get("lane") != "ros_gazebo_system":
        raise ValueError("frozen suite lane must be ros_gazebo_system")
    if not isinstance(suite.get("suite_id"), str) or not suite["suite_id"]:
        raise ValueError("frozen suite requires suite_id")
    if not isinstance(suite.get("suite_version"), str) or not suite[
        "suite_version"
    ]:
        raise ValueError("frozen suite requires suite_version")
    _validate_target_contract(suite.get("target_contract"))
    _validate_seed_policy(suite.get("seed_policy"))
    repeat_policy = suite.get("repeat_policy")
    if not isinstance(repeat_policy, Mapping):
        raise ValueError("frozen suite requires repeat_policy")
    for split in ("validation", "test"):
        values = repeat_policy.get(split)
        if not isinstance(values, Mapping):
            raise ValueError(f"repeat_policy.{split} must be an object")
        indices = values.get("repeat_indices")
        if (
            not isinstance(indices, list)
            or not indices
            or any(
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                for index in indices
            )
        ):
            raise ValueError(
                f"repeat_policy.{split}.repeat_indices must be non-empty "
                "non-negative integers"
            )
    shared_assets = _validate_assets(
        suite.get("shared_assets"),
        root,
        "shared_assets",
    )
    scenarios_raw = suite.get("scenarios")
    if not isinstance(scenarios_raw, list):
        raise ValueError("frozen suite scenarios must be a list")
    if len(scenarios_raw) != len(_EXPECTED_SCENARIO_IDS):
        raise ValueError("frozen suite must contain exactly six task scenarios")
    scenarios: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_entry in enumerate(scenarios_raw):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"scenarios[{index}] must be an object")
        entry = dict(raw_entry)
        scenario_id = _string(entry, "scenario_id", f"scenarios[{index}]")
        if scenario_id in seen_ids:
            raise ValueError(f"duplicate frozen scenario_id: {scenario_id}")
        seen_ids.add(scenario_id)
        if scenario_id not in _EXPECTED_SCENARIO_IDS:
            raise ValueError(f"unexpected frozen scenario_id: {scenario_id}")
        scenario_type = _string(
            entry,
            "scenario_type",
            f"scenarios[{index}]",
        )
        if scenario_type not in _SCENARIO_TYPES:
            raise ValueError(f"unsupported frozen scenario_type: {scenario_type}")
        scenario_version = _string(
            entry,
            "scenario_version",
            f"scenarios[{index}]",
        )
        seed = entry.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError(f"scenarios[{index}].seed must be non-negative")
        relative_path = _relative_path(entry, "path", f"scenarios[{index}]")
        scenario_path = (root / relative_path).resolve(strict=True)
        _require_within(scenario_path, root, f"scenarios[{index}].path")
        expected_hash = _hash_string(entry.get("sha256"), "scenario sha256")
        actual_hash = sha256_file(scenario_path)
        if actual_hash != expected_hash:
            raise ValueError(
                f"frozen scenario hash mismatch for {scenario_id}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        source = _read_yaml(scenario_path, root, f"scenario {scenario_id}")
        if source.get("schema_version") != _SCENARIO_SCHEMA_VERSION:
            raise ValueError(f"scenario {scenario_id} has unsupported schema")
        for key, expected in (
            ("scenario_id", scenario_id),
            ("scenario_type", scenario_type),
            ("scenario_version", scenario_version),
            ("seed", seed),
        ):
            if source.get(key) != expected:
                raise ValueError(
                    f"frozen scenario {scenario_id} disagrees on {key}"
                )
        target = _validate_point_target(entry.get("target"), scenario_id)
        source_goal = source.get("goal")
        if not isinstance(source_goal, Mapping):
            raise ValueError(f"scenario {scenario_id} requires goal")
        for key in ("frame_id", "x", "y", "yaw"):
            if source_goal.get(key) != target[key]:
                raise ValueError(
                    f"frozen scenario {scenario_id} target mismatch: {key}"
                )
        expected = source.get("expected")
        if not isinstance(expected, Mapping):
            raise ValueError(f"scenario {scenario_id} requires expected")
        if expected.get("terminal_status") != entry.get(
            "expected_terminal_outcome"
        ):
            raise ValueError(
                f"frozen scenario {scenario_id} terminal outcome mismatch"
            )
        if expected.get("actionlib_terminal_statuses") != entry.get(
            "actionlib_terminal_statuses"
        ):
            raise ValueError(
                f"frozen scenario {scenario_id} actionlib status mismatch"
            )
        source_assets = source.get("assets")
        if not isinstance(source_assets, Mapping):
            raise ValueError(f"scenario {scenario_id} requires assets")
        for label, asset in shared_assets.items():
            if source_assets.get(label) != asset["path"]:
                raise ValueError(
                    f"scenario {scenario_id} asset path mismatch: {label}"
                )
        entry["path"] = relative_path
        entry["sha256"] = expected_hash
        entry["target"] = target
        scenarios.append(entry)
    if seen_ids != _EXPECTED_SCENARIO_IDS:
        raise ValueError("frozen suite scenario set is incomplete")
    scenario_ids = [entry["scenario_id"] for entry in scenarios]
    for split in ("validation", "test"):
        selected = repeat_policy[split].get("scenario_ids")
        if selected is None:
            repeat_policy[split] = {
                **dict(repeat_policy[split]),
                "scenario_ids": scenario_ids,
            }
        elif selected != scenario_ids:
            raise ValueError(
                f"repeat_policy.{split}.scenario_ids must match frozen order"
            )
    normalized = {
        "schema_version": FROZEN_SUITE_SCHEMA_VERSION,
        "suite_id": suite["suite_id"],
        "suite_version": suite["suite_version"],
        "lane": suite["lane"],
        "target_contract": dict(suite["target_contract"]),
        "seed_policy": dict(suite["seed_policy"]),
        "repeat_policy": repeat_policy,
        "shared_assets": shared_assets,
        "runtime_capture": dict(suite.get("runtime_capture") or {}),
        "scenarios": scenarios,
        "suite_path": _portable_path(Path(suite_path).resolve(), root),
        "suite_sha256": sha256_file(Path(suite_path).resolve()),
    }
    return normalized


def verify_frozen_selection(
    suite_path: str | Path,
    scenario_path: str | Path,
    *,
    split: str,
    repo_root: str | Path,
) -> dict[str, Any]:
    suite = load_frozen_suite(suite_path, repo_root=repo_root)
    if split not in {"validation", "test"}:
        raise ValueError("frozen acceptance split must be validation or test")
    root = Path(repo_root).resolve(strict=True)
    selected_path = Path(scenario_path).resolve(strict=True)
    _require_within(selected_path, root, "selected scenario")
    selected_relative = _portable_path(selected_path, root)
    selected = next(
        (
            entry
            for entry in suite["scenarios"]
            if entry["path"] == selected_relative
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            f"scenario is not part of frozen {split} suite: "
            f"{selected_relative}"
        )
    if selected["scenario_id"] not in suite["repeat_policy"][split][
        "scenario_ids"
    ]:
        raise ValueError(
            f"scenario is not selected for frozen {split}: "
            f"{selected['scenario_id']}"
        )
    return frozen_selection_artifact(
        suite,
        selected,
        split=split,
        repeat_indices=suite["repeat_policy"][split]["repeat_indices"],
    )


def frozen_selection_artifact(
    suite: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    split: str,
    repeat_indices: list[int],
) -> dict[str, Any]:
    return {
        "schema_version": FROZEN_SELECTION_SCHEMA_VERSION,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "split": split,
        "repeat_indices": list(repeat_indices),
        "suite": dict(suite),
        "selected_scenario": dict(selected),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Gazebo acceptance scenario against the frozen suite"
    )
    parser.add_argument("--suite", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args(argv)
    try:
        artifact = verify_frozen_selection(
            args.suite,
            args.scenario,
            split=args.split,
            repo_root=args.repo_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False))
        return 1
    print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _validate_target_contract(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("frozen suite requires target_contract")
    if value.get("type") != "point" or value.get("frame_id") != "map":
        raise ValueError("frozen suite target contract must be absolute map point")


def _validate_seed_policy(value: Any) -> None:
    if not isinstance(value, Mapping) or value.get("kind") != "fixed_per_scenario":
        raise ValueError("frozen suite seed policy must be fixed_per_scenario")
    seeds = value.get("values")
    if seeds != [0]:
        raise ValueError("frozen suite currently requires seed [0]")


def _validate_assets(
    value: Any,
    root: Path,
    label: str,
) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_ASSETS:
        raise ValueError(
            f"{label} must contain exactly {sorted(_REQUIRED_ASSETS)}"
        )
    normalized: dict[str, dict[str, str]] = {}
    for asset_label in sorted(_REQUIRED_ASSETS):
        item = value.get(asset_label)
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}.{asset_label} must be an object")
        relative = _relative_path(item, "path", f"{label}.{asset_label}")
        path = (root / relative).resolve(strict=True)
        _require_within(path, root, f"{label}.{asset_label}")
        expected = _hash_string(
            item.get("sha256"),
            f"{label}.{asset_label}.sha256",
        )
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"{label}.{asset_label} hash mismatch: expected {expected}, "
                f"got {actual}"
            )
        normalized[asset_label] = {
            "path": relative,
            "sha256": expected,
        }
    return normalized


def _validate_point_target(value: Any, scenario_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"frozen scenario {scenario_id} requires point target")
    if value.get("type") != "point" or value.get("frame_id") != "map":
        raise ValueError(f"frozen scenario {scenario_id} target must be map point")
    result = {
        "type": "point",
        "frame_id": "map",
        "x": value.get("x"),
        "y": value.get("y"),
        "yaw": value.get("yaw"),
    }
    for key in ("x", "y", "yaw"):
        if isinstance(result[key], bool) or not isinstance(
            result[key],
            (int, float),
        ):
            raise ValueError(
                f"frozen scenario {scenario_id} target {key} must be numeric"
            )
    return result


def _read_yaml(path: Path, root: Path, label: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    _require_within(resolved, root, label)
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a YAML object")
    return raw


def _relative_path(
    value: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label}.{key} must be a non-empty path")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label}.{key} must be repository-relative")
    return path.as_posix()


def _hash_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a SHA-256 hex string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a SHA-256 hex string") from exc
    return value.lower()


def _string(value: Mapping[str, Any], key: str, label: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string")
    return raw.strip()


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside repository") from exc


def _portable_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "FROZEN_SELECTION_SCHEMA_VERSION",
    "FROZEN_SUITE_SCHEMA_VERSION",
    "frozen_selection_artifact",
    "load_frozen_suite",
    "sha256_file",
    "verify_frozen_selection",
]


if __name__ == "__main__":
    raise SystemExit(main())
