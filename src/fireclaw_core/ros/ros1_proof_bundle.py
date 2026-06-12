from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.infra.log_redaction import redact_dict


def create_ros1_proof_bundle(
    *,
    output_dir: str | Path,
    robot_id: str,
    environment: str,
    doctor_report: dict[str, Any],
    smoke_artifacts: list[dict[str, Any]],
    notes: str = "",
) -> dict[str, Any]:
    """Create a ROS1 proof bundle with redacted summary and artifacts.

    Parameters
    ----------
    output_dir:
        Directory to write bundle files into.
    robot_id:
        Identifier of the robot under test.
    environment:
        Test environment (e.g. "sim", "hardware").
    doctor_report:
        Doctor check report dict.
    smoke_artifacts:
        List of smoke test artifact dicts.
    notes:
        Operator notes (will be redacted of secrets).

    Returns
    -------
    dict with "status": "created" and "output_dir" path.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).isoformat()

    # Build and redact all payloads
    summary = redact_dict({
        "bundle_type": "ros1_proof",
        "robot_id": robot_id,
        "environment": environment,
        "created_at": created_at,
        "doctor_status": doctor_report.get("status", "unknown"),
        "smoke_count": len(smoke_artifacts),
        "smoke_passed": sum(1 for a in smoke_artifacts if a.get("passed")),
        "notes": notes,
    })

    redacted_doctor = redact_dict(doctor_report)
    redacted_smoke = [redact_dict(a) for a in smoke_artifacts]

    # Write files
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "doctor-report.json", redacted_doctor)
    _write_json(output_dir / "ros1-smoke-artifacts.json", redacted_smoke)

    readme = _build_readme(robot_id, environment, created_at, summary)
    (output_dir / "README.md").write_text(readme, encoding="utf-8")

    return {"status": "created", "output_dir": str(output_dir)}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_readme(robot_id: str, environment: str, created_at: str, summary: dict[str, Any]) -> str:
    return f"""# ROS1 Proof Bundle

- **Robot:** {robot_id}
- **Environment:** {environment}
- **Created:** {created_at}
- **Doctor Status:** {summary.get("doctor_status", "unknown")}
- **Smoke Tests:** {summary.get("smoke_passed", 0)}/{summary.get("smoke_count", 0)} passed

## Files

- `summary.json` — Redacted bundle summary
- `doctor-report.json` — Fleet doctor check results
- `ros1-smoke-artifacts.json` — ROS1 smoke test artifacts
- `README.md` — This file

## Notes

{summary.get("notes", "(none)")}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a ROS1 proof bundle.")
    parser.add_argument("--output-dir", required=True, help="Output directory for the bundle.")
    parser.add_argument("--robot-id", required=True, help="Robot identifier.")
    parser.add_argument("--environment", required=True, help="Test environment (sim/hardware).")
    parser.add_argument("--doctor-report", required=True, help="Path to doctor report JSON.")
    parser.add_argument("--smoke-artifacts", required=True, help="Path to smoke artifacts JSONL.")
    parser.add_argument("--notes", default="", help="Operator notes.")

    args = parser.parse_args(argv)

    doctor_path = Path(args.doctor_report)
    if not doctor_path.exists():
        print(f"Error: doctor report not found: {doctor_path}", file=sys.stderr)
        return 1

    smoke_path = Path(args.smoke_artifacts)
    if not smoke_path.exists():
        print(f"Error: smoke artifacts not found: {smoke_path}", file=sys.stderr)
        return 1

    doctor_report = json.loads(doctor_path.read_text(encoding="utf-8"))
    smoke_artifacts = [
        json.loads(line) for line in smoke_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    result = create_ros1_proof_bundle(
        output_dir=args.output_dir,
        robot_id=args.robot_id,
        environment=args.environment,
        doctor_report=doctor_report,
        smoke_artifacts=smoke_artifacts,
        notes=args.notes,
    )

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
