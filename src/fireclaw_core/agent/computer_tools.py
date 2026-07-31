from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from typing import TYPE_CHECKING, Any, Callable, Sequence

from fireclaw_core.agent.docker_sandbox import (
    DockerSandboxRuntime,
    ProcessFactory,
)
from fireclaw_core.infra.path_security import (
    validate_sandbox_workspace_root,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import SandboxProfile

if TYPE_CHECKING:
    from fireclaw_core.agent.tool_runtime import AgentTool


COMPUTER_TOOL_PLUGIN_ID = "fireclaw.agent-tools.computer"
_MAX_FILE_CHARS = 200_000
_MAX_LIST_ENTRIES = 2_000
_MAX_STAGED_SKILL_FILES = 256
_MAX_STAGED_SKILL_FILE_BYTES = 2 * 1024 * 1024
_MAX_STAGED_SKILL_TOTAL_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ComputerSandbox:
    """Trusted host adapter for a narrowly scoped computer workspace."""

    profile: SandboxProfile
    process_factory: ProcessFactory = subprocess.Popen
    _workspace_lock: threading.RLock = field(
        init=False,
        repr=False,
        compare=False,
    )
    _process_slots: threading.BoundedSemaphore = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.profile.enabled:
            raise ValueError("ComputerSandbox requires an enabled sandbox.")
        self.profile.workspace_root.mkdir(parents=True, exist_ok=True)
        self._validated_mount_root()
        object.__setattr__(self, "_workspace_lock", threading.RLock())
        object.__setattr__(
            self,
            "_process_slots",
            threading.BoundedSemaphore(
                self.profile.max_concurrent_processes
            ),
        )
        self._validate_workspace_quota()

    @property
    def root(self) -> Path:
        return self._validated_mount_root()

    def list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative = str(arguments.get("path") or ".")
        max_depth = int(arguments.get("max_depth", 2))
        target = self._resolve(relative, must_exist=True)
        if not target.is_dir():
            raise ValueError("computer_list_files path must be a directory.")

        entries: list[dict[str, Any]] = []
        for current_root, directories, filenames in os.walk(
            target,
            followlinks=False,
        ):
            current = Path(current_root)
            depth = len(current.relative_to(target).parts)
            directories.sort()
            filenames.sort()
            if depth >= max_depth:
                directories[:] = []
            for name in (*directories, *filenames):
                path = current / name
                relative_path = path.relative_to(self.root).as_posix()
                entries.append(
                    {
                        "path": relative_path,
                        "kind": (
                            "symlink"
                            if path.is_symlink()
                            else "directory"
                            if path.is_dir()
                            else "file"
                        ),
                        "size_bytes": (
                            path.stat().st_size
                            if path.is_file() and not path.is_symlink()
                            else None
                        ),
                    }
                )
                if len(entries) >= _MAX_LIST_ENTRIES:
                    return {
                        "path": relative,
                        "entries": entries,
                        "truncated": True,
                    }
        return {
            "path": relative,
            "entries": entries,
            "truncated": False,
        }

    def read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative = str(arguments["path"])
        max_chars = min(
            int(arguments.get("max_chars", self.profile.max_output_chars)),
            _MAX_FILE_CHARS,
        )
        if max_chars <= 0:
            raise ValueError("computer_read_file max_chars must be positive.")
        target = self._resolve(relative, must_exist=True)
        raw = _read_regular_file_limited(
            target,
            max_bytes=self.profile.max_file_bytes,
            description="computer_read_file path",
        )
        digest = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > max_chars
        return {
            "path": relative,
            "content": text[:max_chars],
            "sha256": digest,
            "size_bytes": len(raw),
            "truncated": truncated,
        }

    def write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative = str(arguments["path"])
        content = str(arguments["content"])
        if len(content) > _MAX_FILE_CHARS:
            raise ValueError(
                f"computer_write_file content exceeds {_MAX_FILE_CHARS} characters."
            )
        encoded = content.encode("utf-8")
        if len(encoded) > self.profile.max_file_bytes:
            raise ValueError(
                "computer_write_file content exceeds the sandbox "
                "max_file_bytes limit."
            )
        with self._workspace_lock:
            target = self._resolve(relative, must_exist=False)
            if target.exists() and (
                not target.is_file() or target.is_symlink()
            ):
                raise ValueError(
                    "computer_write_file target must be a regular file."
                )
            expected = arguments.get("expected_sha256")
            if target.exists():
                current_raw = _read_regular_file_limited(
                    target,
                    max_bytes=self.profile.max_file_bytes,
                    description="computer_write_file target",
                )
                current = hashlib.sha256(current_raw).hexdigest()
                if not isinstance(expected, str) or not expected:
                    raise ValueError(
                        "Overwriting an existing file requires expected_sha256 "
                        "from computer_read_file."
                    )
                if expected != current:
                    raise ValueError(
                        "File changed since it was read; expected_sha256 does "
                        "not match."
                    )
            elif expected is not None:
                raise ValueError(
                    "expected_sha256 must be omitted when creating a new file."
                )

            self._validate_workspace_quota(
                additional_bytes=len(encoded),
                additional_files=1,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary_path = Path(handle.name)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, target)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()
        return {
            "path": relative,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
            "created": expected is None,
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cwd = str(arguments.get("cwd") or ".")
        self._resolve(cwd, must_exist=True)
        return self.execute_process(
            argv=_validated_argv(arguments["argv"]),
            cwd=cwd,
            stdin_text=None,
            timeout_seconds=float(
                arguments.get(
                    "timeout_seconds",
                    self.profile.max_timeout_seconds,
                )
            ),
        )

    def execute_process(
        self,
        *,
        argv: list[str],
        cwd: str,
        stdin_text: str | None,
        timeout_seconds: float,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if not self.profile.process_ready:
            raise RuntimeError(
                "Docker process sandbox is not configured with an image."
            )
        argv = _validated_argv(argv)
        sandbox_cwd = self._resolve(
            cwd,
            must_exist=True,
            allow_internal=True,
        )
        if not sandbox_cwd.is_dir():
            raise ValueError("computer_exec cwd must be a directory.")
        if timeout_seconds <= 0:
            raise ValueError("Process timeout_seconds must be positive.")
        timeout = min(
            float(timeout_seconds),
            self.profile.max_timeout_seconds,
        )
        cancellation_requested = cancellation_requested or (lambda: False)
        if cancellation_requested():
            return {
                "argv": argv,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "stdout_bytes_observed": 0,
                "stderr_bytes_observed": 0,
                "stdout_truncated": False,
                "stderr_truncated": False,
                "timed_out": False,
                "cancelled": True,
                "timeout_seconds": timeout,
                "container_name": None,
                "container_cleanup_succeeded": True,
                "image_identity": None,
            }
        container_cwd = (
            Path("/workspace") / sandbox_cwd.relative_to(self.root)
        ).as_posix()
        if stdin_text is not None and len(stdin_text.encode("utf-8")) > _MAX_FILE_CHARS:
            raise ValueError(
                f"Process stdin exceeds {_MAX_FILE_CHARS} encoded bytes."
            )
        if not self._process_slots.acquire(blocking=False):
            raise RuntimeError(
                "Sandbox concurrent process limit reached; retry after an "
                "active invocation finishes."
            )
        try:
            self._validate_workspace_quota()
            runtime = DockerSandboxRuntime(process_factory=self.process_factory)
            image_identity = runtime.verify_image(
                configured_reference=str(self.profile.image),
                expected_image_id=str(self.profile.image_digest),
                max_output_bytes=self.profile.max_output_bytes,
            )
            container_name = runtime.new_container_name()
            command = self._docker_create_command(
                argv=argv,
                container_cwd=container_cwd,
                container_name=container_name,
                image_id=image_identity.image_id,
            )
            completed = runtime.execute_container(
                create_command=command,
                container_name=container_name,
                stdin_text=stdin_text,
                timeout_seconds=timeout,
                max_output_bytes=self.profile.max_output_bytes,
                cancellation_requested=cancellation_requested,
            )
        finally:
            self._process_slots.release()
        stdout, stdout_chars_truncated = _bounded_text(
            completed.stdout,
            self.profile.max_output_chars,
        )
        stderr, stderr_chars_truncated = _bounded_text(
            completed.stderr,
            self.profile.max_output_chars,
        )
        return {
            "argv": argv,
            "exit_code": completed.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_bytes_observed": completed.stdout_bytes_observed,
            "stderr_bytes_observed": completed.stderr_bytes_observed,
            "stdout_truncated": (
                completed.stdout_truncated or stdout_chars_truncated
            ),
            "stderr_truncated": (
                completed.stderr_truncated or stderr_chars_truncated
            ),
            "timed_out": completed.timed_out,
            "cancelled": completed.cancelled,
            "timeout_seconds": timeout,
            "container_name": container_name,
            "container_cleanup_succeeded": True,
            "image_identity": image_identity.to_dict(),
        }

    def stage_skill(
        self,
        source_dir: str | Path,
        *,
        skill_name: str,
    ) -> str:
        """Copy a legacy manifest directory into the sandbox workspace."""

        source = Path(source_dir)
        if source.is_symlink():
            raise ValueError("Legacy skill source directory must not be a symlink.")
        source = source.resolve(strict=True)
        if not source.is_dir():
            raise ValueError("Legacy skill source must be a directory.")
        try:
            self.root.resolve().relative_to(source)
        except ValueError:
            pass
        else:
            raise ValueError(
                "Legacy skill source must not contain the sandbox workspace."
            )

        files: list[tuple[Path, Path, bytes, int]] = []
        total_bytes = 0
        for current_root, directories, filenames in os.walk(
            source,
            followlinks=False,
        ):
            current = Path(current_root)
            for directory in directories:
                if (current / directory).is_symlink():
                    raise ValueError(
                        "Legacy skill directories must not contain symlinks."
                    )
            for filename in sorted(filenames):
                path = current / filename
                if path.is_symlink():
                    raise ValueError(
                        "Legacy skill directories must not contain symlinks."
                    )
                if not path.is_file():
                    raise ValueError(
                        "Legacy skill directories may contain only regular files."
                    )
                raw = _read_regular_file_limited(
                    path,
                    max_bytes=_MAX_STAGED_SKILL_FILE_BYTES,
                    description="Legacy skill file",
                )
                total_bytes += len(raw)
                if total_bytes > _MAX_STAGED_SKILL_TOTAL_BYTES:
                    raise ValueError(
                        "Legacy skill directory exceeds the staging size limit."
                    )
                files.append(
                    (
                        path,
                        path.relative_to(source),
                        raw,
                        path.stat().st_mode & 0o777,
                    )
                )
                if len(files) > _MAX_STAGED_SKILL_FILES:
                    raise ValueError(
                        "Legacy skill directory exceeds the staging file limit."
                    )

        digest = hashlib.sha256()
        for _, relative, raw, mode in sorted(
            files,
            key=lambda item: item[1].as_posix(),
        ):
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(mode).encode("ascii"))
            digest.update(b"\0")
            digest.update(raw)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", skill_name).strip(".-")
        safe_name = safe_name or "legacy-skill"
        relative_destination = (
            Path(".fireclaw")
            / "legacy-skills"
            / f"{safe_name}-{digest.hexdigest()[:16]}"
        )
        destination = self._resolve(
            relative_destination.as_posix(),
            must_exist=False,
            allow_internal=True,
        )
        with self._workspace_lock:
            if destination.exists():
                if not destination.is_dir() or destination.is_symlink():
                    raise ValueError(
                        "Legacy skill staging destination is not a regular "
                        "directory."
                    )
                return relative_destination.as_posix()
            self._validate_workspace_quota(
                additional_bytes=total_bytes,
                additional_files=len(files),
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=f".{safe_name}-",
                    dir=destination.parent,
                )
            )
            try:
                for _, relative, raw, mode in files:
                    target = temporary / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    target.chmod(mode)
                try:
                    os.replace(temporary, destination)
                except OSError:
                    if not destination.is_dir():
                        raise
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return relative_destination.as_posix()

    def _resolve(
        self,
        relative: str,
        *,
        must_exist: bool,
        allow_internal: bool = False,
    ) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            raise ValueError("Computer tool paths must be workspace-relative.")
        root = self.root
        resolved = (root / candidate).resolve(strict=False)
        try:
            relative_path = resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Computer tool path escapes the sandbox.") from exc
        if (
            not allow_internal
            and relative_path.parts
            and relative_path.parts[0] == ".fireclaw"
        ):
            raise ValueError(
                "Computer tool access to the reserved .fireclaw directory "
                "is prohibited."
            )
        if must_exist and not resolved.exists():
            raise ValueError(f"Sandbox path does not exist: {relative}")
        return resolved

    def _validated_mount_root(self) -> Path:
        canonical = validate_sandbox_workspace_root(
            self.profile.workspace_root,
            allowed_roots=self.profile.allowed_workspace_roots,
            require_exists=True,
        )
        if canonical != self.profile.workspace_root:
            raise ValueError(
                "Sandbox workspace_root changed through a symlink after "
                "deployment validation."
            )
        return canonical

    def _validate_workspace_quota(
        self,
        *,
        additional_bytes: int = 0,
        additional_files: int = 0,
    ) -> tuple[int, int]:
        file_count = 0
        total_bytes = 0
        for current_root, directories, filenames in os.walk(
            self.root,
            followlinks=False,
        ):
            current = Path(current_root)
            for name in (*directories, *filenames):
                path = current / name
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError(
                        "Sandbox workspace must not contain symbolic links."
                    )
                if path.name in directories:
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise ValueError(
                            "Sandbox workspace contains a non-directory entry."
                        )
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(
                        "Sandbox workspace may contain only regular files "
                        "and directories."
                    )
                file_count += 1
                total_bytes += metadata.st_size
                if (
                    file_count + additional_files
                    > self.profile.max_workspace_files
                ):
                    raise ValueError(
                        "Sandbox workspace exceeds max_workspace_files."
                    )
                if (
                    total_bytes + additional_bytes
                    > self.profile.max_workspace_bytes
                ):
                    raise ValueError(
                        "Sandbox workspace exceeds max_workspace_bytes."
                    )
        if file_count + additional_files > self.profile.max_workspace_files:
            raise ValueError("Sandbox workspace exceeds max_workspace_files.")
        if total_bytes + additional_bytes > self.profile.max_workspace_bytes:
            raise ValueError("Sandbox workspace exceeds max_workspace_bytes.")
        return file_count, total_bytes

    def _docker_create_command(
        self,
        *,
        argv: Sequence[str],
        container_cwd: str,
        container_name: str,
        image_id: str,
    ) -> list[str]:
        mount_root = self.root
        workspace_digest = hashlib.sha256(
            str(mount_root).encode("utf-8")
        ).hexdigest()
        return [
            "docker",
            "create",
            "--name",
            container_name,
            "--init",
            "--label",
            "fireclaw.sandbox=1",
            "--label",
            f"fireclaw.invocation_id={container_name}",
            "--label",
            f"fireclaw.workspace_sha256={workspace_digest}",
            "--label",
            f"fireclaw.image_id={image_id}",
            "--pull=never",
            "--network",
            self.profile.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.profile.pids_limit),
            "--memory",
            f"{self.profile.memory_mb}m",
            "--cpus",
            str(self.profile.cpus),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,src={mount_root},dst=/workspace,readonly",
            "--workdir",
            container_cwd,
            image_id,
            *argv,
        ]


def _read_regular_file_limited(
    path: Path,
    *,
    max_bytes: int,
    description: str,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{description} must be a regular file.") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{description} must be a regular file.")
        if metadata.st_size > max_bytes:
            raise ValueError(f"{description} exceeds the per-file size limit.")
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > max_bytes:
                raise ValueError(
                    f"{description} exceeds the per-file size limit."
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def register_computer_tool_plugin(
    host: FireClawPluginHost,
    sandbox: ComputerSandbox,
    *,
    plugin_id: str = COMPUTER_TOOL_PLUGIN_ID,
) -> None:
    """Compatibility wrapper for the historical in-core registration API.

    Canonical registration is now ``extensions/computer-tools`` discovered by
    the generic extension loader.  This wrapper only adapts the public Plugin
    provider for older callers and does not own Tool schemas.
    """

    tools = computer_agent_tools(sandbox)

    def register(api) -> None:
        for tool in tools:
            api.register_tool(
                tool,
                metadata={
                    "tool_class": "agent_tool",
                    "effect": tool.effect,
                    "roles": list(tool.roles),
                    "modes": list(tool.modes),
                    "requires_sandbox": tool.requires_sandbox,
                },
            )

    host.activate(
        plugin_id,
        register,
        name="Restricted computer tools",
        description=(
            "Workspace-scoped file tools and Docker-only process execution."
        ),
        source="builtin_agent_tool_plugin",
        trust_level="builtin",
    )


def computer_agent_tools(sandbox: ComputerSandbox) -> tuple[Any, ...]:
    """Compatibility projection of the provider-owned ToolSpecs."""

    from fireclaw_core.plugin.sdk_adapter import normalize_registered_tool

    provider_path = (
        Path(__file__).resolve().parents[3]
        / "extensions"
        / "computer-tools"
        / "plugin"
        / "entrypoint.py"
    )
    import importlib.util
    import sys

    module_name = "fireclaw_computer_tools_provider"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, provider_path)
        if spec is None or spec.loader is None:
            raise ImportError("computer-tools extension provider is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return tuple(
        normalize_registered_tool(value)
        for value in module._tools(sandbox)
    )


def _validated_argv(value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 128
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > 4096
            or "\x00" in item
            for item in value
        )
    ):
        raise ValueError("computer_exec argv must contain 1-128 safe strings.")
    return list(value)


def _bounded_text(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars] + "\n[output truncated]", True
