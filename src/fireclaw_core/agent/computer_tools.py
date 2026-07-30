from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Sequence

from fireclaw_core.agent.tool_runtime import AgentTool
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import SandboxProfile


COMPUTER_TOOL_PLUGIN_ID = "fireclaw.agent-tools.computer"
_MAX_FILE_CHARS = 200_000
_MAX_LIST_ENTRIES = 2_000


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ComputerSandbox:
    """Trusted host adapter for a narrowly scoped computer workspace."""

    profile: SandboxProfile
    process_runner: ProcessRunner = subprocess.run

    def __post_init__(self) -> None:
        if not self.profile.enabled:
            raise ValueError("ComputerSandbox requires an enabled sandbox.")
        self.profile.workspace_root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self.profile.workspace_root

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
        target = self._resolve(relative, must_exist=True)
        if not target.is_file() or target.is_symlink():
            raise ValueError("computer_read_file path must be a regular file.")
        raw = target.read_bytes()
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
        target = self._resolve(relative, must_exist=False)
        if target.exists() and (not target.is_file() or target.is_symlink()):
            raise ValueError(
                "computer_write_file target must be a regular file."
            )
        expected = arguments.get("expected_sha256")
        if target.exists():
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if not isinstance(expected, str) or not expected:
                raise ValueError(
                    "Overwriting an existing file requires expected_sha256 "
                    "from computer_read_file."
                )
            if expected != current:
                raise ValueError(
                    "File changed since it was read; expected_sha256 does not match."
                )
        elif expected is not None:
            raise ValueError(
                "expected_sha256 must be omitted when creating a new file."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
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
        if not self.profile.process_ready:
            raise RuntimeError(
                "Docker process sandbox is not configured with an image."
            )
        argv = _validated_argv(arguments["argv"])
        relative_cwd = str(arguments.get("cwd") or ".")
        cwd = self._resolve(relative_cwd, must_exist=True)
        if not cwd.is_dir():
            raise ValueError("computer_exec cwd must be a directory.")
        timeout = min(
            float(
                arguments.get(
                    "timeout_seconds",
                    self.profile.max_timeout_seconds,
                )
            ),
            self.profile.max_timeout_seconds,
        )
        container_cwd = (
            Path("/workspace") / cwd.relative_to(self.root)
        ).as_posix()
        command = self._docker_command(
            argv=argv,
            container_cwd=container_cwd,
        )
        try:
            completed = self.process_runner(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Docker executable is not available.") from exc
        except subprocess.TimeoutExpired as exc:
            return {
                "argv": argv,
                "exit_code": None,
                "stdout": _bounded_output(exc.stdout, self.profile),
                "stderr": _bounded_output(exc.stderr, self.profile),
                "timed_out": True,
                "timeout_seconds": timeout,
            }
        return {
            "argv": argv,
            "exit_code": completed.returncode,
            "stdout": _bounded_output(completed.stdout, self.profile),
            "stderr": _bounded_output(completed.stderr, self.profile),
            "timed_out": False,
            "timeout_seconds": timeout,
        }

    def _resolve(self, relative: str, *, must_exist: bool) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            raise ValueError("Computer tool paths must be workspace-relative.")
        resolved = (self.root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Computer tool path escapes the sandbox.") from exc
        if must_exist and not resolved.exists():
            raise ValueError(f"Sandbox path does not exist: {relative}")
        return resolved

    def _docker_command(
        self,
        *,
        argv: Sequence[str],
        container_cwd: str,
    ) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
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
            f"type=bind,src={self.root},dst=/workspace,rw",
            "--workdir",
            container_cwd,
            str(self.profile.image),
            *argv,
        ]


def register_computer_tool_plugin(
    host: FireClawPluginHost,
    sandbox: ComputerSandbox,
    *,
    plugin_id: str = COMPUTER_TOOL_PLUGIN_ID,
) -> None:
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
    )


def computer_agent_tools(sandbox: ComputerSandbox) -> tuple[AgentTool, ...]:
    common = {
        "roles": ("mission_agent", "robot_agent"),
        "modes": ("simulation", "real"),
        "requires_sandbox": True,
        "metadata": {"family": "computer"},
    }
    return (
        AgentTool(
            name="computer_list_files",
            description=(
                "List files inside the configured agent sandbox workspace."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative directory.",
                        "maxLength": 1024,
                    },
                    "max_depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 8,
                    },
                },
                "additionalProperties": False,
            },
            handler=sandbox.list_files,
            effect="read",
            **common,
        ),
        AgentTool(
            name="computer_read_file",
            description=(
                "Read one UTF-8 text file inside the configured sandbox."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_FILE_CHARS,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=sandbox.read_file,
            effect="read",
            **common,
        ),
        AgentTool(
            name="computer_write_file",
            description=(
                "Create or atomically replace a UTF-8 text file inside the "
                "sandbox. Existing files require the SHA-256 returned by "
                "computer_read_file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                    },
                    "content": {
                        "type": "string",
                        "maxLength": _MAX_FILE_CHARS,
                    },
                    "expected_sha256": {
                        "type": "string",
                        "minLength": 64,
                        "maxLength": 64,
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=sandbox.write_file,
            effect="bounded_mutation",
            **common,
        ),
        AgentTool(
            name="computer_exec",
            description=(
                "Run an argv command in the configured Docker sandbox. "
                "No host shell is used."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                        },
                        "minItems": 1,
                        "maxItems": 128,
                    },
                    "cwd": {
                        "type": "string",
                        "maxLength": 1024,
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 0.1,
                    },
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            handler=sandbox.execute,
            effect="process",
            **common,
        ),
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


def _bounded_output(value: Any, profile: SandboxProfile) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    if len(text) <= profile.max_output_chars:
        return text
    return text[: profile.max_output_chars] + "\n[output truncated]"
