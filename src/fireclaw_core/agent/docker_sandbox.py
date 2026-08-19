from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import threading
import time
from typing import Any, Callable, Sequence
from uuid import uuid4


CancellationCheck = Callable[[], bool]
ProcessFactory = Callable[..., subprocess.Popen]

_PIPE_CHUNK_BYTES = 16 * 1024
_POLL_INTERVAL_SECONDS = 0.02
_PROCESS_EXIT_GRACE_SECONDS = 2.0
_CONTROL_TIMEOUT_SECONDS = 10.0


class DockerLifecycleError(RuntimeError):
    """Docker failed before FireClaw could prove a contained lifecycle."""


@dataclass(frozen=True)
class BoundedCommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_bytes_observed: int
    stderr_bytes_observed: int
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    cancelled: bool


@dataclass(frozen=True)
class DockerImageIdentity:
    configured_reference: str
    image_id: str
    repository_digests: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured_reference": self.configured_reference,
            "image_id": self.image_id,
            "repository_digests": list(self.repository_digests),
        }


@dataclass
class _BoundedCapture:
    limit_bytes: int
    data: bytearray
    observed_bytes: int = 0
    truncated: bool = False

    def consume(self, pipe: Any) -> None:
        try:
            while True:
                chunk = pipe.read(_PIPE_CHUNK_BYTES)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                self.observed_bytes += len(chunk)
                remaining = self.limit_bytes - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        finally:
            pipe.close()

    def text(self) -> str:
        text = bytes(self.data).decode("utf-8", errors="replace")
        if self.truncated:
            return text + "\n[output truncated]"
        return text


@dataclass(frozen=True)
class DockerSandboxRuntime:
    """Trusted Docker adapter with bounded I/O and deterministic cleanup."""

    process_factory: ProcessFactory = subprocess.Popen
    docker_executable: str = "docker"

    def verify_image(
        self,
        *,
        configured_reference: str,
        expected_image_id: str,
        max_output_bytes: int,
    ) -> DockerImageIdentity:
        result = self._run(
            [
                self.docker_executable,
                "image",
                "inspect",
                "--format",
                "{{json .Id}}\t{{json .RepoDigests}}",
                configured_reference,
            ],
            timeout_seconds=_CONTROL_TIMEOUT_SECONDS,
            max_output_bytes=max_output_bytes,
        )
        self._require_control_success(result, operation="inspect sandbox image")
        line = result.stdout.strip()
        try:
            raw_id, raw_repo_digests = line.split("\t", 1)
            image_id = json.loads(raw_id)
            repository_digests = json.loads(raw_repo_digests)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise DockerLifecycleError(
                "Docker returned malformed sandbox image identity metadata."
            ) from exc
        if repository_digests is None:
            repository_digests = []
        if not isinstance(image_id, str) or not isinstance(repository_digests, list):
            raise DockerLifecycleError(
                "Docker returned malformed sandbox image identity metadata."
            )
        if image_id != expected_image_id:
            raise DockerLifecycleError(
                "Sandbox image digest mismatch: configured image reference "
                f"{configured_reference!r} resolved to {image_id!r}, expected "
                f"{expected_image_id!r}."
            )
        if any(not isinstance(item, str) for item in repository_digests):
            raise DockerLifecycleError(
                "Docker returned malformed sandbox repository digest metadata."
            )
        return DockerImageIdentity(
            configured_reference=configured_reference,
            image_id=image_id,
            repository_digests=tuple(repository_digests),
        )

    def execute_container(
        self,
        *,
        create_command: Sequence[str],
        container_name: str,
        stdin_text: str | None,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: CancellationCheck,
    ) -> BoundedCommandResult:
        cleanup_required = False
        execution_result: BoundedCommandResult | None = None
        primary_error: BaseException | None = None
        try:
            cleanup_required = True
            created = self._run(
                list(create_command),
                timeout_seconds=_CONTROL_TIMEOUT_SECONDS,
                max_output_bytes=max_output_bytes,
            )
            self._require_control_success(created, operation="create sandbox container")
            execution_result = self._run(
                [
                    self.docker_executable,
                    "start",
                    "--attach",
                    "--interactive",
                    container_name,
                ],
                stdin_text=stdin_text,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                cancellation_requested=cancellation_requested,
            )
        except BaseException as exc:
            primary_error = exc
        cleanup_error: BaseException | None = None
        if cleanup_required:
            try:
                self._force_remove(container_name, max_output_bytes=max_output_bytes)
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            message = (
                f"Could not prove cleanup of Docker sandbox container "
                f"{container_name!r}: {cleanup_error}"
            )
            if primary_error is not None:
                message += f" Original execution error: {primary_error}"
            raise DockerLifecycleError(message) from cleanup_error
        if primary_error is not None:
            raise primary_error
        if execution_result is None:
            raise DockerLifecycleError(
                "Docker sandbox execution ended without a terminal result."
            )
        return execution_result

    def new_container_name(self) -> str:
        return f"fireclaw-exec-{uuid4().hex}"

    def _force_remove(self, container_name: str, *, max_output_bytes: int) -> None:
        result = self._run(
            [self.docker_executable, "rm", "--force", container_name],
            timeout_seconds=_CONTROL_TIMEOUT_SECONDS,
            max_output_bytes=max_output_bytes,
        )
        if result.exit_code == 0:
            return
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if "no such container" in combined:
            return
        self._require_control_success(result, operation="remove sandbox container")

    def _require_control_success(
        self,
        result: BoundedCommandResult,
        *,
        operation: str,
    ) -> None:
        if result.timed_out:
            raise DockerLifecycleError(f"Docker timed out while trying to {operation}.")
        if result.cancelled:
            raise DockerLifecycleError(
                f"Docker was cancelled while trying to {operation}."
            )
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise DockerLifecycleError(
                f"Docker could not {operation}: {detail or 'unknown Docker error'}"
            )

    def _run(
        self,
        command: Sequence[str],
        *,
        stdin_text: str | None = None,
        timeout_seconds: float,
        max_output_bytes: int,
        cancellation_requested: CancellationCheck | None = None,
    ) -> BoundedCommandResult:
        return run_bounded_process(
            command,
            stdin_text=stdin_text,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            cancellation_requested=cancellation_requested,
            process_factory=self.process_factory,
        )


def run_bounded_process(
    command: Sequence[str],
    *,
    stdin_text: str | None = None,
    timeout_seconds: float,
    max_output_bytes: int,
    cancellation_requested: CancellationCheck | None = None,
    process_factory: ProcessFactory = subprocess.Popen,
) -> BoundedCommandResult:
    """Run a process while draining output without retaining unbounded data."""

    if timeout_seconds <= 0:
        raise ValueError("Process timeout_seconds must be positive.")
    if max_output_bytes <= 0:
        raise ValueError("Process max_output_bytes must be positive.")
    cancellation_requested = cancellation_requested or (lambda: False)
    if cancellation_requested():
        return BoundedCommandResult(
            exit_code=None,
            stdout="",
            stderr="",
            stdout_bytes_observed=0,
            stderr_bytes_observed=0,
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            cancelled=True,
        )
    try:
        process = process_factory(
            list(command),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            close_fds=True,
        )
    except FileNotFoundError as exc:
        raise DockerLifecycleError("Docker executable is not available.") from exc
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        raise DockerLifecycleError("Docker process pipes were not created.")

    stdout_capture = _BoundedCapture(max_output_bytes, bytearray())
    stderr_capture = _BoundedCapture(max_output_bytes, bytearray())
    stdout_thread = threading.Thread(
        target=stdout_capture.consume,
        args=(process.stdout,),
        name="fireclaw-docker-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stderr_capture.consume,
        args=(process.stderr,),
        name="fireclaw-docker-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    stdin_thread: threading.Thread | None = None
    if stdin_text is not None and process.stdin is not None:
        encoded_input = stdin_text.encode("utf-8")

        def _write_stdin() -> None:
            try:
                process.stdin.write(encoded_input)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        stdin_thread = threading.Thread(
            target=_write_stdin,
            name="fireclaw-docker-stdin",
            daemon=True,
        )
        stdin_thread.start()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    cancelled = False
    while process.poll() is None:
        if cancellation_requested():
            cancelled = True
            process.kill()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(_POLL_INTERVAL_SECONDS)
    try:
        process.wait(timeout=_PROCESS_EXIT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    stdout_thread.join(timeout=_PROCESS_EXIT_GRACE_SECONDS)
    stderr_thread.join(timeout=_PROCESS_EXIT_GRACE_SECONDS)
    if stdin_thread is not None:
        stdin_thread.join(timeout=_PROCESS_EXIT_GRACE_SECONDS)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        raise DockerLifecycleError(
            "Docker output pipes did not close after termination."
        )
    return BoundedCommandResult(
        exit_code=None if timed_out or cancelled else process.returncode,
        stdout=stdout_capture.text(),
        stderr=stderr_capture.text(),
        stdout_bytes_observed=stdout_capture.observed_bytes,
        stderr_bytes_observed=stderr_capture.observed_bytes,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        timed_out=timed_out,
        cancelled=cancelled,
    )
