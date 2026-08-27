"""Offline LLM planning evaluation with immutable paper-oriented evidence.

This lane invokes the production Mission planning policy and bounded
``MissionDeliberationRuntime`` over fixture-owned immutable snapshots. It does
not construct a Mission Gateway, scheduler, Robot Gateway, Adapter, ROS node,
or Gazebo process, so a model proposal can be compiled and scored but never
physically dispatched.

Usage::

    python -m \
      fireclaw_core.devtools.llm_planning_eval \
      --config fireclaw.sim.toml \
      --scenarios tests/fixtures/embodied_eval/planning_scenarios.json \
      --output-dir results/embodied-eval/llm-planning-example \
      --temperature 0
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import traceback
from typing import Any
from urllib.parse import urlparse

from fireclaw_core.evaluation.artifacts import (
    EvaluationRunBundle,
    canonical_json_sha256,
    make_run_id,
)
from fireclaw_core.evaluation.contracts import (
    EVALUATION_RUN_SCHEMA_VERSION,
    EvaluationScenario,
    load_evaluation_suite,
)
from fireclaw_core.evaluation.metrics import (
    PLANNING_METRIC_DEFINITIONS,
    aggregate_planning_records,
)
from fireclaw_core.evaluation.planning import (
    PLANNING_EVALUATION_PROTOCOL_VERSION,
    PlanningCaseResult,
    evaluate_planning_case,
    planning_tool_inventory,
)
from fireclaw_core.evaluation.provenance import (
    file_identity,
    repository_snapshot,
    runtime_snapshot,
)
from fireclaw_core.gateway.config import find_config, load_config
from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationLimits,
)
from fireclaw_core.provider.model_catalog import ModelCatalog
from fireclaw_core.provider.provider import OpenAICompatProvider
from fireclaw_core.provider.provider_runtime import (
    ProviderRuntime,
    SimpleProviderRuntime,
)


def run_llm_planning_eval(
    *,
    scenarios_path: Path,
    output_dir: Path,
    provider_runtime: ProviderRuntime,
    provider_name: str,
    requested_model: str,
    temperature: float = 0.0,
    planning_timeout_seconds: float = 240.0,
    max_iterations: int = 4,
    max_observations: int = 3,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
    provider_metadata: dict[str, Any] | None = None,
    run_id: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Run a versioned LLM planning suite without physical dispatch."""

    resolved_run_id = run_id or make_run_id("llm-planning")
    bundle = EvaluationRunBundle(output_dir, run_id=resolved_run_id)
    root = (
        Path(repo_root).resolve(strict=False)
        if repo_root is not None
        else Path(__file__).resolve().parents[3]
    )
    started_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": PLANNING_EVALUATION_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": "llm_planning",
        "status": "running",
        "started_at": started_at,
        "scenario_source": str(Path(scenarios_path).resolve(strict=False)),
        "runner": {
            "name": "fireclaw_core.devtools.llm_planning_eval",
            "planning_runtime": "MissionDeliberationRuntime",
            "max_iterations": max_iterations,
            "max_observations": max_observations,
            "timeout_seconds": planning_timeout_seconds,
            "physical_dispatch_allowed": False,
            "gateway_constructed": False,
            "scheduler_constructed": False,
            "robot_adapter_constructed": False,
        },
        "selection": {
            "exclusion_rule": "none",
            "retention_rule": (
                "retain every attempted case, provider error, parser error, "
                "validation rejection, and successful proposal"
            ),
        },
    }
    bundle.write_json("run-manifest.json", redact_dict(manifest))
    model_config = {
        "provider": provider_name,
        "requested_model": requested_model,
        "temperature": temperature,
        "seed_source": "scenario.seed",
        "seed_forwarding": "provider_request.seed",
        "seed_caveat": (
            "A forwarded seed does not guarantee deterministic output; "
            "provider implementation, model revision, and backend batching "
            "may still vary."
        ),
        "input_cost_per_million_tokens_usd": input_cost_per_million,
        "output_cost_per_million_tokens_usd": output_cost_per_million,
        "provider_metadata": _json_copy(provider_metadata or {}),
    }
    base_provenance: dict[str, Any] = {
        "repository": repository_snapshot(root),
        "runtime": runtime_snapshot(),
        "model_configuration": model_config,
        "provider_runtime_status_before": _json_copy(
            provider_runtime.status()
        ),
        "runner_source": file_identity(__file__, repo_root=root),
        "prompt_and_tool_source": file_identity(
            root / "src/fireclaw_core/planner/llm_planner.py",
            repo_root=root,
        ),
        "planning_runtime_source": file_identity(
            root / "src/fireclaw_core/mission/mission_deliberation.py",
            repo_root=root,
        ),
        "system_context": {
            "applicable": False,
            "reason": (
                "ROS, Gazebo, map-server state, and navigation execution "
                "belong to ros_gazebo_system; this lane uses frozen fixtures."
            ),
            "ros_version": None,
            "gazebo_version": None,
            "map": None,
            "navigation_parameters": None,
        },
        "openclaw_analogue": {
            "reused_shape": [
                "stable run identity",
                "model/provider attribution",
                "normalized usage accounting",
                "replayable model transcript",
                "Tool projection snapshot",
            ],
            "fireclaw_adaptation": (
                "typed Mission snapshot, bounded read/propose loop, "
                "deterministic graph compilation, safety rejection evidence, "
                "and an explicit no-dispatch boundary"
            ),
        },
    }

    try:
        _validate_run_configuration(
            provider_name=provider_name,
            requested_model=requested_model,
            temperature=temperature,
            planning_timeout_seconds=planning_timeout_seconds,
            max_iterations=max_iterations,
            max_observations=max_observations,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        )
        suite = load_evaluation_suite(scenarios_path)
        if suite.lane != "llm_planning":
            raise ValueError(
                "llm_planning_eval only runs lane=llm_planning; "
                f"received {suite.lane!r}"
            )
    except Exception as exc:
        return _finalize_configuration_error(
            bundle=bundle,
            manifest=manifest,
            base_provenance=base_provenance,
            started_at=started_at,
            error=exc,
        )

    bundle.write_json("scenario-suite.json", redact_dict(suite.to_dict()))
    bundle.write_json("model-config.json", redact_dict(model_config))
    scenario_records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    tool_evidence: list[dict[str, Any]] = []
    plugin_inventory: dict[str, Any] | None = None
    limits = MissionDeliberationLimits(
        max_iterations=max_iterations,
        timeout_seconds=planning_timeout_seconds,
        max_observations=max_observations,
        max_agent_tool_executions=0,
    )
    for scenario in suite.scenarios:
        case_prefix = Path("cases") / scenario.case_id
        bundle.write_json(case_prefix / "scenario.json", scenario.to_dict())
        case_started = datetime.now(timezone.utc)
        try:
            case = evaluate_planning_case(
                scenario,
                provider_runtime=provider_runtime,
                provider_name=provider_name,
                temperature=temperature,
                limits=limits,
                input_cost_per_million=input_cost_per_million,
                output_cost_per_million=output_cost_per_million,
            )
            current_inventory = case.plugin_inventory
            if plugin_inventory is None:
                plugin_inventory = current_inventory
            elif (
                current_inventory.get("inventory_sha256")
                != plugin_inventory.get("inventory_sha256")
            ):
                drift = {
                    "phase": "scenario",
                    "case_id": scenario.case_id,
                    "error_type": "PluginInventoryDrift",
                    "message": (
                        "Planner Plugin inventory changed within one "
                        "evaluation run."
                    ),
                }
                errors.append(drift)
                case.record["contract_passed"] = False
                case.record["error"] = drift
                case.score["contract_passed"] = False
                case.score["checks"]["stable_plugin_inventory"] = False
            _record_case_artifacts(bundle, case_prefix, case)
            scenario_records.append(case.record)
            tool_evidence.append({
                "case_id": scenario.case_id,
                "provider_calls": case.provider_calls,
            })
            bundle.write_json(
                case_prefix / "scenario-record.json",
                redact_dict(case.record),
            )
        except Exception as exc:
            completed = datetime.now(timezone.utc)
            error = redact_dict({
                "phase": "scenario",
                "case_id": scenario.case_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })
            errors.append(error)
            failed = _failed_case_record(
                scenario,
                error=error,
                latency_ms=(completed - case_started).total_seconds() * 1000.0,
            )
            scenario_records.append(failed)
            bundle.write_json(case_prefix / "error.json", error)
            bundle.write_json(
                case_prefix / "scenario-record.json",
                failed,
            )

    aggregate = aggregate_planning_records(scenario_records)
    all_passed = bool(scenario_records) and all(
        record.get("contract_passed") is True
        for record in scenario_records
    )
    status = "pass" if all_passed else "warn"
    completed_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": PLANNING_EVALUATION_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": suite.lane,
        "status": status,
        "scenario_count": len(scenario_records),
        "metrics": aggregate["metrics"],
        "metric_statistics": aggregate["metric_statistics"],
        "planning_status_counts": aggregate["planning_status_counts"],
        "safety_rejection_count": aggregate["safety_rejection_count"],
        "unexposed_tool_call_count": aggregate[
            "unexposed_tool_call_count"
        ],
        "tool_protocol_violation_count": aggregate[
            "tool_protocol_violation_count"
        ],
        "tool_protocol_repair_count": aggregate[
            "tool_protocol_repair_count"
        ],
        "suite": {
            "suite_id": suite.suite_id,
            "suite_version": suite.suite_version,
            "source_sha256": suite.source_sha256,
        },
        "started_at": started_at,
        "completed_at": completed_at,
        "error_count": len(errors),
    }
    dynamic_tool_inventory = planning_tool_inventory(tool_evidence)
    resolved_plugins = plugin_inventory or {
        "plugins": [],
        "contributions": [],
        "inventory_sha256": canonical_json_sha256({}),
        "missing_reason": "no scenario reached planner construction",
    }
    actual_models = sorted({
        str(call["response"]["model"])
        for item in tool_evidence
        for call in item["provider_calls"]
        if call.get("status") == "success"
        and isinstance(call.get("response"), dict)
        and call["response"].get("model") is not None
    })
    provenance = {
        **base_provenance,
        "provider_runtime_status_after": _json_copy(
            provider_runtime.status()
        ),
        "actual_response_models": actual_models,
        "suite": {
            "suite_id": suite.suite_id,
            "suite_version": suite.suite_version,
            "source_path": suite.source_path,
            "source_sha256": suite.source_sha256,
            "legacy_source_format": suite.legacy_source_format,
        },
        "randomness": {
            "scenario_and_model_seeds": sorted({
                scenario.seed for scenario in suite.scenarios
            }),
            "temperature": temperature,
            "seed_forwarded_rate": aggregate["metrics"][
                "seed_forwarded_rate"
            ],
            "determinism_guaranteed": False,
        },
        "inventories": {
            "plugin_inventory_sha256": resolved_plugins[
                "inventory_sha256"
            ],
            "planning_tool_inventory_sha256": dynamic_tool_inventory[
                "inventory_sha256"
            ],
        },
    }

    bundle.write_json("summary.json", redact_dict(summary))
    bundle.write_json(
        "metric-definitions.json",
        PLANNING_METRIC_DEFINITIONS,
    )
    bundle.write_jsonl("scenarios.jsonl", scenario_records)
    bundle.write_jsonl("errors.jsonl", errors)
    bundle.write_json(
        "plugin-inventory.json",
        redact_dict(resolved_plugins),
    )
    bundle.write_json(
        "planning-tool-inventory.json",
        redact_dict(dynamic_tool_inventory),
    )
    bundle.write_json("provenance.json", redact_dict(provenance))
    bundle.write_json("paper-summary.json", redact_dict({
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": PLANNING_EVALUATION_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": suite.lane,
        "suite_id": suite.suite_id,
        "suite_version": suite.suite_version,
        "scenario_count": len(scenario_records),
        "status": status,
        "requested_model": requested_model,
        "actual_response_models": actual_models,
        "temperature": temperature,
        "seeds": provenance["randomness"]["scenario_and_model_seeds"],
        "metrics": aggregate["metrics"],
        "metric_statistics": aggregate["metric_statistics"],
        "planning_status_counts": aggregate["planning_status_counts"],
        "safety_rejection_count": aggregate["safety_rejection_count"],
        "unsafe_proposal_proxy_warning": (
            "Runtime safety rejections are a reproducible proxy; paper "
            "claims about unsafe proposals still require an independent "
            "annotation protocol."
        ),
        "exclusions": [],
        "recompute_from": "scenarios.jsonl + metric-definitions.json",
    }))
    manifest.update({
        "status": status,
        "completed_at": completed_at,
        "suite": summary["suite"],
        "scenario_count": len(scenario_records),
        "error_count": len(errors),
        "planning_status_counts": aggregate["planning_status_counts"],
        "inventories": provenance["inventories"],
    })
    bundle.replace_json("run-manifest.json", redact_dict(manifest))
    artifact_manifest = bundle.finalize_artifact_manifest()
    return {
        **summary,
        "output_dir": str(bundle.run_dir),
        "artifact_count": artifact_manifest["artifact_count"],
    }


def _record_case_artifacts(
    bundle: EvaluationRunBundle,
    prefix: Path,
    case: PlanningCaseResult,
) -> None:
    bundle.write_json(prefix / "input.json", redact_dict(case.input_snapshot))
    bundle.write_json(
        prefix / "state-snapshot.json",
        redact_dict(case.state_snapshot),
    )
    bundle.write_json(
        prefix / "planner-context.json",
        redact_dict(case.planner_context),
    )
    bundle.write_json(
        prefix / "deliberation-result.json",
        redact_dict(case.deliberation_result),
    )
    bundle.write_json(
        prefix / "planner-output.json",
        redact_dict(case.planner_output),
    )
    bundle.write_json(
        prefix / "provider-calls.json",
        redact_dict({"calls": case.provider_calls}),
    )
    bundle.write_json(
        prefix / "harness-traces.json",
        redact_dict({"traces": case.harness_traces}),
    )
    bundle.write_json(prefix / "score.json", redact_dict(case.score))
    bundle.write_json(
        prefix / "plugin-inventory.json",
        redact_dict(case.plugin_inventory),
    )


def _failed_case_record(
    scenario: EvaluationScenario,
    *,
    error: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "case_id": scenario.case_id,
        "scenario_id": scenario.scenario_id,
        "scenario_version": scenario.scenario_version,
        "suite_id": scenario.suite_id,
        "suite_version": scenario.suite_version,
        "lane": scenario.lane,
        "split": scenario.split,
        "seed": scenario.seed,
        "repeat_index": scenario.repeat_index,
        "target_type": scenario.target_type,
        "task_type": scenario.task_type,
        "observed_planning_status": "runner_error",
        "planning_latency_ms": round(max(0.0, latency_ms), 6),
        "contract_passed": False,
        "planning_success": False,
        "first_try_clean": False,
        "tool_protocol_valid_first_try": False,
        "planning_recovery_applicable": False,
        "planning_recovered": False,
        "target_match": False,
        "capability_match": False,
        "intent_match": False,
        "task_count_match": False,
        "task_type_match": False,
        "required_operations_met": False,
        "safety_rejection_free": False,
        "no_dispatch": False,
        "provider_success": False,
        "seed_forwarded": False,
        "model_call_count": None,
        "tool_protocol_violation_count": 0,
        "tool_protocol_repair_count": 0,
        "safety_rejection_count": 0,
        "unexposed_tool_call_count": 0,
        "physical_dispatch_count": 0,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "estimated_cost_usd": None,
        "usage_reported": False,
        "error": error,
    }


def _validate_run_configuration(
    *,
    provider_name: str,
    requested_model: str,
    temperature: float,
    planning_timeout_seconds: float,
    max_iterations: int,
    max_observations: int,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> None:
    if not provider_name.strip():
        raise ValueError("provider_name must not be empty")
    if not requested_model.strip():
        raise ValueError("requested_model must not be empty")
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not isfinite(float(temperature))
        or not 0.0 <= float(temperature) <= 2.0
    ):
        raise ValueError("temperature must be between 0 and 2")
    if (
        isinstance(planning_timeout_seconds, bool)
        or not isinstance(planning_timeout_seconds, (int, float))
        or not isfinite(float(planning_timeout_seconds))
        or planning_timeout_seconds <= 0
    ):
        raise ValueError("planning_timeout_seconds must be positive and finite")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations <= 0
    ):
        raise ValueError("max_iterations must be a positive integer")
    if (
        isinstance(max_observations, bool)
        or not isinstance(max_observations, int)
        or max_observations < 0
    ):
        raise ValueError("max_observations must be a non-negative integer")
    for label, value in (
        ("input_cost_per_million", input_cost_per_million),
        ("output_cost_per_million", output_cost_per_million),
    ):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or value < 0
        ):
            raise ValueError(f"{label} must be non-negative and finite")


def _finalize_configuration_error(
    *,
    bundle: EvaluationRunBundle,
    manifest: dict[str, Any],
    base_provenance: dict[str, Any],
    started_at: str,
    error: Exception,
) -> dict[str, Any]:
    error_record = redact_dict({
        "phase": "configuration",
        "error_type": type(error).__name__,
        "message": str(error),
    })
    aggregate = aggregate_planning_records([])
    completed_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": PLANNING_EVALUATION_PROTOCOL_VERSION,
        "run_id": bundle.run_id,
        "lane": "llm_planning",
        "status": "error",
        "scenario_count": 0,
        "metrics": aggregate["metrics"],
        "metric_statistics": aggregate["metric_statistics"],
        "planning_status_counts": aggregate["planning_status_counts"],
        "safety_rejection_count": 0,
        "unexposed_tool_call_count": 0,
        "started_at": started_at,
        "completed_at": completed_at,
        "error": error_record,
    }
    bundle.write_json("summary.json", summary)
    bundle.write_json(
        "metric-definitions.json",
        PLANNING_METRIC_DEFINITIONS,
    )
    bundle.write_jsonl("scenarios.jsonl", [])
    bundle.write_jsonl("errors.jsonl", [error_record])
    bundle.write_json("provenance.json", redact_dict(base_provenance))
    manifest.update({
        "status": "error",
        "completed_at": completed_at,
        "scenario_count": 0,
        "error": error_record,
    })
    bundle.replace_json("run-manifest.json", redact_dict(manifest))
    artifact_manifest = bundle.finalize_artifact_manifest()
    return {
        **summary,
        "output_dir": str(bundle.run_dir),
        "artifact_count": artifact_manifest["artifact_count"],
    }


def _catalog_metadata(
    *,
    model: str,
    model_catalog_path: Path | None,
) -> tuple[dict[str, Any], float | None, float | None]:
    if model_catalog_path is None:
        return {
            "catalog_configured": False,
            "requested_model": model,
        }, None, None
    catalog = ModelCatalog(model_catalog_path)
    descriptor = catalog.resolve(model)
    return {
        "catalog_configured": True,
        "catalog_path_sha256": _file_sha256(model_catalog_path),
        "descriptor": {
            "id": descriptor.id,
            "name": descriptor.name,
            "provider": descriptor.provider,
            "context_window": descriptor.context_window,
            "max_tokens": descriptor.max_tokens,
            "supports_tools": descriptor.supports_tools,
            "cost_input_per_million_tokens_usd": descriptor.cost_input,
            "cost_output_per_million_tokens_usd": descriptor.cost_output,
            "tokenizer_id": descriptor.tokenizer_id,
        },
    }, descriptor.cost_input, descriptor.cost_output


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _nonempty_config_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    value = value.strip()
    return value or None


def _provider_name_from_url(base_url: str) -> str:
    hostname = urlparse(base_url).hostname
    return hostname or "openai-compatible-provider"


def _resolve_provider_settings(
    *,
    config_path: Path | None,
    config: dict[str, Any],
    provider_base_url: str | None,
    provider_name: str | None,
    model: str | None,
    api_key_env: str | None,
    model_catalog: str | None,
) -> dict[str, Any]:
    base_url = _nonempty_config_string(
        provider_base_url
        if provider_base_url is not None
        else config.get("provider_base_url"),
        field_name="provider_base_url",
    )
    if base_url is None:
        raise ValueError(
            "provider_base_url is required; pass --provider-base-url or "
            "set [provider].base_url in fireclaw.sim.toml"
        )
    resolved_name = _nonempty_config_string(
        provider_name
        if provider_name is not None
        else config.get("provider_name"),
        field_name="provider_name",
    ) or _provider_name_from_url(base_url)
    resolved_model = _nonempty_config_string(
        model if model is not None else config.get("model"),
        field_name="model",
    )
    if resolved_model is None:
        raise ValueError(
            "model is required; pass --model or set [provider].model in the "
            "selected mode-specific TOML"
        )
    resolved_api_key_env = _nonempty_config_string(
        api_key_env
        if api_key_env is not None
        else config.get("provider_api_key_env"),
        field_name="api_key_env",
    ) or "FIRECLAW_PROVIDER_API_KEY"
    configured_api_key = _nonempty_config_string(
        config.get("provider_api_key"),
        field_name="provider.api_key",
    )
    if api_key_env is None and configured_api_key is not None:
        api_key = configured_api_key
        credential_source = {
            "kind": "config_file",
            "name": "[provider].api_key",
            "secret_value_recorded": False,
        }
    else:
        api_key = os.environ.get(resolved_api_key_env)
        credential_source = {
            "kind": "environment_variable",
            "name": resolved_api_key_env,
            "secret_value_recorded": False,
        }
    if not api_key:
        raise ValueError(
            "Provider credential is missing; set [provider].api_key in the "
            "selected mode-specific TOML or the configured API-key environment variable "
            f"{resolved_api_key_env!r}"
        )

    catalog_value = (
        model_catalog
        if model_catalog is not None
        else config.get("model_catalog_path")
    )
    catalog_path: Path | None = None
    if catalog_value is not None:
        catalog_raw = _nonempty_config_string(
            catalog_value,
            field_name="model_catalog",
        )
        if catalog_raw is not None:
            catalog_path = Path(catalog_raw)
            if not catalog_path.is_absolute() and model_catalog is None:
                catalog_path = (
                    (config_path.parent if config_path else Path.cwd())
                    / catalog_path
                )
            catalog_path = catalog_path.resolve(strict=True)
    return {
        "base_url": base_url,
        "provider_name": resolved_name,
        "model": resolved_model,
        "api_key": api_key,
        "api_key_env": resolved_api_key_env,
        "thinking": config.get("provider_thinking"),
        "credential_source": credential_source,
        "catalog_path": catalog_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the offline LLM planning lane and write an immutable "
            "evaluation proof bundle."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to a mode-specific TOML; legacy ./fireclaw.toml is only "
            "auto-detected for compatibility. Explicit provider flags override "
            "[provider]."
        ),
    )
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--provider-base-url", default=None)
    parser.add_argument("--provider-name", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--api-key-env",
        default=None,
        help=(
            "Environment variable containing the provider API key; the key "
            "is never written to artifacts. [provider].api_key_env is used "
            "when this flag is omitted."
        ),
    )
    parser.add_argument("--model-catalog", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--provider-timeout",
        type=float,
        default=60.0,
    )
    parser.add_argument(
        "--planning-timeout",
        type=float,
        default=240.0,
    )
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--max-observations", type=int, default=3)
    args = parser.parse_args(argv)

    try:
        config_path = find_config(args.config)
        if args.config is not None and config_path is None:
            raise ValueError(f"Config file not found: {args.config}")
        config = load_config(config_path) if config_path is not None else {}
        provider_settings = _resolve_provider_settings(
            config_path=(
                config_path.resolve(strict=True)
                if config_path is not None
                else None
            ),
            config=config,
            provider_base_url=args.provider_base_url,
            provider_name=args.provider_name,
            model=args.model,
            api_key_env=args.api_key_env,
            model_catalog=args.model_catalog,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False))
        return 1

    try:
        catalog_path = provider_settings["catalog_path"]
        catalog_metadata, input_cost, output_cost = _catalog_metadata(
            model=provider_settings["model"],
            model_catalog_path=catalog_path,
        )
        provider_kwargs: dict[str, Any] = {
            "base_url": provider_settings["base_url"],
            "api_key": provider_settings["api_key"],
            "timeout": args.provider_timeout,
        }
        if provider_settings["thinking"] is not None:
            provider_kwargs["thinking"] = provider_settings["thinking"]
        provider = OpenAICompatProvider(
            **provider_kwargs,
        )
        catalog = ModelCatalog(catalog_path) if catalog_path else None
        provider_runtime = SimpleProviderRuntime(
            provider=provider,
            model_id=provider_settings["model"],
            catalog=catalog,
        )
        result = run_llm_planning_eval(
            scenarios_path=Path(args.scenarios),
            output_dir=Path(args.output_dir),
            provider_runtime=provider_runtime,
            provider_name=provider_settings["provider_name"],
            requested_model=provider_settings["model"],
            temperature=args.temperature,
            planning_timeout_seconds=args.planning_timeout,
            max_iterations=args.max_iterations,
            max_observations=args.max_observations,
            input_cost_per_million=input_cost,
            output_cost_per_million=output_cost,
            provider_metadata={
                "endpoint_sha256": sha256(
                    provider_settings["base_url"].encode("utf-8")
                ).hexdigest(),
                "endpoint_recorded": False,
                "credential_source": provider_settings[
                    "credential_source"
                ],
                "config_source": (
                    {
                        "path": str(config_path),
                        "section": "provider",
                    }
                    if config_path is not None
                    else None
                ),
                "provider_timeout_seconds": args.provider_timeout,
                "model_catalog": catalog_metadata,
            },
            run_id=args.run_id,
        )
    except (FileExistsError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "error":
        return 1
    if result["status"] == "warn":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
