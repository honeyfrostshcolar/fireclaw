"""Packaging embodied experiment artifacts into a redacted proof bundle."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.log_redaction import redact_dict


def create_embodied_proof_bundle(
    *,
    output_dir: str | Path,
    run_id: str,
    mission_trace: dict[str, Any] | None = None,
    mission_events: dict[str, Any] | None = None,
    task_flow: dict[str, Any] | None = None,
    session_lineage: dict[str, Any] | None = None,
    memory_eval: dict[str, Any] | None = None,
    doctor_report: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Create an embodied experiment proof bundle with redacted artifacts.

    Parameters
    ----------
    output_dir:
        Directory to write bundle files into.
    run_id:
        Identifier for this experiment run.
    mission_trace:
        Mission trace dict (mission_id, status, subtasks, etc.).
    mission_events:
        Mission event replay dict.
    task_flow:
        Task-flow graph dict.
    session_lineage:
        Session lineage dict.
    memory_eval:
        Memory retrieval evaluation results dict.
    doctor_report:
        Fleet doctor check report dict.
    notes:
        Operator notes (will be redacted of secrets).

    Returns
    -------
    dict with "status": "created" and "output_dir" path.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).isoformat()

    # Build presence flags for summary
    presence = {
        "mission_trace": mission_trace is not None,
        "mission_events": mission_events is not None,
        "task_flow": task_flow is not None,
        "session_lineage": session_lineage is not None,
        "memory_eval": memory_eval is not None,
        "doctor_report": doctor_report is not None,
    }

    # Build and redact summary
    summary = redact_dict({
        "bundle_type": "embodied_experiment",
        "run_id": run_id,
        "created_at": created_at,
        "has": presence,
        "notes": notes,
    })

    _write_json(output_dir / "summary.json", summary)

    # Write optional artifact files (redacted)
    optional_files: list[tuple[str, dict[str, Any] | None]] = [
        ("mission-trace.json", mission_trace),
        ("mission-events.json", mission_events),
        ("task-flow.json", task_flow),
        ("session-lineage.json", session_lineage),
        ("memory-eval.json", memory_eval),
        ("doctor-report.json", doctor_report),
    ]
    for filename, data in optional_files:
        if data is not None:
            _write_json(output_dir / filename, redact_dict(data))

    # Always write README
    readme = _build_readme(run_id, created_at, presence, summary)
    (output_dir / "README.md").write_text(readme, encoding="utf-8")

    return {"status": "created", "output_dir": str(output_dir)}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_readme(
    run_id: str,
    created_at: str,
    presence: dict[str, bool],
    summary: dict[str, Any],
) -> str:
    artifact_lines = [
        "- `summary.json` -- Redacted bundle summary",
        "- `mission-trace.json` -- Mission trace (if present)",
        "- `mission-events.json` -- Mission event replay (if present)",
        "- `task-flow.json` -- Task-flow graph (if present)",
        "- `session-lineage.json` -- Session lineage (if present)",
        "- `memory-eval.json` -- Memory retrieval evaluation (if present)",
        "- `doctor-report.json` -- Fleet doctor check results (if present)",
        "- `README.md` -- This file",
    ]
    included = [k for k, v in presence.items() if v]
    return f"""# Embodied Experiment Proof Bundle

- **Run ID:** {run_id}
- **Created:** {created_at}
- **Included artifacts:** {', '.join(included) if included else '(none)'}

## Files

{chr(10).join(artifact_lines)}

## Notes

{summary.get('notes', '(none)')}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an embodied experiment proof bundle.")
    parser.add_argument("--output-dir", required=True, help="Output directory for the bundle.")
    parser.add_argument("--run-id", required=True, help="Experiment run identifier.")
    parser.add_argument("--mission-trace", default=None, help="Path to mission trace JSON.")
    parser.add_argument("--mission-events", default=None, help="Path to mission events JSON.")
    parser.add_argument("--task-flow", default=None, help="Path to task-flow JSON.")
    parser.add_argument("--session-lineage", default=None, help="Path to session lineage JSON.")
    parser.add_argument("--memory-eval", default=None, help="Path to memory eval JSON.")
    parser.add_argument("--doctor-report", default=None, help="Path to doctor report JSON.")
    parser.add_argument("--notes", default="", help="Operator notes.")

    args = parser.parse_args(argv)

    # Load optional JSON files
    def _load_json(path_str: str | None) -> dict[str, Any] | None:
        if path_str is None:
            return None
        p = Path(path_str)
        if not p.exists():
            print(f"Error: file not found: {p}", file=sys.stderr)
            raise SystemExit(1)
        return json.loads(p.read_text(encoding="utf-8"))

    result = create_embodied_proof_bundle(
        output_dir=args.output_dir,
        run_id=args.run_id,
        mission_trace=_load_json(args.mission_trace),
        mission_events=_load_json(args.mission_events),
        task_flow=_load_json(args.task_flow),
        session_lineage=_load_json(args.session_lineage),
        memory_eval=_load_json(args.memory_eval),
        doctor_report=_load_json(args.doctor_report),
        notes=args.notes,
    )

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
