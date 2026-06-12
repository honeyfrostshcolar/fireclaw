"""ROS1 smoke proof artifacts -- JSONL-based recording of smoke test runs.

Stores non-secret metadata about each smoke test execution for
real-robot validation provenance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


@dataclass(frozen=True)
class Ros1SmokeArtifact:
    """Immutable record of a single ROS1 smoke test run."""

    run_id: str
    robot_id: str
    environment: str  # e.g. "sim", "real"
    command: str
    checks: Tuple[str, ...]  # e.g. ("topic_publish", "service_call")
    passed: bool
    started_at: str  # ISO 8601
    finished_at: str  # ISO 8601
    error_summary: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON encoding."""
        return {
            "run_id": self.run_id,
            "robot_id": self.robot_id,
            "environment": self.environment,
            "command": self.command,
            "checks": list(self.checks),
            "passed": self.passed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_summary": self.error_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Ros1SmokeArtifact:
        """Deserialize from a plain dict (e.g. parsed JSON line)."""
        checks = d.get("checks", [])
        if isinstance(checks, list):
            checks = tuple(checks)
        return cls(
            run_id=d["run_id"],
            robot_id=d["robot_id"],
            environment=d["environment"],
            command=d["command"],
            checks=checks,
            passed=d["passed"],
            started_at=d["started_at"],
            finished_at=d["finished_at"],
            error_summary=d.get("error_summary"),
        )


class JsonlRos1SmokeArtifactStore:
    """Append-only JSONL store for ROS1 smoke artifacts."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def append(self, artifact: Ros1SmokeArtifact) -> None:
        """Append one artifact as a JSON line."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(artifact.to_dict(), ensure_ascii=False) + "\n")

    def list_recent(self, limit: int = 10) -> List[Ros1SmokeArtifact]:
        """Return up to *limit* artifacts, most recent first."""
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        artifacts: List[Ros1SmokeArtifact] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            artifacts.append(Ros1SmokeArtifact.from_dict(json.loads(line)))
            if len(artifacts) >= limit:
                break
        return artifacts
