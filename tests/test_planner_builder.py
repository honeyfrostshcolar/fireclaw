from __future__ import annotations

import pytest

from fireclaw_core.planner.planner_builder import build_planner


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
