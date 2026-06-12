from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fireclaw_core.agent.robot import RobotActionResult


@dataclass(frozen=True)
class SubprocessSkillRunner:
    command: list[str]
    timeout_seconds: float = 30.0
    cwd: str | Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def run(
        self,
        inputs: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotActionResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        cancellation_requested = cancellation_requested or (lambda: False)
        serialized_inputs = json.dumps(inputs, ensure_ascii=False)
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.cwd,
                env=self.env or None,
            )
            completed = self._communicate_until_complete(
                process,
                serialized_inputs,
                cancellation_requested,
            )
        except _SubprocessCancelled:
            return RobotActionResult(
                ok=False,
                status="cancelled",
                robot_id="external",
                mode="subprocess",
                action="subprocess_skill",
                dry_run=True,
                data={},
                timestamp=timestamp,
                error="Subprocess skill cancelled by operator request.",
            )
        except _SubprocessTimedOut:
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

    def _communicate_until_complete(
        self,
        process: subprocess.Popen[str],
        serialized_inputs: str,
        cancellation_requested: Callable[[], bool],
    ) -> subprocess.CompletedProcess[str]:
        deadline = time.monotonic() + self.timeout_seconds
        input_pending = True
        while True:
            if cancellation_requested():
                self._terminate_cancelled_process(process)
                raise _SubprocessCancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill_process(process)
                raise _SubprocessTimedOut
            try:
                if input_pending:
                    stdout, stderr = process.communicate(
                        input=serialized_inputs,
                        timeout=min(0.02, remaining),
                    )
                    input_pending = False
                else:
                    stdout, stderr = process.communicate(timeout=min(0.02, remaining))
                return subprocess.CompletedProcess(
                    self.command,
                    process.returncode,
                    stdout,
                    stderr,
                )
            except subprocess.TimeoutExpired:
                input_pending = False
                continue

    def _terminate_cancelled_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            self._kill_process(process)

    def _kill_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.kill()
        process.communicate()


class _SubprocessCancelled(Exception):
    pass


class _SubprocessTimedOut(Exception):
    pass
