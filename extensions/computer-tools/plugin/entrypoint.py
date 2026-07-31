"""Built-in computer Tool Plugin.

The sandbox implementation is supplied by the host as a service.  This
extension owns the LLM-facing schemas and never grants a host shell or a
filesystem path outside that sandbox.
"""

from __future__ import annotations

from typing import Any

from fireclaw_plugin_sdk import PluginApi, ToolSpec


_MAX_FILE_CHARS = 200_000


def _tools(sandbox: Any) -> tuple[ToolSpec, ...]:
    common = {
        "roles": ("mission_agent", "robot_agent"),
        "modes": ("simulation", "real"),
        "requires_sandbox": True,
        "metadata": {"family": "computer", "extension_owned": True},
    }
    return (
        ToolSpec(
            name="computer_list_files",
            description="List files inside the configured agent sandbox workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "maxLength": 1024},
                    "max_depth": {"type": "integer", "minimum": 0, "maximum": 8},
                },
                "additionalProperties": False,
            },
            handler=sandbox.list_files,
            effect="read",
            **common,
        ),
        ToolSpec(
            name="computer_read_file",
            description="Read one UTF-8 text file inside the configured sandbox.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 1024},
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
        ToolSpec(
            name="computer_write_file",
            description=(
                "Create or atomically replace a UTF-8 text file inside the "
                "sandbox. Existing files require the SHA-256 returned by "
                "computer_read_file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "content": {"type": "string", "maxLength": _MAX_FILE_CHARS},
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
        ToolSpec(
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
                        "items": {"type": "string", "minLength": 1, "maxLength": 4096},
                        "minItems": 1,
                        "maxItems": 128,
                    },
                    "cwd": {"type": "string", "maxLength": 1024},
                    "timeout_seconds": {"type": "number", "minimum": 0.1},
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            handler=sandbox.execute,
            effect="process",
            **common,
        ),
    )


def register(api: PluginApi) -> None:
    sandbox = api.services.get("computer_sandbox")
    if sandbox is None or not bool(api.config.get("enabled", True)):
        return
    for tool in _tools(sandbox):
        api.register_tool(tool, metadata=dict(tool.metadata))
