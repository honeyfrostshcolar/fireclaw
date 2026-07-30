from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from fireclaw_core.agent.robot import RobotActionResult


class SandboxedSkillExecutor(Protocol):
    """Trusted boundary used by legacy executable Tool manifests."""

    def stage_skill(self, source_dir: str | Path, *, skill_name: str) -> str:
        """Materialize a manifest directory and return a sandbox-relative cwd."""

    def execute_process(
        self,
        *,
        argv: list[str],
        cwd: str,
        stdin_text: str | None,
        timeout_seconds: float,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Execute argv inside the configured process sandbox."""


@dataclass(frozen=True)
class SubprocessSkillRunner:
    """Compatibility runner that can execute only through a trusted sandbox."""

    command: list[str]
    executor: SandboxedSkillExecutor
    timeout_seconds: float = 30.0
    cwd: str = "."

    def run(
        self,
        inputs: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotActionResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        cancellation_requested = cancellation_requested or (lambda: False)
        if cancellation_requested():
            return self._cancelled(timestamp)

        try:
            completed = self.executor.execute_process(
                argv=list(self.command),
                cwd=self.cwd,
                stdin_text=json.dumps(inputs, ensure_ascii=False),
                timeout_seconds=self.timeout_seconds,
                cancellation_requested=cancellation_requested,
            )
        except Exception as exc:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="sandboxed_subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error=f"Sandboxed subprocess skill failed: {exc}",
            )

        if completed.get("cancelled"):
            return self._cancelled(timestamp)
        if completed.get("timed_out"):
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="sandboxed_subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error=(
                    "Sandboxed subprocess skill timed out after "
                    f"{completed.get('timeout_seconds', self.timeout_seconds)} seconds."
                ),
            )

        returncode = completed.get("exit_code")
        stdout = str(completed.get("stdout") or "")
        stderr = str(completed.get("stderr") or "")
        if returncode != 0:
            detail = f": {stderr.strip()}" if stderr.strip() else ""
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="sandboxed_subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={"returncode": returncode},
                timestamp=timestamp,
                error=(
                    "Sandboxed subprocess skill exited with code "
                    f"{returncode}{detail}"
                ),
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="sandboxed_subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={"stdout": stdout},
                timestamp=timestamp,
                error=(
                    "Sandboxed subprocess skill returned invalid JSON: "
                    f"{exc.msg}"
                ),
            )

        if not isinstance(payload, dict):
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="sandboxed_subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={"stdout": stdout},
                timestamp=timestamp,
                error="Sandboxed subprocess skill JSON response must be an object.",
            )

        data = payload.get("data", {})
        if not isinstance(data, dict):
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id="external",
                mode="sandboxed_subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error=(
                    "Sandboxed subprocess skill response field 'data' "
                    "must be an object."
                ),
            )

        error = payload.get("error")
        ok = bool(payload.get("ok", False))
        return RobotActionResult(
            ok=ok,
            status="succeeded" if ok else "failed",
            robot_id="external",
            mode="sandboxed_subprocess",
            action="subprocess_skill",
            dry_run=True,
            data=data,
            timestamp=timestamp,
            error=str(error) if error else None,
        )

    @staticmethod
    def _cancelled(timestamp: str) -> RobotActionResult:
        return RobotActionResult(
            ok=False,
            status="cancelled",
            robot_id="external",
            mode="sandboxed_subprocess",
            action="subprocess_skill",
            dry_run=True,
            data={},
            timestamp=timestamp,
            error="Sandboxed subprocess skill cancelled by operator request.",
        )
