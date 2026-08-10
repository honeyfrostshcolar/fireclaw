from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import subprocess
import time

import pytest

from fireclaw_core.agent.computer_tools import (
    ComputerSandbox,
)
from fireclaw_core.agent.tool_runtime import (
    AgentTool,
    AgentToolRuntime,
)
from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
    execution_action_hash,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.policy.deployment import (
    DEPLOYMENT_POLICY_ID,
    DeploymentProfile,
    SandboxProfile,
)
from fireclaw_core.infra.runtime_state import (
    SqliteAuthoritativeRuntimeStore,
)

_TEST_IMAGE_ID = "sha256:" + ("a" * 64)
_EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _register_test_tool(
    host: FireClawPluginHost,
    tool: AgentTool,
    *,
    owner_plugin_id: str,
) -> None:
    host.activate(
        owner_plugin_id,
        lambda api: api.register_tool(tool),
        trust_level="trusted",
    )


def _computer_tools(sandbox: ComputerSandbox) -> dict[str, AgentTool]:
    host = FireClawPluginHost()
    load_fireclaw_extensions(
        host,
        (_EXTENSIONS / "computer-tools",),
        mode="simulation",
        role="robot_agent",
        services={"fireclaw.agent-tools.computer.sandbox": sandbox},
        strict=True,
    )
    return {
        item.contribution_id: item.value
        for item in host.contributions("tool")
    }


class _RecordingInput:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, value: bytes) -> int:
        self.data.extend(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _ImmediateProcess:
    def __init__(
        self,
        *,
        stdout: bytes,
        stderr: bytes = b"",
        returncode: int = 0,
        with_stdin: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.stdin = _RecordingInput() if with_stdin else None
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _RecordingDockerFactory:
    def __init__(self, *, start_stdout: str) -> None:
        self.start_stdout = start_stdout
        self.calls: list[tuple[list[str], dict, _ImmediateProcess]] = []

    def __call__(self, command: list[str], **kwargs):
        if command[1:3] == ["image", "inspect"]:
            stdout = f'"{_TEST_IMAGE_ID}"\t[]\n'.encode()
        elif command[1] == "start":
            stdout = self.start_stdout.encode()
        else:
            stdout = b"ok"
        process = _ImmediateProcess(
            stdout=stdout,
            with_stdin=kwargs.get("stdin") == subprocess.PIPE,
        )
        self.calls.append((command, kwargs, process))
        return process


def _profile(
    tmp_path: Path,
    *,
    mode: str = "simulation",
    role: str = "robot_agent",
) -> DeploymentProfile:
    return DeploymentProfile(
        mode=mode,
        role=role,
        sandbox=SandboxProfile(
            enabled=True,
            workspace_root=tmp_path / role,
            image="fireclaw-sim:test",
            image_digest=_TEST_IMAGE_ID,
        ),
    )


def _authorization(
    name: str,
    arguments: dict,
    *,
    scope_hash: str = "unrelated-scope",
) -> VerifiedExecutionAuthorization:
    return VerifiedExecutionAuthorization(
        authorization_id="auth-1",
        request_id="request-1",
        operator_id="operator-1",
        scope_hash=scope_hash,
        authorized_action_hashes=frozenset(
            {execution_action_hash(name, arguments)}
        ),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
    )


def test_runtime_projects_agent_tools_but_not_physical_skill_objects(
    tmp_path: Path,
) -> None:
    host = FireClawPluginHost()
    _register_test_tool(
        host,
        AgentTool(
            name="inspect",
            description="Inspect test state.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda arguments: {"ok": True},
        ),
        owner_plugin_id="test.inspect",
    )
    host.activate(
        "test.not-agent-tool",
        lambda api: api.register_tool(
            type("OtherTool", (), {"name": "physical_like"})()
        ),
        trust_level="trusted",
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path),
    )

    assert [item["function"]["name"] for item in runtime.tool_schemas()] == [
        "inspect"
    ]
    exposure = runtime.exposure_manifest()
    assert exposure["policy_id"] == DEPLOYMENT_POLICY_ID
    assert exposure["profile_id"] == "robot_agent.simulation"


def test_runtime_executes_allowed_tool_and_audits_result(
    tmp_path: Path,
) -> None:
    events: list[dict] = []
    host = FireClawPluginHost()
    _register_test_tool(
        host,
        AgentTool(
            name="inspect",
            description="Inspect a named item.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=lambda arguments: {"seen": arguments["name"]},
        ),
        owner_plugin_id="test.inspect",
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path),
        event_sink=events.append,
    )

    result = runtime.execute("inspect", {"name": "map"})

    assert result.status == "executed"
    assert result.output == {"seen": "map"}
    assert events[0]["type"] == "agent_tool.execution"


def test_before_tool_call_adjustment_is_revalidated(
    tmp_path: Path,
) -> None:
    called = False

    def handler(arguments: dict) -> dict:
        nonlocal called
        called = True
        return arguments

    host = FireClawPluginHost()
    _register_test_tool(
        host,
        AgentTool(
            name="inspect",
            description="Inspect a named item.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=handler,
        ),
        owner_plugin_id="test.inspect",
    )
    host.activate(
        "test.guard",
        lambda api: api.register_hook(
            "before_tool_call",
            "inspect",
            lambda payload: {"arguments": {"unexpected": True}},
        ),
        trust_level="trusted",
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path),
    )

    result = runtime.execute("inspect", {"name": "map"})

    assert result.status == "blocked"
    assert result.error_code == "adjusted_agent_tool_arguments_invalid"
    assert called is False


def test_real_mutation_requires_exact_verified_authorization(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    arguments = {"path": "notes.txt", "content": "safe"}
    host = FireClawPluginHost()
    _register_test_tool(
        host,
        AgentTool(
            name="computer_write_file",
            description="Write a bounded test file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=lambda value: calls.append(value) or {"ok": True},
            effect="bounded_mutation",
            requires_sandbox=True,
        ),
        owner_plugin_id="test.write",
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path, mode="real"),
        authorization_use_recorder=lambda **kwargs: True,
    )

    pending = runtime.execute("computer_write_file", arguments)
    assert pending.approval_request is not None
    scope_hash = pending.approval_request["scope_hash"]
    modified = runtime.execute(
        "computer_write_file",
        arguments,
        authorization=_authorization(
            "computer_write_file",
            {"path": "other.txt", "content": "safe"},
        ),
    )
    wrong_scope = runtime.execute(
        "computer_write_file",
        arguments,
        authorization=_authorization("computer_write_file", arguments),
    )
    allowed = runtime.execute(
        "computer_write_file",
        arguments,
        authorization=_authorization(
            "computer_write_file",
            arguments,
            scope_hash=scope_hash,
        ),
    )

    assert pending.status == "approval_required"
    assert modified.status == "approval_required"
    assert wrong_scope.status == "approval_required"
    assert allowed.status == "executed"
    assert allowed.authorization_scope_hash == scope_hash
    assert calls == [arguments]


def test_real_mutation_consumes_exact_authorization_once(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []
    arguments = {"path": "notes.txt", "content": "safe"}
    host = FireClawPluginHost()
    _register_test_tool(
        host,
        AgentTool(
            name="computer_write_file",
            description="Write a bounded test file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=lambda value: calls.append(value) or {"ok": True},
            effect="bounded_mutation",
            requires_sandbox=True,
        ),
        owner_plugin_id="test.write",
    )
    store = SqliteAuthoritativeRuntimeStore(tmp_path / "runtime.sqlite3")
    store.persist_execution_authorization(
        {"authorization_id": "auth-1"}
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path, mode="real"),
        authorization_use_recorder=store.record_authorization_use,
    )
    pending = runtime.execute("computer_write_file", arguments)
    assert pending.approval_request is not None
    authorization = _authorization(
        "computer_write_file",
        arguments,
        scope_hash=pending.approval_request["scope_hash"],
    )

    first = runtime.execute(
        "computer_write_file",
        arguments,
        authorization=authorization,
    )
    replay = runtime.execute(
        "computer_write_file",
        arguments,
        authorization=authorization,
    )

    assert first.status == "executed"
    assert first.authorization_operation_id is not None
    assert replay.status == "blocked"
    assert replay.error_code == "agent_tool_authorization_already_used"
    assert calls == [arguments]


def test_agent_tool_rejects_physical_effect() -> None:
    with pytest.raises(ValueError, match="Physical actions"):
        AgentTool(
            name="drive_motor",
            description="Unsafe test.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda arguments: None,
            effect="physical",
        )


def test_computer_tools_confine_files_and_use_docker_without_shell(
    tmp_path: Path,
) -> None:
    factory = _RecordingDockerFactory(start_stdout="ok")
    profile = _profile(tmp_path).sandbox
    sandbox = ComputerSandbox(profile=profile, process_factory=factory)
    tools = _computer_tools(sandbox)

    with pytest.raises(ValueError, match="escapes the sandbox"):
        tools["computer_read_file"].handler({"path": "../secret"})

    created = tools["computer_write_file"].handler(
        {"path": "notes/a.txt", "content": "one"}
    )
    read = tools["computer_read_file"].handler({"path": "notes/a.txt"})
    with pytest.raises(ValueError, match="expected_sha256"):
        tools["computer_write_file"].handler(
            {"path": "notes/a.txt", "content": "two"}
        )
    updated = tools["computer_write_file"].handler(
        {
            "path": "notes/a.txt",
            "content": "two",
            "expected_sha256": read["sha256"],
        }
    )
    executed = tools["computer_exec"].handler(
        {"argv": ["printf", "ok"], "cwd": "notes"}
    )

    assert created["created"] is True
    assert updated["created"] is False
    assert executed["exit_code"] == 0
    commands = [call[0] for call in factory.calls]
    assert [command[1] for command in commands] == [
        "image",
        "create",
        "start",
        "rm",
    ]
    command = commands[1]
    assert command[:2] == ["docker", "create"]
    assert "--name" in command
    assert "--init" in command
    assert "--pull=never" in command
    assert "--network" in command
    assert "--cap-drop" in command
    assert "--read-only" in command
    mount = command[command.index("--mount") + 1]
    assert mount.endswith(",readonly")
    assert ",rw" not in mount
    assert "--env" not in command
    assert "-e" not in command
    assert command[-2:] == ["printf", "ok"]
    assert executed["container_cleanup_succeeded"] is True
    assert executed["image_identity"]["image_id"] == _TEST_IMAGE_ID


def test_computer_sandbox_passes_skill_input_only_to_docker_stdin(
    tmp_path: Path,
) -> None:
    factory = _RecordingDockerFactory(start_stdout="{}")
    sandbox = ComputerSandbox(
        profile=_profile(tmp_path).sandbox,
        process_factory=factory,
    )

    result = sandbox.execute_process(
        argv=["python3", "worker.py"],
        cwd=".",
        stdin_text='{"target":"door-a"}',
        timeout_seconds=5,
    )

    create_command = next(
        command for command, _, _ in factory.calls if command[1] == "create"
    )
    start_process = next(
        process for command, _, process in factory.calls if command[1] == "start"
    )
    assert create_command[-2:] == ["python3", "worker.py"]
    assert bytes(start_process.stdin.data) == b'{"target":"door-a"}'
    assert result["exit_code"] == 0


def test_computer_sandbox_enforces_reserved_path_and_file_quota(
    tmp_path: Path,
) -> None:
    profile = SandboxProfile(
        enabled=True,
        workspace_root=tmp_path / "sandbox",
        image="fireclaw-sim:test",
        image_digest=_TEST_IMAGE_ID,
        max_file_bytes=8,
        max_workspace_bytes=12,
        max_workspace_files=2,
    )
    sandbox = ComputerSandbox(profile=profile)

    with pytest.raises(ValueError, match="reserved .fireclaw"):
        sandbox.write_file({"path": ".fireclaw/control", "content": "x"})
    sandbox.write_file({"path": "a.txt", "content": "12345678"})
    with pytest.raises(ValueError, match="max_file_bytes"):
        sandbox.write_file({"path": "large.txt", "content": "123456789"})
    with pytest.raises(ValueError, match="max_workspace_bytes"):
        sandbox.write_file({"path": "b.txt", "content": "12345"})


def test_computer_sandbox_rejects_oversized_existing_file_without_loading_it(
    tmp_path: Path,
) -> None:
    profile = SandboxProfile(
        enabled=True,
        workspace_root=tmp_path / "sandbox",
        image="fireclaw-sim:test",
        image_digest=_TEST_IMAGE_ID,
        max_file_bytes=16,
        max_workspace_bytes=1024,
    )
    sandbox = ComputerSandbox(profile=profile)
    oversized = sandbox.root / "sparse.txt"
    with oversized.open("wb") as handle:
        handle.truncate(17)

    with pytest.raises(ValueError, match="per-file size limit"):
        sandbox.read_file({"path": "sparse.txt"})


def test_agent_tool_runtime_bounds_handler_time_and_result_size(
    tmp_path: Path,
) -> None:
    host = FireClawPluginHost()

    def slow_handler(arguments):
        time.sleep(0.05)
        return {"ok": True}

    _register_test_tool(
        host,
        AgentTool(
            name="slow",
            description="Slow trusted test handler.",
            input_schema={"type": "object", "properties": {}},
            handler=slow_handler,
            max_execution_seconds=0.01,
        ),
        owner_plugin_id="test.slow",
    )
    _register_test_tool(
        host,
        AgentTool(
            name="large",
            description="Oversized trusted test result.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda arguments: {"value": "x" * 128},
            max_result_bytes=32,
        ),
        owner_plugin_id="test.large",
    )
    runtime = AgentToolRuntime(
        plugin_host=host,
        profile=_profile(tmp_path),
    )

    timed_out = runtime.execute("slow", {})
    oversized = runtime.execute("large", {})

    assert timed_out.error_code == "agent_tool_handler_timed_out"
    assert oversized.error_code == "agent_tool_result_invalid"


def test_computer_plugin_registers_atomically(tmp_path: Path) -> None:
    host = FireClawPluginHost()
    sandbox = ComputerSandbox(profile=_profile(tmp_path).sandbox)

    load_fireclaw_extensions(
        host,
        (_EXTENSIONS / "computer-tools",),
        mode="simulation",
        role="robot_agent",
        services={"fireclaw.agent-tools.computer.sandbox": sandbox},
        strict=True,
    )

    names = {
        contribution.contribution_id
        for contribution in host.contributions("tool")
    }
    assert names == {
        "computer_exec",
        "computer_list_files",
        "computer_read_file",
        "computer_write_file",
    }
