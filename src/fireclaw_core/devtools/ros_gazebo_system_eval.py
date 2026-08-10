"""Build immutable common-schema bundles from live ROS/Gazebo proofs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import traceback
from typing import Any, Sequence

from fireclaw_core.evaluation.artifacts import (
    EvaluationRunBundle,
    make_run_id,
    sha256_file,
)
from fireclaw_core.evaluation.contracts import EVALUATION_RUN_SCHEMA_VERSION
from fireclaw_core.evaluation.metrics import (
    SYSTEM_METRIC_DEFINITIONS,
    aggregate_system_records,
)
from fireclaw_core.evaluation.provenance import (
    file_identity,
    repository_snapshot,
    ros_gazebo_runtime_snapshot,
    runtime_snapshot,
)
from fireclaw_core.evaluation.system import (
    ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
    SystemCaseResult,
    collect_ros_gazebo_case,
)
from fireclaw_core.infra.log_redaction import redact_dict


def run_ros_gazebo_system_eval(
    *,
    source_proof_dirs: Sequence[Path],
    output_dir: Path,
    split: str = "development",
    repeat_indices: Sequence[int] | None = None,
    embed_source_proofs: bool = True,
    max_source_bytes: int = 512 * 1024 * 1024,
    run_id: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Normalize one or more trusted live proof directories."""

    sources = tuple(Path(item).resolve(strict=True) for item in source_proof_dirs)
    if not sources:
        raise ValueError("at least one source proof directory is required")
    if len(set(sources)) != len(sources):
        raise ValueError("source proof directories must be unique")
    repeats = tuple(repeat_indices or (0 for _ in sources))
    if len(repeats) != len(sources):
        raise ValueError(
            "repeat_indices must contain one value per source proof"
        )
    output = Path(output_dir).resolve(strict=False)
    for source in sources:
        if output == source:
            raise ValueError("output_dir must not equal a source proof directory")
        if _is_within(source, output):
            raise ValueError("source proof directory must not be inside output_dir")
    if any(_is_within(output, source) for source in sources) and len(sources) != 1:
        raise ValueError(
            "an output nested in a source proof is only valid for one source"
        )

    resolved_run_id = run_id or make_run_id("ros-gazebo-system")
    bundle = EvaluationRunBundle(output, run_id=resolved_run_id)
    root = (
        Path(repo_root).resolve(strict=False)
        if repo_root is not None
        else Path(__file__).resolve().parents[3]
    )
    collector_repository = repository_snapshot(root)
    started_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": "ros_gazebo_system",
        "status": "running",
        "started_at": started_at,
        "source_proof_count": len(sources),
        "runner": {
            "name": "fireclaw_core.devtools.ros_gazebo_system_eval",
            "mode": "offline_proof_collection",
            "invokes_ros": False,
            "invokes_gazebo": False,
            "embed_source_proofs": embed_source_proofs,
            "max_source_bytes_per_case": max_source_bytes,
        },
        "selection": {
            "exclusion_rule": "none",
            "retention_rule": (
                "retain every supplied proof, including failed, incomplete, "
                "cancelled, timed-out, escalated, and collection-error cases"
            ),
        },
    }
    bundle.write_json("run-manifest.json", redact_dict(manifest))
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    proof_index: list[dict[str, Any]] = []
    plugin_inventories: list[dict[str, Any]] = []
    tool_inventories: list[dict[str, Any]] = []
    source_repositories: list[dict[str, Any]] = []

    for index, (source, repeat_index) in enumerate(zip(sources, repeats)):
        try:
            case = collect_ros_gazebo_case(
                source,
                split=split,
                repeat_index=repeat_index,
                exclude_paths=(output,),
                max_source_bytes=max_source_bytes,
            )
            if collector_repository.get("dirty") is not False:
                _mark_paper_incomplete(
                    case,
                    "collector_repository_dirty",
                )
            embedding_error = _record_system_case(
                bundle,
                case,
                embed_source_proofs=embed_source_proofs,
            )
            if embedding_error is not None:
                errors.append(embedding_error)
            records.append(case.record)
            proof_index.append({
                "case_id": case.case_id,
                "source_run_id": case.record["source_run_id"],
                "source_path": str(source),
                "source_proof_sha256": case.record["source_proof_sha256"],
                "source_file_count": case.source_inventory["file_count"],
                "source_proof_bytes": case.source_inventory["total_bytes"],
                "embedded": embed_source_proofs and embedding_error is None,
            })
            plugin_inventories.append({
                "case_id": case.case_id,
                "inventory": case.plugin_inventory,
            })
            tool_inventories.append({
                "case_id": case.case_id,
                "inventory": case.tool_inventory,
            })
            source_repositories.append({
                "case_id": case.case_id,
                "repository": case.record.get("source_repository"),
            })
        except Exception as exc:
            error = redact_dict({
                "phase": "proof_collection",
                "source_index": index,
                "source_path": str(source),
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })
            errors.append(error)
            record = _failed_system_record(
                source=source,
                source_index=index,
                split=split,
                repeat_index=repeat_index,
                error=error,
            )
            records.append(record)
            case_prefix = Path("cases") / record["case_id"]
            bundle.write_json(case_prefix / "error.json", error)
            bundle.write_json(case_prefix / "scenario-record.json", record)
            proof_index.append({
                "case_id": record["case_id"],
                "source_path": str(source),
                "embedded": False,
                "error": error,
            })

    aggregate = aggregate_system_records(records)
    all_contracts_passed = (
        bool(records)
        and not errors
        and all(record.get("contract_passed") is True for record in records)
    )
    status = "pass" if all_contracts_passed else "warn"
    completed_at = datetime.now(timezone.utc).isoformat()
    paper_ready = bool(records) and all(
        record.get("paper_evidence_complete") is True for record in records
    )
    summary = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": "ros_gazebo_system",
        "status": status,
        "paper_evidence_complete": paper_ready,
        "scenario_count": len(records),
        "metrics": aggregate["metrics"],
        "metric_statistics": aggregate["metric_statistics"],
        "outcome_counts": aggregate["outcome_counts"],
        "scenario_type_counts": aggregate["scenario_type_counts"],
        "collision_metric_missing_count": aggregate[
            "collision_metric_missing_count"
        ],
        "split": split,
        "started_at": started_at,
        "completed_at": completed_at,
        "error_count": len(errors),
    }
    provenance = {
        "repository": collector_repository,
        "runtime": runtime_snapshot(),
        "collection_environment": ros_gazebo_runtime_snapshot(),
        "source_repositories": source_repositories,
        "runner_source": file_identity(__file__, repo_root=root),
        "scorer_source": file_identity(
            root / "src/fireclaw_core/evaluation/system.py",
            repo_root=root,
        ),
        "models": {
            "mission_planner": {
                "kind": "deterministic_acceptance_planner",
                "model_invoked": False,
            },
            "robot_agent": {
                "kind": "deterministic_acceptance_policy",
                "model_invoked": False,
            },
        },
        "openclaw_analogue": {
            "reused_shape": [
                "stable run identity",
                "replayable event transcript",
                "Tool projection inventory",
                "content-addressed evidence",
            ],
            "fireclaw_adaptation": (
                "ROS/Gazebo runtime provenance, physical safe-stop proof, "
                "typed diagnostics, bounded recovery, and canonical physical "
                "terminal outcomes"
            ),
        },
    }
    bundle.write_json("summary.json", redact_dict(summary))
    bundle.write_json("metric-definitions.json", SYSTEM_METRIC_DEFINITIONS)
    bundle.write_jsonl("scenarios.jsonl", records)
    bundle.write_jsonl("errors.jsonl", errors)
    bundle.write_json("source-proof-index.json", redact_dict({
        "proofs": proof_index,
    }))
    bundle.write_json("plugin-inventories.json", redact_dict({
        "cases": plugin_inventories,
    }))
    bundle.write_json("tool-inventories.json", redact_dict({
        "cases": tool_inventories,
    }))
    bundle.write_json("provenance.json", redact_dict(provenance))
    bundle.write_json("paper-summary.json", redact_dict({
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": "ros_gazebo_system",
        "status": status,
        "paper_evidence_complete": paper_ready,
        "split": split,
        "scenario_count": len(records),
        "metrics": aggregate["metrics"],
        "metric_statistics": aggregate["metric_statistics"],
        "outcome_counts": aggregate["outcome_counts"],
        "scenario_type_counts": aggregate["scenario_type_counts"],
        "collision_metric_missing_count": aggregate[
            "collision_metric_missing_count"
        ],
        "warnings": [
            *(
                []
                if paper_ready
                else [
                    "At least one case is dirty, development-only, or missing "
                    "paper provenance. Do not use as a final paper baseline."
                ]
            ),
            *(
                []
                if aggregate["collision_metric_missing_count"] == 0
                else [
                    "Collision instrumentation is missing for at least one "
                    "case; missing collision data is not a zero-collision result."
                ]
            ),
        ],
        "exclusions": [],
        "recompute_from": "scenarios.jsonl + metric-definitions.json",
    }))
    manifest.update({
        "status": status,
        "completed_at": completed_at,
        "scenario_count": len(records),
        "error_count": len(errors),
        "paper_evidence_complete": paper_ready,
        "outcome_counts": aggregate["outcome_counts"],
    })
    bundle.replace_json("run-manifest.json", redact_dict(manifest))
    artifact_manifest = bundle.finalize_artifact_manifest()
    return {
        **summary,
        "output_dir": str(bundle.run_dir),
        "artifact_count": artifact_manifest["artifact_count"],
    }


def _record_system_case(
    bundle: EvaluationRunBundle,
    case: SystemCaseResult,
    *,
    embed_source_proofs: bool,
) -> dict[str, Any] | None:
    prefix = Path("cases") / case.case_id
    embedding_error: dict[str, Any] | None = None
    if embed_source_proofs:
        try:
            expected_source_hashes = {
                str(item["path"]): str(item["sha256"])
                for item in case.source_inventory.get("files", [])
                if isinstance(item, dict)
                and item.get("kind") != "symlink"
                and isinstance(item.get("path"), str)
                and isinstance(item.get("sha256"), str)
            }
            for relative, source in case.source_files:
                copied = bundle.copy_file(
                    prefix / "source-proof" / relative,
                    source,
                )
                if sha256_file(copied) != expected_source_hashes.get(relative):
                    raise ValueError(
                        f"source proof changed while embedding: {relative}"
                    )
            for relative, source in case.asset_files:
                copied = bundle.copy_file(
                    prefix / "source-assets" / relative,
                    source,
                )
                label = relative.split("-", 1)[0]
                expected = (
                    case.asset_inventory.get("assets", {})
                    .get(label, {})
                    .get("actual_sha256")
                )
                if sha256_file(copied) != expected:
                    raise ValueError(
                        f"source asset changed while embedding: {relative}"
                    )
        except Exception as exc:
            embedding_error = redact_dict({
                "phase": "proof_embedding",
                "case_id": case.case_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            case.record["error"] = embedding_error
            case.score["checks"]["source_proof_embedded"] = False
            _mark_paper_incomplete(
                case,
                "source_proof_embedding_error",
            )
    else:
        _mark_paper_incomplete(case, "source_proof_not_embedded")
    bundle.write_json(prefix / "scenario.json", case.normalized_scenario)
    bundle.write_json(prefix / "source-manifest.json", redact_dict(
        case.source_manifest
    ))
    bundle.write_json(prefix / "source-artifact-inventory.json", redact_dict(
        case.source_inventory
    ))
    bundle.write_json(prefix / "asset-inventory.json", redact_dict(
        case.asset_inventory
    ))
    bundle.write_json(prefix / "plugin-inventory.json", redact_dict(
        case.plugin_inventory
    ))
    bundle.write_json(prefix / "tool-inventory.json", redact_dict(
        case.tool_inventory
    ))
    bundle.write_json(prefix / "system-versions.json", redact_dict(
        case.source_versions
    ))
    bundle.write_json(prefix / "evidence-summary.json", redact_dict(
        case.evidence_summary
    ))
    bundle.write_json(prefix / "score.json", redact_dict(case.score))
    if embedding_error is not None:
        bundle.write_json(prefix / "embedding-error.json", embedding_error)
    bundle.write_json(prefix / "scenario-record.json", redact_dict(
        case.record
    ))
    return embedding_error


def _mark_paper_incomplete(
    case: SystemCaseResult,
    reason: str,
) -> None:
    case.record["paper_evidence_complete"] = False
    case.record["missing_data"] = sorted(set([
        *case.record.get("missing_data", []),
        reason,
    ]))
    case.score["metric_values"]["paper_evidence_complete"] = False
    case.score["missing_data"] = sorted(set([
        *case.score.get("missing_data", []),
        reason,
    ]))


def _failed_system_record(
    *,
    source: Path,
    source_index: int,
    split: str,
    repeat_index: int,
    error: dict[str, Any],
) -> dict[str, Any]:
    case_id = f"collection-error-{source_index:03d}"
    return {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
        "case_id": case_id,
        "scenario_id": case_id,
        "scenario_version": "unknown",
        "suite_id": "fireclaw-gazebo-navigation-acceptance",
        "suite_version": "1.0.0",
        "lane": "ros_gazebo_system",
        "split": split,
        "seed": None,
        "repeat_index": repeat_index,
        "target_type": None,
        "target": None,
        "task_type": "navigation",
        "scenario_type": "collection_error",
        "source_path": str(source),
        "expected_terminal_outcome": None,
        "observed_terminal_outcome": None,
        "contract_passed": False,
        "task_success": False,
        "terminal_match": False,
        "final_report_present": False,
        "scheduler_evidence_present": False,
        "same_task_resume": False,
        "plugin_contract_met": False,
        "adapter_fallback_free": False,
        "event_reconstructable": False,
        "asset_integrity": False,
        "provenance_complete": False,
        "paper_evidence_complete": False,
        "safe_stop_applicable": False,
        "safe_stop_met": False,
        "diagnostics_applicable": False,
        "diagnostics_evidence_met": False,
        "recovery_applicable": False,
        "recovery_success": False,
        "escalation_applicable": False,
        "escalation_correct": False,
        "collision_metric_available": False,
        "collision_free": None,
        "collision_count": None,
        "system_latency_ms": None,
        "feedback_count": None,
        "goal_position_error_m": None,
        "safe_stop_latency_ms": None,
        "source_proof_bytes": None,
        "recovery_attempt_count": None,
        "missing_data": ["source_proof"],
        "error": error,
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize live Gazebo acceptance proofs into an immutable "
            "ros_gazebo_system evaluation bundle."
        ),
    )
    parser.add_argument(
        "--source-proof",
        action="append",
        required=True,
        help="Gazebo acceptance proof directory; repeat for multiple cases.",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--split", default="development")
    parser.add_argument(
        "--repeat-index",
        action="append",
        type=int,
        default=None,
        help="One repeat index per --source-proof; defaults to zero.",
    )
    parser.add_argument(
        "--reference-only",
        action="store_true",
        help=(
            "Write hashes and paths without embedding raw source proof files; "
            "not recommended for archival paper bundles."
        ),
    )
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=512 * 1024 * 1024,
    )
    args = parser.parse_args(argv)
    try:
        result = run_ros_gazebo_system_eval(
            source_proof_dirs=[Path(item) for item in args.source_proof],
            output_dir=Path(args.output_dir),
            split=args.split,
            repeat_indices=args.repeat_index,
            embed_source_proofs=not args.reference_only,
            max_source_bytes=args.max_source_bytes,
            run_id=args.run_id,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
