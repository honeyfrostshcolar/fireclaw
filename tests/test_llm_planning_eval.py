from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fireclaw_core.devtools.llm_planning_eval import (
    run_llm_planning_eval,
)
from fireclaw_core.evaluation.contracts import load_evaluation_suite
from fireclaw_core.evaluation.planning import (
    build_offline_planning_inputs,
)
from fireclaw_core.provider.model_catalog import ModelDescriptor
from fireclaw_core.provider.provider import (
    ChatCompletion,
    TokenUsage,
    ToolCall,
)


PLANNING_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "embodied_eval"
    / "planning_scenarios.json"
)


class _ScriptedPlanningRuntime:
    def __init__(self) -> None:
        self.call_count = 0
        self.received_seeds: list[int | None] = []

    def select_model(self, *, task, preferred_model=None):
        del task, preferred_model
        return ModelDescriptor(
            id="scripted-planner-v1",
            name="Scripted Planner",
            provider="test",
            context_window=65_536,
            max_tokens=8_192,
            supports_tools=True,
            cost_input=1.0,
            cost_output=2.0,
        )

    def status(self):
        return {
            "type": "scripted-test",
            "model": "scripted-planner-v1",
        }

    def chat_completion(
        self,
        *,
        messages,
        tools,
        temperature=0.0,
        max_tokens=4096,
        seed=None,
    ):
        del temperature, max_tokens
        self.call_count += 1
        self.received_seeds.append(seed)
        payload = json.loads(messages[-1]["content"])
        serialized = json.dumps(payload, ensure_ascii=False)
        iteration = max(_values_named(payload, "iteration") or [1])
        if "planning-area-navigation" in serialized and iteration == 1:
            call = ToolCall(
                id=f"call-{self.call_count}",
                name="inspect_mission_state",
                arguments={
                    "kind": "environment_beliefs",
                    "subject_id": "area-alpha",
                },
            )
        else:
            target: dict[str, Any]
            assumptions: list[dict[str, Any]] = []
            if "planning-area-navigation" in serialized:
                target = {"frame_id": "map", "area_id": "area-alpha"}
                assumptions = [
                    {
                        "belief_id": belief_id,
                        "expected_value": True,
                        "knowledge_refs": [],
                    }
                    for belief_id in _belief_id_enum(tools)
                ]
            elif "planning-entity-navigation" in serialized:
                target = {
                    "frame_id": "map",
                    "entity_id": "victim-marker-7",
                }
            else:
                target = {
                    "frame_id": "map",
                    "pose": {"x": 1.5, "y": -0.5, "yaw": 0.0},
                }
            call = ToolCall(
                id=f"call-{self.call_count}",
                name="propose_task_graph",
                arguments={
                    "intent": "patrol",
                    "nodes": [
                        {
                            "node_id": "navigate_target",
                            "task_type": "navigation",
                            "command": "Navigate to the typed target.",
                            "target": target,
                            "capability_required": "navigate",
                            "completion_goal": (
                                "Reach the typed target with navigation evidence."
                            ),
                            "depends_on": [],
                            "execution_mode": "parallel",
                            "belief_assumptions": assumptions,
                        }
                    ],
                    "knowledge_refs": [],
                },
            )
        return ChatCompletion(
            content=None,
            tool_calls=[call],
            usage=TokenUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
            model="scripted-planner-v1",
            finish_reason="tool_calls",
        )


def _values_named(value: Any, name: str) -> list[int]:
    values: list[int] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == name and isinstance(item, int):
                values.append(item)
            values.extend(_values_named(item, name))
    elif isinstance(value, list):
        for item in value:
            values.extend(_values_named(item, name))
    return values


def _belief_id_enum(tools: list[dict[str, Any]]) -> list[str]:
    graph_tool = next(
        tool
        for tool in tools
        if tool["function"]["name"] == "propose_task_graph"
    )
    return list(
        graph_tool["function"]["parameters"]["properties"]["nodes"]
        ["items"]["properties"]["belief_assumptions"]["items"]
        ["properties"]["belief_id"]["enum"]
    )


def test_offline_inputs_build_valid_frozen_point_area_entity_snapshots() -> None:
    suite = load_evaluation_suite(PLANNING_FIXTURE)

    inputs = [build_offline_planning_inputs(item) for item in suite.scenarios]

    assert len(inputs) == 3
    assert {
        item.state_snapshot.snapshot_id.split(":")[1]
        for item in inputs
    } == {
        scenario.case_id for scenario in suite.scenarios
    }
    assert all(
        item.normalized_input["physical_execution"]["allowed"] is False
        for item in inputs
    )
    assert all(
        len(item.state_snapshot.environment_beliefs) == 2
        for item in inputs
    )


def test_llm_planning_lane_records_complete_no_dispatch_bundle(
    tmp_path: Path,
) -> None:
    runtime = _ScriptedPlanningRuntime()
    output_dir = tmp_path / "planning-run"

    result = run_llm_planning_eval(
        scenarios_path=PLANNING_FIXTURE,
        output_dir=output_dir,
        provider_runtime=runtime,
        provider_name="scripted-test",
        requested_model="scripted-planner-v1",
        temperature=0.0,
        planning_timeout_seconds=10.0,
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
        provider_metadata={"fixture_provider": True},
        run_id="planning-test-run",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert result["status"] == "pass"
    assert result["scenario_count"] == 3
    assert result["metrics"]["contract_pass_rate"] == 1.0
    assert result["metrics"]["planning_success_rate"] == 1.0
    assert result["metrics"]["target_match_rate"] == 1.0
    assert result["metrics"]["no_dispatch_rate"] == 1.0
    assert result["metrics"]["seed_forwarded_rate"] == 1.0
    assert result["metrics"]["unsafe_proposal_proxy_rate"] == 0.0
    assert runtime.received_seeds == [17, 17, 17, 17]

    records = [
        json.loads(line)
        for line in (output_dir / "scenarios.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 3
    assert all(record["physical_dispatch_count"] == 0 for record in records)
    assert all(record["contract_passed"] is True for record in records)
    assert sum(record["model_call_count"] for record in records) == 4
    assert sum(record["total_tokens"] for record in records) == 480

    area_dir = (
        output_dir
        / "cases"
        / "planning-area-navigation__seed-17__repeat-000"
    )
    area_result = json.loads(
        (area_dir / "deliberation-result.json").read_text(encoding="utf-8")
    )
    assert [item["operation"] for item in area_result["attempts"]] == [
        "inspect_state",
        "propose_plan",
    ]
    provider_calls = json.loads(
        (area_dir / "provider-calls.json").read_text(encoding="utf-8")
    )["calls"]
    assert all(call["request"]["seed"] == 17 for call in provider_calls)
    assert all(call["request"]["messages"] for call in provider_calls)
    assert all(call["request"]["tools"] for call in provider_calls)
    assert (output_dir / "artifact-manifest.json").is_file()


def test_llm_planning_lane_refuses_to_overwrite_existing_run(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / "old.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        run_llm_planning_eval(
            scenarios_path=PLANNING_FIXTURE,
            output_dir=output_dir,
            provider_runtime=_ScriptedPlanningRuntime(),
            provider_name="scripted-test",
            requested_model="scripted-planner-v1",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_iterations": 1.5}, "max_iterations"),
        ({"max_observations": 1.5}, "max_observations"),
    ],
)
def test_llm_planning_lane_rejects_non_integer_loop_limits(
    tmp_path: Path,
    overrides: dict[str, Any],
    message: str,
) -> None:
    result = run_llm_planning_eval(
        scenarios_path=PLANNING_FIXTURE,
        output_dir=tmp_path / message,
        provider_runtime=_ScriptedPlanningRuntime(),
        provider_name="scripted-test",
        requested_model="scripted-planner-v1",
        **overrides,
    )

    assert result["status"] == "error"
    assert message in result["error"]["message"]
