from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.robot import RobotActionResult


@dataclass(frozen=True)
class SubprocessSkillRunner:
    command: list[str]
    timeout_seconds: float = 30.0
    cwd: str | Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def run(self, inputs: dict[str, Any]) -> RobotActionResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(inputs, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                cwd=self.cwd,
                env=self.env or None,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error=f"Subprocess skill timed out after {self.timeout_seconds} seconds.",
            )
        except OSError as exc:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error=str(exc),
            )

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={"returncode": completed.returncode},
                timestamp=timestamp,
                error=f"Subprocess skill exited with code {completed.returncode}{detail}",
            )

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={"stdout": completed.stdout},
                timestamp=timestamp,
                error=f"Subprocess skill returned invalid JSON: {exc.msg}",
            )

        if not isinstance(payload, dict):
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={"stdout": completed.stdout},
                timestamp=timestamp,
                error="Subprocess skill JSON response must be an object.",
            )

        data = payload.get("data", {})
        if not isinstance(data, dict):
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error="Subprocess skill response field 'data' must be an object.",
            )

        error = payload.get("error")
        ok = bool(payload.get("ok", False))
        return RobotActionResult(
            ok=ok,
            status="succeeded" if ok else "failed",
            robot_id="external",
            mode="subprocess",
            action="subprocess_skill",
            dry_run=True,
            data=data,
            timestamp=timestamp,
            error=str(error) if error else None,
        )
