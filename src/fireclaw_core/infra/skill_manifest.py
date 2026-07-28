from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fireclaw_core.execution.skills import RISK_LEVELS, Skill, create_subprocess_skill


def load_subprocess_skill_from_manifest(path: str | Path) -> Skill:
    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    if not isinstance(manifest, dict):
        raise ValueError("Skill manifest must be a JSON object.")

    name = _required_string(manifest, "name")
    description = _required_string(manifest, "description")
    runtime = _required_string(manifest, "runtime")
    if runtime != "subprocess":
        raise ValueError("Only runtime='subprocess' manifests are supported in this loader.")

    command = manifest.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError("Skill manifest field 'command' must be a non-empty list of strings.")
    command = [sys.executable if item == "{python}" else item for item in command]

    timeout_seconds = manifest.get("timeout_seconds", 30.0)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("Skill manifest field 'timeout_seconds' must be a positive number.")

    dry_run_only = manifest.get("dry_run_only", True)
    if not isinstance(dry_run_only, bool):
        raise ValueError("Skill manifest field 'dry_run_only' must be a boolean.")

    max_attempts = manifest.get("max_attempts", 1)
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("Skill manifest field 'max_attempts' must be a positive integer.")

    idempotent = manifest.get("idempotent", False)
    if not isinstance(idempotent, bool):
        raise ValueError("Skill manifest field 'idempotent' must be a boolean.")
    if max_attempts > 1 and not idempotent:
        raise ValueError("Skill manifest field 'idempotent' must be true when max_attempts > 1.")

    required_sensors = _optional_string_list(manifest, "required_sensors")
    failure_categories = _optional_string_list(manifest, "failure_categories")

    allow_real_robot = manifest.get("allow_real_robot", False)
    if not isinstance(allow_real_robot, bool):
        raise ValueError("Skill manifest field 'allow_real_robot' must be a boolean.")
    if allow_real_robot and dry_run_only:
        raise ValueError("Skill manifest field 'allow_real_robot' conflicts with dry_run_only=true.")

    input_schema = _optional_input_schema(manifest)
    risk_level = _optional_risk_level(manifest)

    return create_subprocess_skill(
        name=name,
        description=description,
        command=command,
        timeout_seconds=float(timeout_seconds),
        cwd=manifest_path.parent,
        dry_run_only=dry_run_only,
        max_attempts=max_attempts,
        idempotent=idempotent,
        required_sensors=required_sensors,
        failure_categories=failure_categories,
        allow_real_robot=allow_real_robot,
        input_schema=input_schema,
        risk_level=risk_level,
    )


def _required_string(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill manifest field '{key}' must be a non-empty string.")
    return value


def _optional_string_list(manifest: dict[str, Any], key: str) -> list[str]:
    value = manifest.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"Skill manifest field '{key}' must be a list of non-empty strings.")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"Skill manifest field '{key}' must be a list of non-empty strings.")
    return list(value)


def _optional_input_schema(manifest: dict[str, Any]) -> dict[str, Any] | None:
    value = manifest.get("input_schema")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Skill manifest field 'input_schema' must be an object schema.")
    if value.get("type") != "object":
        raise ValueError("Skill manifest field 'input_schema' must have type='object'.")
    return dict(value)


def _optional_risk_level(manifest: dict[str, Any]) -> str:
    value = manifest.get("risk_level", "low")
    if not isinstance(value, str) or value not in RISK_LEVELS:
        allowed = ", ".join(sorted(RISK_LEVELS))
        raise ValueError(f"Skill manifest field 'risk_level' must be one of: {allowed}.")
    return value
