"""Cross-process consolidation coordinator with file lease and recovery."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fireclaw_core.memory.consolidation import (
    ConsolidationResult,
    FireClawConsolidationEngine,
)
from fireclaw_core.memory.consolidation_state import (
    ConsolidationBoundary,
    ConsolidationStateStore,
)
from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryStore,
    MEMORY_RUNTIME_MODES,
)
from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)

TERMINAL_MISSION_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
TERMINAL_SUBTASK_STATUSES = frozenset(
    set(ROBOT_TASK_TERMINAL_STATUSES)
    | {"succeeded", "block", "denied"}
)


@dataclass(frozen=True)
class CoordinatorRunResult:
    processed: int
    lock_busy: int = 0
    errors: int = 0


class MemoryConsolidationCoordinator:
    """Manages closed-range consolidation boundaries with file lease and recovery."""

    def __init__(
        self,
        *,
        engine: FireClawConsolidationEngine,
        state_store: ConsolidationStateStore,
        store: EmbodiedMemoryStore,
        lock_path: Path | str,
        post_boundary_hook: Callable[[ConsolidationBoundary], Any] | None = None,
    ) -> None:
        self._engine = engine
        self._state_store = state_store
        self._store = store
        self._lock_path = Path(lock_path)
        self._post_boundary_hook = post_boundary_hook
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def request_terminal_boundary(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        scope_kind: str,
        robot_id: str | None,
        subtask_id: str | None,
        terminal_event_id: str,
        terminal_status: str,
        through_sequence: int,
    ) -> None:
        """Persist a boundary request if the transition is terminal.

        Non-terminal statuses are silently ignored.
        """
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        if terminal_status not in TERMINAL_SUBTASK_STATUSES and terminal_status not in TERMINAL_MISSION_STATUSES:
            # Non-terminal: ignore
            return
        if not mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if not terminal_event_id.strip():
            raise ValueError("terminal_event_id must not be empty")
        if through_sequence <= 0:
            raise ValueError("through_sequence must be positive")

        self._state_store.reserve_boundary(
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            scope_kind=scope_kind,
            robot_id=robot_id,
            subtask_id=subtask_id,
            terminal_event_id=terminal_event_id,
            trigger_reason="subtask_terminal" if scope_kind == "subtask" else "mission_terminal",
            through_sequence=through_sequence,
        )

    def run_pending_once(self, *, max_boundaries: int = 8) -> CoordinatorRunResult:
        """Process up to max_boundaries pending boundaries under file lease."""
        processed = 0
        lock_busy = 0
        errors = 0
        try:
            with _file_lease(self._lock_path):
                pending = self._state_store.pending_boundaries()
                for boundary in pending[:max_boundaries]:
                    try:
                        self._process_boundary(boundary)
                        processed += 1
                    except Exception as exc:
                        logger.warning(
                            "consolidation boundary failed",
                            extra={"boundary_id": boundary.boundary_id},
                            exc_info=True,
                        )
                        try:
                            self._state_store.transition(
                                boundary.boundary_id,
                                status="failed",
                                error_code="consolidation_failed",
                                error_class=type(exc).__name__,
                            )
                        except Exception:
                            pass
                        errors += 1
        except _LeaseBusyError:
            lock_busy = len(self._state_store.pending_boundaries()[:max_boundaries])
        return CoordinatorRunResult(processed=processed, lock_busy=lock_busy, errors=errors)

    def recover(self) -> None:
        """Reset running and failed boundaries back to queued for retry."""
        statuses = self._state_store._latest_boundary_statuses()
        for boundary in self._state_store.boundaries().values():
            status = statuses.get(boundary.boundary_id, "queued")
            if status in {"running", "failed"}:
                self._state_store.transition(boundary.boundary_id, status="queued")

    def start(self) -> None:
        """Start the background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="consolidation-coordinator",
        )
        self._worker_thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        """Stop the background worker and wait for it to finish."""
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout_seconds)
            self._worker_thread = None

    def status(self) -> dict[str, Any]:
        """Return content-free status about the consolidation queue."""
        pending = self._state_store.pending_boundaries()
        statuses = self._state_store._latest_boundary_statuses()
        queued = sum(1 for b in pending if statuses.get(b.boundary_id, "queued") == "queued")
        running = sum(1 for b in pending if statuses.get(b.boundary_id) == "running")
        return {
            "queued_boundaries": queued,
            "running_boundaries": running,
            "total_pending": len(pending),
            "worker_alive": self._worker_thread is not None and self._worker_thread.is_alive(),
        }

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            result = self.run_pending_once()
            if result.processed == 0 and result.lock_busy == 0:
                self._stop_event.wait(timeout=30.0)
            else:
                self._stop_event.wait(timeout=1.0)

    def _process_boundary(self, boundary: ConsolidationBoundary) -> None:
        """Process a single boundary: select sources, consolidate, advance watermark."""
        self._state_store.transition(boundary.boundary_id, status="running")

        # Select eligible source events in the closed range
        source_ids = self._select_eligible_sources(boundary)

        if len(source_ids) < self._engine.config.min_events_per_episode:
            # Not enough events for an episode — advance watermark without artifact
            self._state_store.transition(
                boundary.boundary_id,
                status="covered_without_episode",
                through_sequence=boundary.through_sequence,
            )
            self._run_post_boundary_hook(boundary)
            return

        # Consolidate the exact source set
        result = self._engine.consolidate_events(
            mission_id=boundary.mission_id,
            runtime_mode=boundary.runtime_mode,
            source_event_ids=tuple(source_ids),
        )

        # Check actual result: if no artifacts produced, report covered_without_episode
        if not result.episode_event_ids and not result.gist_event_ids:
            self._state_store.transition(
                boundary.boundary_id,
                status="covered_without_episode",
                through_sequence=boundary.through_sequence,
            )
        else:
            self._state_store.transition(
                boundary.boundary_id,
                status="completed",
                through_sequence=boundary.through_sequence,
            )
        self._run_post_boundary_hook(boundary)

    def _run_post_boundary_hook(self, boundary: ConsolidationBoundary) -> None:
        if self._post_boundary_hook is None:
            return
        try:
            self._post_boundary_hook(boundary)
        except Exception:
            logger.warning(
                "post-consolidation boundary hook failed",
                extra={"boundary_id": boundary.boundary_id},
                exc_info=True,
            )

    def _select_eligible_sources(self, boundary: ConsolidationBoundary) -> list[str]:
        """Select eligible event IDs within the boundary's closed range."""
        all_events = self._store.list_events(mission_id=boundary.mission_id)
        eligible: list[str] = []
        for seq_idx, event in enumerate(all_events, start=1):
            # 1-based absolute sequence position
            if seq_idx <= boundary.after_sequence or seq_idx > boundary.through_sequence:
                continue
            if event.runtime_mode != boundary.runtime_mode:
                continue
            # Scope-kind exact predicates
            if boundary.scope_kind == "subtask":
                if event.robot_id != boundary.robot_id or event.subtask_id != boundary.subtask_id:
                    continue
            else:
                if event.subtask_id is not None:
                    continue
            if event.event_type in {"episode", "gist", "lesson"}:
                continue
            eligible.append(event.event_id)
        return eligible


class _LeaseBusyError(Exception):
    """Raised when the consolidation lock is held by another process."""


class _file_lease:
    """Context manager for Linux fcntl file lock."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: Any = None

    def __enter__(self) -> _file_lease:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.touch(exist_ok=True)
        self._fd = self._lock_path.open("r+")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self._fd.close()
            self._fd = None
            raise _LeaseBusyError(f"consolidation lock busy: {self._lock_path}")
        # Write content-free owner metadata
        try:
            meta = {"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}
            self._lock_path.write_text(json.dumps(meta))
        except Exception:
            pass
        return self

    def __exit__(self, *args: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                self._fd.close()
                self._fd = None
