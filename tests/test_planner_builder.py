from __future__ import annotations

import json

import pytest

from fireclaw_core.planner.planner_builder import build_planner
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile

_TEST_IMAGE_ID = "sha256:" + ("a" * 64)


def test_build_planner_deterministic():
    planner = build_planner(planner_type="deterministic")
    # Deterministic planner returns clarify when no robots are available
    result = planner.plan("去二楼搜索受困人员")
    assert result.status == "clarify"
    assert result.intent is None


def test_build_planner_llm_missing_params():
    with pytest.raises(ValueError, match="provider_base_url"):
        build_planner(planner_type="llm")


def test_build_planner_llm_with_params():
    planner = build_planner(
        planner_type="llm",
        provider_base_url="https://api.example.com/v1",
        provider_api_key="test-key",
        model="gpt-4o",
    )
    assert planner is not None


def test_build_planner_projects_simulation_computer_tools(tmp_path):
    planner = build_planner(
        planner_type="llm",
        provider_base_url="https://api.example.com/v1",
        provider_api_key="test-key",
        model="gpt-4o",
        deployment_profile=DeploymentProfile(
            mode="simulation",
            role="mission_agent",
            sandbox=SandboxProfile(
                enabled=True,
                workspace_root=tmp_path / "workspace",
                image="fireclaw-agent-sim:test",
                image_digest=_TEST_IMAGE_ID,
            ),
        ),
    )

    assert planner.agent_tool_runtime is not None
    assert {
        tool["function"]["name"]
        for tool in planner.agent_tool_runtime.tool_schemas()
    } == {
        "computer_exec",
        "computer_list_files",
        "computer_read_file",
        "computer_write_file",
    }


def test_build_planner_uses_model_catalog_for_context_budget(tmp_path):
    catalog_path = tmp_path / "models.json"
    catalog_path.write_text(
        json.dumps({
            "models": [{
                "id": "local-model",
                "name": "Local Model",
                "provider": "local",
                "context_window": 16384,
                "max_tokens": 1024,
                "supports_tools": True,
                "tokenizer_id": "local/tokenizer",
            }],
        }),
        encoding="utf-8",
    )

    planner = build_planner(
        planner_type="llm",
        provider_base_url="https://api.example.com/v1",
        provider_api_key="test-key",
        model="local-model",
        model_catalog_path=str(catalog_path),
    )

    descriptor = planner._provider_runtime.select_model(
        task="mission_planning"
    )
    assert descriptor.context_window == 16384
    assert descriptor.max_tokens == 1024
    assert descriptor.tokenizer_id == "local/tokenizer"
