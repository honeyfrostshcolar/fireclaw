"""Build an immutable bundle for Gazebo collision positive controls."""

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
from fireclaw_core.evaluation.calibration import (
    CollisionCalibrationCaseResult,
    GAZEBO_COLLISION_CALIBRATION_METRIC_DEFINITIONS,
    GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION,
    collect_gazebo_collision_calibration_case,
)
from fireclaw_core.evaluation.contracts import EVALUATION_RUN_SCHEMA_VERSION
from fireclaw_core.evaluation.metrics import rate_statistics
from fireclaw_core.evaluation.provenance import (
    file_identity,
    repository_snapshot,
    ros_gazebo_runtime_snapshot,
    runtime_snapshot,
)
from fireclaw_core.infra.log_redaction import redact_dict


def run_gazebo_collision_calibration_eval(
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
    sources = tuple(Path(item).resolve(strict=True) for item in source_proof_dirs)
    if not sources:
        raise ValueError("at least one source proof directory is required")
    if len(set(sources)) != len(sources):
        raise ValueError("source proof directories must be unique")
    repeats = tuple(repeat_indices or (0 for _source in sources))
    if len(repeats) != len(sources):
        raise ValueError("repeat_indices must contain one value per source proof")
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

    resolved_run_id = run_id or make_run_id("gazebo-collision-calibration")
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
        "protocol_version": GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": "gazebo_collision_calibration",
        "status": "running",
        "started_at": started_at,
        "source_proof_count": len(sources),
        "excluded_from_task_metrics": True,
        "runner": {
            "name": (
                "fireclaw_core.devtools."
                "gazebo_collision_calibration_eval"
            ),
            "mode": "offline_positive_control_verification",
            "invokes_ros": False,
            "invokes_gazebo": False,
            "embed_source_proofs": embed_source_proofs,
            "max_source_bytes_per_case": max_source_bytes,
        },
        "selection": {
            "exclusion_rule": "none",
            "retention_rule": (
                "retain every supplied positive-control proof, including "
                "failed and incomplete calibration attempts"
            ),
        },
    }
    bundle.write_json("run-manifest.json", redact_dict(manifest))
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    proof_index: list[dict[str, Any]] = []

    for index, (source, repeat_index) in enumerate(zip(sources, repeats)):
        try:
            case = collect_gazebo_collision_calibration_case(
                source,
                split=split,
                repeat_index=repeat_index,
                exclude_paths=(output,),
                max_source_bytes=max_source_bytes,
            )
            if collector_repository.get("dirty") is not False:
                _mark_paper_incomplete(case, "collector_repository_dirty")
            embedding_error = _record_case(
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
        except Exception as exc:
            error = redact_dict({
                "phase": "calibration_proof_collection",
                "source_index": index,
                "source_path": str(source),
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })
            errors.append(error)
            record = _failed_record(
                source=source,
                source_index=index,
                split=split,
                repeat_index=repeat_index,
                error=error,
            )
            records.append(record)
            prefix = Path("cases") / record["case_id"]
            bundle.write_json(prefix / "error.json", error)
            bundle.write_json(prefix / "scenario-record.json", record)
            proof_index.append({
                "case_id": record["case_id"],
                "source_path": str(source),
                "embedded": False,
                "error": error,
            })

    passed_count = sum(
        record.get("calibration_passed") is True for record in records
    )
    detection_count = sum(
        record.get("collision_detection_success") is True
        for record in records
    )
    no_task_count = sum(
        record.get("no_task_dispatch") is True for record in records
    )
    stopped_count = sum(
        record.get("robot_remained_stopped") is True for record in records
    )
    status = (
        "pass"
        if records and not errors and passed_count == len(records)
        else "warn"
    )
    paper_ready = bool(records) and all(
        record.get("paper_evidence_complete") is True for record in records
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION,
        "run_id": resolved_run_id,
        "lane": "gazebo_collision_calibration",
        "status": status,
        "excluded_from_task_metrics": True,
        "task_metrics_applicable": False,
        "calibration_case_count": len(records),
        "calibration_pass_count": passed_count,
        "calibration_pass_rate": _rate(passed_count, len(records)),
        "collision_detection_success_count": detection_count,
        "collision_detection_success_rate": _rate(
            detection_count,
            len(records),
        ),
        "no_task_dispatch_rate": _rate(no_task_count, len(records)),
        "robot_remained_stopped_rate": _rate(stopped_count, len(records)),
        "metric_statistics": {
            "calibration_pass_rate": rate_statistics(
                passed_count,
                len(records),
            ),
            "collision_detection_success_rate": rate_statistics(
                detection_count,
                len(records),
            ),
            "no_task_dispatch_rate": rate_statistics(
                no_task_count,
                len(records),
            ),
            "robot_remained_stopped_rate": rate_statistics(
                stopped_count,
                len(records),
            ),
        },
        "total_probe_contact_states": sum(
            int(record.get("probe_contact_state_count") or 0)
            for record in records
        ),
        "total_probe_collision_episodes": sum(
            int(record.get("probe_collision_episode_count") or 0)
            for record in records
        ),
        "paper_evidence_complete": paper_ready,
        "split": split,
        "started_at": started_at,
        "completed_at": completed_at,
        "error_count": len(errors),
    }
    provenance = {
        "repository": collector_repository,
        "runtime": runtime_snapshot(),
        "collection_environment": ros_gazebo_runtime_snapshot(),
        "runner_source": file_identity(__file__, repo_root=root),
        "scorer_source": file_identity(
            root / "src/fireclaw_core/evaluation/calibration.py",
            repo_root=root,
        ),
        "measurement_boundary": {
            "simulation_only": True,
            "mission_or_robot_task_created": False,
            "agent_plugin_or_tool_invoked": False,
            "purpose": (
                "calibrate collision measurement sensitivity; never estimate "
                "navigation task success or collision-free task rate"
            ),
        },
    }
    bundle.write_json("summary.json", redact_dict(summary))
    bundle.write_json(
        "metric-definitions.json",
        GAZEBO_COLLISION_CALIBRATION_METRIC_DEFINITIONS,
    )
    bundle.write_jsonl("calibrations.jsonl", records)
    bundle.write_jsonl("errors.jsonl", errors)
    bundle.write_json("source-proof-index.json", redact_dict({
        "proofs": proof_index,
    }))
    bundle.write_json("provenance.json", redact_dict(provenance))
    bundle.write_json("paper-summary.json", redact_dict({
        **summary,
        "warnings": [] if paper_ready else [
            "This development/dirty calibration is not final paper evidence."
        ],
        "task_metric_exclusion": (
            "Positive controls are excluded from task success, collision-free, "
            "recovery, and terminal-outcome denominators."
        ),
        "recompute_from": "calibrations.jsonl + metric-definitions.json",
    }))
    manifest.update({
        "status": status,
        "completed_at": completed_at,
        "calibration_case_count": len(records),
        "error_count": len(errors),
        "paper_evidence_complete": paper_ready,
    })
    bundle.replace_json("run-manifest.json", redact_dict(manifest))
    artifact_manifest = bundle.finalize_artifact_manifest()
    return {
        **summary,
        "output_dir": str(bundle.run_dir),
        "artifact_count": artifact_manifest["artifact_count"],
    }


def _record_case(
    bundle: EvaluationRunBundle,
    case: CollisionCalibrationCaseResult,
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
            _mark_paper_incomplete(case, "source_proof_embedding_error")
    else:
        _mark_paper_incomplete(case, "source_proof_not_embedded")
    bundle.write_json(prefix / "scenario.json", case.scenario)
    bundle.write_json(
        prefix / "source-manifest.json",
        redact_dict(case.source_manifest),
    )
    bundle.write_json(
        prefix / "source-artifact-inventory.json",
        redact_dict(case.source_inventory),
    )
    bundle.write_json(
        prefix / "asset-inventory.json",
        redact_dict(case.asset_inventory),
    )
    bundle.write_json(
        prefix / "system-versions.json",
        redact_dict(case.source_versions),
    )
    bundle.write_json(
        prefix / "evidence-summary.json",
        redact_dict(case.evidence_summary),
    )
    bundle.write_json(prefix / "score.json", redact_dict(case.score))
    bundle.write_json(
        prefix / "calibration-record.json",
        redact_dict(case.record),
    )
    if embedding_error is not None:
        bundle.write_json(prefix / "embedding-error.json", embedding_error)
    return embedding_error


def _mark_paper_incomplete(
    case: CollisionCalibrationCaseResult,
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


def _failed_record(
    *,
    source: Path,
    source_index: int,
    split: str,
    repeat_index: int,
    error: dict[str, Any],
) -> dict[str, Any]:
    case_id = f"calibration-collection-error-{source_index:03d}"
    return {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION,
        "case_id": case_id,
        "scenario_id": case_id,
        "scenario_version": "unknown",
        "suite_id": "fireclaw-gazebo-collision-calibration",
        "suite_version": "1.0.0",
        "lane": "gazebo_collision_calibration",
        "split": split,
        "seed": None,
        "repeat_index": repeat_index,
        "scenario_type": "collection_error",
        "simulation_only": True,
        "excluded_from_task_metrics": True,
        "task_metrics_applicable": False,
        "task_success": None,
        "source_path": str(source),
        "calibration_passed": False,
        "collision_detection_success": False,
        "collision_metric_available": False,
        "observed_collision_free": None,
        "collision_count": None,
        "prohibited_contact_state_count": None,
        "probe_contact_state_count": None,
        "probe_collision_episode_count": None,
        "no_task_dispatch": False,
        "robot_remained_stopped": False,
        "robot_displacement_m": None,
        "asset_integrity": False,
        "provenance_complete": False,
        "paper_evidence_complete": False,
        "source_proof_bytes": None,
        "missing_data": ["source_proof"],
        "error": error,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Gazebo collision positive-control proofs in an immutable, "
            "task-metric-excluded evaluation lane."
        ),
    )
    parser.add_argument("--source-proof", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--split", default="development")
    parser.add_argument("--repeat-index", action="append", type=int, default=None)
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=512 * 1024 * 1024,
    )
    args = parser.parse_args(argv)
    try:
        result = run_gazebo_collision_calibration_eval(
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
