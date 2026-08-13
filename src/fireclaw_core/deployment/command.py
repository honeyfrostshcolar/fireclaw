"""Bounded subprocess execution for trusted deployment operations."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class DeploymentCommandResult:
    argv: tuple[str, ...]
    exit_code: int | None
    output: str
    duration_seconds: float
    timed_out: bool = False
    truncated: bool = False
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and not self.truncated
            and self.error_code is None
        )


class DeploymentCommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> DeploymentCommandResult:
        ...


class SubprocessDeploymentCommandRunner:
    """Run deployer-owned argv without an interpolated shell command."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> DeploymentCommandResult:
        command = tuple(str(part) for part in argv)
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=dict(env) if env is not None else None,
                cwd=str(cwd) if cwd is not None else None,
                start_new_session=True,
            )
        except FileNotFoundError:
            return DeploymentCommandResult(
                argv=command,
                exit_code=None,
                output="",
                duration_seconds=time.monotonic() - started,
                error_code="executable_not_found",
            )
        except OSError:
            return DeploymentCommandResult(
                argv=command,
                exit_code=None,
                output="",
                duration_seconds=time.monotonic() - started,
                error_code="process_start_failed",
            )

        assert process.stdout is not None
        output = bytearray()
        timed_out = False
        truncated = False
        stream_open = True
        selector = selectors.DefaultSelector()
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = started + timeout_seconds
        try:
            while stream_open:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _terminate(process)
                    break
                events = selector.select(timeout=min(0.05, remaining))
                for key, _ in events:
                    room = max_output_bytes - len(output)
                    try:
                        chunk = os.read(key.fileobj.fileno(), min(4096, room + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        stream_open = False
                        break
                    output.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True
                        _terminate(process)
                        stream_open = False
                        break
                if process.poll() is not None and not events and stream_open:
                    try:
                        chunk = os.read(
                            process.stdout.fileno(),
                            max_output_bytes - len(output) + 1,
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        stream_open = False
                    else:
                        room = max_output_bytes - len(output)
                        output.extend(chunk[:room])
                        if len(chunk) > room:
                            truncated = True
                            stream_open = False
            if process.poll() is None:
                _terminate(process)
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _kill(process)
            process.wait()
        finally:
            selector.close()
            process.stdout.close()

        return DeploymentCommandResult(
            argv=command,
            exit_code=process.returncode,
            output=output.decode("utf-8", errors="replace"),
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
            truncated=truncated,
        )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        _kill(process)


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


__all__ = [
    "DeploymentCommandResult",
    "DeploymentCommandRunner",
    "SubprocessDeploymentCommandRunner",
]
