"""Provider-owned Plugin activation example."""

from __future__ import annotations

from typing import Any

from fireclaw_plugin_sdk import PluginApi, ToolSpec


def _example_status(_arguments: dict[str, Any]) -> dict[str, Any]:
    return {"status": "succeeded", "plugin": "example"}


def register(api: PluginApi) -> None:
    """Register this Plugin's Tools through the public SDK contract."""

    api.register_tool(
        ToolSpec(
            name="example_status",
            description="Read a deterministic status from the example Plugin.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=_example_status,
            metadata={"extension_owned": True},
        )
    )
