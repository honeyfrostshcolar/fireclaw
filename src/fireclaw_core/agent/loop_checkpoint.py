"""Durable checkpoints for bounded FireClaw agent loops."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


LOOP_CHECKPOINT_VERSION = 1
LOOP_CHECKPOINT_STATUSES = frozenset({
    "running",
    "completed",
    "blocked",
    "escalated",
    "failed",
    "cancelled",
    "timed_out",
})


@dataclass(frozen=True)
class AgentLoopPendingOperation:
    operation_id: str
    iteration: int
    operation: str
    decision: dict[str, Any]
    prepared_at: str

    def __post_init__(self) -> None:
        if not self.operation_id.strip():
            raise ValueError("operation_id must not be empty")
        if self.iteration <= 0:
            raise ValueError("pending operation iteration must be positive")
        if not self.operation.strip():
            raise ValueError("pending operation must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "iteration": self.iteration,
            "operation": self.operation,
            "decision": dict(self.decision),
            "prepared_at": self.prepared_at,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> AgentLoopPendingOperation:
        decision = value.get("decision")
        if not isinstance(decision, dict):
            raise ValueError("pending operation decision must be an object")
        return cls(
            operation_id=str(value.get("operation_id") or ""),
            iteration=int(value.get("iteration") or 0),
            operation=str(value.get("operation") or ""),
            decision=dict(decision),
            prepared_at=str(value.get("prepared_at") or ""),
        )


@dataclass(frozen=True)
class AgentLoopCheckpoint:
    checkpoint_key: str
    role: str
    run_id: str
    status: str
    next_iteration: int
    started_at: str
    elapsed_seconds: float
    attempts: tuple[dict[str, Any], ...]
    observations: tuple[Any, ...]
    updated_at: str
    pending_operation: AgentLoopPendingOperation | None = None
    adapter_state: dict[str, Any] | None = None
    result: Any | None = None
    message: str | None = None
    reason_code: str | None = None
    version: int = LOOP_CHECKPOINT_VERSION

    def __post_init__(self) -> None:
        if self.version != LOOP_CHECKPOINT_VERSION:
            raise ValueError(
                f"Unsupported agent loop checkpoint version: {self.version}"
            )
        for name in ("checkpoint_key", "role", "run_id", "started_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.status not in LOOP_CHECKPOINT_STATUSES:
            raise ValueError(
                f"Unsupported agent loop checkpoint status: {self.status}"
            )
        if self.next_iteration <= 0:
            raise ValueError("next_iteration must be positive")
        if self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        if self.status != "running" and self.pending_operation is not None:
            raise ValueError(
                "terminal agent loop checkpoint cannot have a pending operation"
            )

    @property
    def is_recoverable(self) -> bool:
        return self.status == "running"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "version": self.version,
            "checkpoint_key": self.checkpoint_key,
            "role": self.role,
            "run_id": self.run_id,
            "status": self.status,
            "next_iteration": self.next_iteration,
            "started_at": self.started_at,
            "elapsed_seconds": self.elapsed_seconds,
            "attempts": [dict(item) for item in self.attempts],
            "observations": list(self.observations),
            "adapter_state": dict(self.adapter_state or {}),
            "result": self.result,
            "message": self.message,
            "reason_code": self.reason_code,
            "updated_at": self.updated_at,
        }
        if self.pending_operation is not None:
            result["pending_operation"] = self.pending_operation.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentLoopCheckpoint:
        attempts = value.get("attempts")
        observations = value.get("observations")
        adapter_state = value.get("adapter_state")
        if not isinstance(attempts, list) or any(
            not isinstance(item, dict) for item in attempts
        ):
            raise ValueError("checkpoint attempts must be a list of objects")
        if not isinstance(observations, list):
            raise ValueError("checkpoint observations must be a list")
        if adapter_state is not None and not isinstance(adapter_state, dict):
            raise ValueError("checkpoint adapter_state must be an object")
        pending = value.get("pending_operation")
        if pending is not None and not isinstance(pending, dict):
            raise ValueError("checkpoint pending_operation must be an object")
        return cls(
            version=int(value.get("version") or 0),
            checkpoint_key=str(value.get("checkpoint_key") or ""),
            role=str(value.get("role") or ""),
            run_id=str(value.get("run_id") or ""),
            status=str(value.get("status") or ""),
            next_iteration=int(value.get("next_iteration") or 0),
            started_at=str(value.get("started_at") or ""),
            elapsed_seconds=float(value.get("elapsed_seconds") or 0.0),
            attempts=tuple(dict(item) for item in attempts),
            observations=tuple(observations),
            pending_operation=(
                AgentLoopPendingOperation.from_dict(pending)
                if isinstance(pending, dict)
                else None
            ),
            adapter_state=(
                dict(adapter_state)
                if isinstance(adapter_state, dict)
                else None
            ),
            result=value.get("result"),
            message=(
                value["message"]
                if isinstance(value.get("message"), str)
                else None
            ),
            reason_code=(
                value["reason_code"]
                if isinstance(value.get("reason_code"), str)
                else None
            ),
            updated_at=str(value.get("updated_at") or ""),
        )


class AgentLoopCheckpointStore(Protocol):
    def append(self, checkpoint: AgentLoopCheckpoint) -> None:
        ...

    def latest(
        self,
        checkpoint_key: str,
    ) -> AgentLoopCheckpoint | None:
        ...


class JsonlAgentLoopCheckpointStore:
    """Append-only loop checkpoints with strict recovery reads."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def append(self, checkpoint: AgentLoopCheckpoint) -> None:
        encoded = (
            json.dumps(
                checkpoint.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+b") as handle:
                handle.seek(0)
                existing = handle.read()
                if existing and not existing.endswith((b"\n", b"\r")):
                    tail_start = max(
                        existing.rfind(b"\n"),
                        existing.rfind(b"\r"),
                    ) + 1
                    tail = existing[tail_start:].strip()
                    try:
                        json.loads(tail.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        handle.truncate(tail_start)
                    else:
                        handle.seek(0, os.SEEK_END)
                        handle.write(b"\n")
                handle.seek(0, os.SEEK_END)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    def latest(
        self,
        checkpoint_key: str,
    ) -> AgentLoopCheckpoint | None:
        return self.latest_by_key().get(checkpoint_key)

    def latest_by_key(self) -> dict[str, AgentLoopCheckpoint]:
        with self._lock:
            if not self.path.exists():
                return {}
            raw_lines = self.path.read_bytes().splitlines(keepends=True)
            latest: dict[str, AgentLoopCheckpoint] = {}
            for index, raw_line in enumerate(raw_lines, start=1):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    incomplete_tail = (
                        index == len(raw_lines)
                        and not raw_line.endswith((b"\n", b"\r"))
                    )
                    if incomplete_tail:
                        continue
                    raise ValueError(
                        f"Malformed agent loop checkpoint at line {index}."
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Invalid agent loop checkpoint at line {index}."
                    )
                try:
                    checkpoint = AgentLoopCheckpoint.from_dict(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Invalid agent loop checkpoint at line "
                        f"{index}: {exc}"
                    ) from exc
                latest[checkpoint.checkpoint_key] = checkpoint
            return latest

    def recoverable(self) -> dict[str, AgentLoopCheckpoint]:
        return {
            key: checkpoint
            for key, checkpoint in self.latest_by_key().items()
            if checkpoint.is_recoverable
        }
