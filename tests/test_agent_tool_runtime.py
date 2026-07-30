from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fireclaw_core.agent.computer_tools import (
    ComputerSandbox,
    computer_agent_tools,
    register_computer_tool_plugin,
)
from fireclaw_core.agent.tool_runtime import (
    AgentTool,
    AgentToolRuntime,
    register_agent_tool,
)
from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
    execution_action_hash,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import (
    DEPLOYMENT_POLICY_ID,
    DeploymentProfile,
    SandboxProfile,
)


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
    register_agent_tool(
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
    register_agent_tool(
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
    register_agent_tool(
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
    register_agent_tool(
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
    calls: list[tuple[list[str], dict]] = []

    def runner(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "ok", "stderr": ""},
        )()

    profile = _profile(tmp_path).sandbox
    sandbox = ComputerSandbox(profile=profile, process_runner=runner)
    tools = {tool.name: tool for tool in computer_agent_tools(sandbox)}

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
    command, kwargs = calls[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--pull=never" in command
    assert "--network" in command
    assert "--cap-drop" in command
    assert "--read-only" in command
    assert command[-2:] == ["printf", "ok"]
    assert kwargs["check"] is False


def test_computer_plugin_registers_atomically(tmp_path: Path) -> None:
    host = FireClawPluginHost()
    sandbox = ComputerSandbox(profile=_profile(tmp_path).sandbox)

    register_computer_tool_plugin(host, sandbox)

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
