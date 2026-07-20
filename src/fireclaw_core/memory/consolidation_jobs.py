"""Append-only job journal for resumable embodied-memory consolidation."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONSOLIDATION_JOB_STATUSES = frozenset({"pending", "running", "completed", "failed"})


@dataclass(frozen=True)
class ConsolidationJob:
    job_id: str
    mission_id: str
    runtime_mode: str
    source_event_ids: tuple[str, ...]
    supporting_event_ids: tuple[str, ...]
    episode_event_id: str
    gist_event_id: str
    status: str
    completed_steps: tuple[str, ...]
    created_at: str
    updated_at: str
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")
        if not self.mission_id:
            raise ValueError("mission_id must not be empty")
        if not self.runtime_mode:
            raise ValueError("runtime_mode must not be empty")
        if not self.source_event_ids:
            raise ValueError("source_event_ids must not be empty")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids must not contain duplicates")
        if len(set(self.supporting_event_ids)) != len(self.supporting_event_ids):
            raise ValueError("supporting_event_ids must not contain duplicates")
        if set(self.source_event_ids) & set(self.supporting_event_ids):
            raise ValueError("source_event_ids and supporting_event_ids must not overlap")
        if not self.episode_event_id or not self.gist_event_id:
            raise ValueError("episode_event_id and gist_event_id must not be empty")
        if self.status not in CONSOLIDATION_JOB_STATUSES:
            raise ValueError(f"invalid consolidation job status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_event_ids"] = list(self.source_event_ids)
        data["supporting_event_ids"] = list(self.supporting_event_ids)
        data["completed_steps"] = list(self.completed_steps)
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConsolidationJob":
        return cls(
            job_id=str(value.get("job_id") or ""),
            mission_id=str(value.get("mission_id") or ""),
            runtime_mode=str(value.get("runtime_mode") or ""),
            source_event_ids=tuple(
                str(item) for item in value.get("source_event_ids", [])
                if isinstance(item, str) and item
            ),
            supporting_event_ids=tuple(
                str(item) for item in value.get("supporting_event_ids", [])
                if isinstance(item, str) and item
            ),
            episode_event_id=str(value.get("episode_event_id") or ""),
            gist_event_id=str(value.get("gist_event_id") or ""),
            status=str(value.get("status") or ""),
            completed_steps=tuple(
                str(item) for item in value.get("completed_steps", [])
                if isinstance(item, str) and item
            ),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            error=value.get("error") if isinstance(value.get("error"), str) else None,
        )


class ConsolidationJobStore:
    """Persists the latest state of each job as replayable JSONL transitions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def ensure_job(
        self,
        *,
        job_id: str,
        mission_id: str,
        runtime_mode: str,
        source_event_ids: tuple[str, ...],
        supporting_event_ids: tuple[str, ...] = (),
        episode_event_id: str,
        gist_event_id: str,
    ) -> ConsolidationJob:
        with self._lock:
            existing = self._jobs_by_id().get(job_id)
            if existing is not None:
                expected = (
                    mission_id,
                    runtime_mode,
                    source_event_ids,
                    supporting_event_ids,
                    episode_event_id,
                    gist_event_id,
                )
                actual = (
                    existing.mission_id,
                    existing.runtime_mode,
                    existing.source_event_ids,
                    existing.supporting_event_ids,
                    existing.episode_event_id,
                    existing.gist_event_id,
                )
                if actual != expected:
                    raise ValueError(f"consolidation job identity conflict: {job_id}")
                return existing
            now = _utc_now()
            job = ConsolidationJob(
                job_id=job_id,
                mission_id=mission_id,
                runtime_mode=runtime_mode,
                source_event_ids=source_event_ids,
                supporting_event_ids=supporting_event_ids,
                episode_event_id=episode_event_id,
                gist_event_id=gist_event_id,
                status="pending",
                completed_steps=(),
                created_at=now,
                updated_at=now,
            )
            self._append(job)
            return job

    def transition(
        self,
        job_id: str,
        *,
        status: str,
        completed_step: str | None = None,
        error: str | None = None,
    ) -> ConsolidationJob:
        if status not in CONSOLIDATION_JOB_STATUSES:
            raise ValueError(f"invalid consolidation job status: {status}")
        with self._lock:
            current = self._jobs_by_id().get(job_id)
            if current is None:
                raise KeyError(f"consolidation job not found: {job_id}")
            if current.status == "completed" and status != "completed":
                raise ValueError("completed consolidation jobs cannot be reopened")
            steps = list(current.completed_steps)
            if completed_step is not None and completed_step not in steps:
                steps.append(completed_step)
            updated = ConsolidationJob(
                job_id=current.job_id,
                mission_id=current.mission_id,
                runtime_mode=current.runtime_mode,
                source_event_ids=current.source_event_ids,
                supporting_event_ids=current.supporting_event_ids,
                episode_event_id=current.episode_event_id,
                gist_event_id=current.gist_event_id,
                status=status,
                completed_steps=tuple(steps),
                created_at=current.created_at,
                updated_at=_utc_now(),
                error=error,
            )
            self._append(updated)
            return updated

    def get(self, job_id: str) -> ConsolidationJob | None:
        with self._lock:
            return self._jobs_by_id().get(job_id)

    def list_jobs(
        self,
        *,
        mission_id: str | None = None,
        status: str | None = None,
    ) -> list[ConsolidationJob]:
        with self._lock:
            jobs = list(self._jobs_by_id().values())
        if mission_id is not None:
            jobs = [job for job in jobs if job.mission_id == mission_id]
        if status is not None:
            jobs = [job for job in jobs if job.status == status]
        return sorted(jobs, key=lambda job: (job.created_at, job.job_id))

    def _jobs_by_id(self) -> dict[str, ConsolidationJob]:
        if not self.path.exists():
            return {}
        jobs: dict[str, ConsolidationJob] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    continue
                try:
                    job = ConsolidationJob.from_dict(value)
                except (TypeError, ValueError):
                    continue
                jobs[job.job_id] = job
        return jobs

    def _append(self, job: ConsolidationJob) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(job.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
