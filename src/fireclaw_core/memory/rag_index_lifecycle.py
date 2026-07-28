"""Versioned lifecycle management for derived mission-memory RAG indexes."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable
from uuid import uuid4

from fireclaw_core.memory.rag_indexing import (
    MemoryRagIndexReport,
    build_memory_rag_indexes,
)
from fireclaw_core.rag.bm25_retrieval import BM25Retriever
from fireclaw_core.rag.dense_retrieval import DenseRetriever, EmbeddingProvider
from fireclaw_core.rag.runtime_retrieval import RagRuntimeConfig


GENERATION_SCHEMA_VERSION = 1
CURRENT_GENERATION_FILE = "current.json"
LAST_ATTEMPT_FILE = "last-attempt.json"

IndexBuilder = Callable[..., MemoryRagIndexReport]


@dataclass(frozen=True)
class MemoryRagRefreshReport:
    status: str
    generation_id: str | None
    source_version: str | None
    previous_generation_id: str | None = None
    error_code: str | None = None
    error_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryRagIndexLifecycleManager:
    """Build, validate, and atomically publish derived RAG generations."""

    def __init__(
        self,
        *,
        memory_path: Path,
        generation_root: Path,
        rag_config: RagRuntimeConfig,
        embedding_provider: EmbeddingProvider | None = None,
        index_builder: IndexBuilder = build_memory_rag_indexes,
    ) -> None:
        if rag_config.generation_root is None:
            raise ValueError("managed RAG indexing requires generation_root")
        self._memory_path = Path(memory_path)
        self._generation_root = Path(generation_root)
        self._rag_config = rag_config
        self._embedding_provider = embedding_provider
        self._index_builder = index_builder
        self._lock = threading.Lock()
        self._last_report: MemoryRagRefreshReport | None = None

    def refresh(
        self,
        *,
        trigger_reason: str,
        boundary_id: str | None = None,
    ) -> MemoryRagRefreshReport:
        """Publish a fresh generation, preserving the current one on failure."""
        self._generation_root.mkdir(parents=True, exist_ok=True)
        with self._lock, _exclusive_file_lock(
            self._generation_root / "index-build.lock"
        ):
            current = _read_json_if_valid(
                self._generation_root / CURRENT_GENERATION_FILE
            )
            previous_id = _nonempty_string(
                current.get("generation_id") if current else None
            )
            building_dir: Path | None = None
            try:
                source_bytes = self._memory_path.read_bytes()
                source_version = hashlib.sha256(source_bytes).hexdigest()
                build_signature = self._build_signature()
                if (
                    current is not None
                    and current.get("source_version") == source_version
                    and current.get("build_signature") == build_signature
                    and self._current_generation_valid(current)
                ):
                    report = MemoryRagRefreshReport(
                        status="unchanged",
                        generation_id=previous_id,
                        source_version=source_version,
                        previous_generation_id=previous_id,
                    )
                    self._record_attempt(
                        report,
                        trigger_reason=trigger_reason,
                        boundary_id=boundary_id,
                    )
                    return report

                generation_id = (
                    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    + f"-{source_version[:12]}-{uuid4().hex[:8]}"
                )
                generations_dir = self._generation_root / "generations"
                generations_dir.mkdir(parents=True, exist_ok=True)
                building_dir = generations_dir / f".building-{generation_id}"
                final_dir = generations_dir / generation_id
                building_dir.mkdir(parents=False, exist_ok=False)
                snapshot_path = building_dir / "source-memory.jsonl"
                snapshot_path.write_bytes(source_bytes)

                needs_bm25 = self._rag_config.backend in {
                    "bm25",
                    "hybrid",
                    "hybrid_rerank",
                }
                needs_dense = self._rag_config.backend in {
                    "dense",
                    "hybrid",
                    "hybrid_rerank",
                }
                index_report = self._index_builder(
                    snapshot_path,
                    building_dir,
                    embedding_provider=self._embedding_provider,
                    build_bm25=needs_bm25,
                    build_dense=needs_dense,
                )
                self._validate_generation(
                    building_dir,
                    build_bm25=needs_bm25,
                    build_dense=needs_dense,
                )

                final_source_bytes = self._memory_path.read_bytes()
                final_source_stat = self._memory_path.stat()
                if hashlib.sha256(final_source_bytes).hexdigest() != source_version:
                    raise RuntimeError("authority_changed_during_build")

                metadata = {
                    "schema_version": GENERATION_SCHEMA_VERSION,
                    "generation_id": generation_id,
                    "source_kind": self._rag_config.source_kind,
                    "source_path": str(self._memory_path.resolve()),
                    "source_version": source_version,
                    "source_size": final_source_stat.st_size,
                    "source_mtime_ns": final_source_stat.st_mtime_ns,
                    "build_signature": build_signature,
                    "backend": self._rag_config.backend,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "record_count": index_report.record_count,
                    "bm25_index_dir": (
                        f"generations/{generation_id}/bm25"
                        if needs_bm25
                        else None
                    ),
                    "dense_index_dir": (
                        f"generations/{generation_id}/dense"
                        if needs_dense
                        else None
                    ),
                }
                _write_json(building_dir / "generation.json", metadata)
                os.replace(building_dir, final_dir)
                building_dir = None
                _atomic_write_json(
                    self._generation_root / CURRENT_GENERATION_FILE,
                    metadata,
                )
                report = MemoryRagRefreshReport(
                    status="published",
                    generation_id=generation_id,
                    source_version=source_version,
                    previous_generation_id=previous_id,
                )
                self._record_attempt(
                    report,
                    trigger_reason=trigger_reason,
                    boundary_id=boundary_id,
                )
                return report
            except Exception as exc:
                if building_dir is not None:
                    shutil.rmtree(building_dir, ignore_errors=True)
                error_code = (
                    "authority_changed_during_build"
                    if str(exc) == "authority_changed_during_build"
                    else "index_build_failed"
                )
                report = MemoryRagRefreshReport(
                    status="failed_using_previous",
                    generation_id=previous_id,
                    source_version=None,
                    previous_generation_id=previous_id,
                    error_code=error_code,
                    error_class=type(exc).__name__,
                )
                self._record_attempt(
                    report,
                    trigger_reason=trigger_reason,
                    boundary_id=boundary_id,
                )
                return report

    def status(self) -> dict[str, Any]:
        current = _read_json_if_valid(
            self._generation_root / CURRENT_GENERATION_FILE
        )
        source_current = False
        if current is not None and self._memory_path.exists():
            stat = self._memory_path.stat()
            source_current = (
                current.get("source_size") == stat.st_size
                and current.get("source_mtime_ns") == stat.st_mtime_ns
            )
        return {
            "configured": True,
            "generation_root": str(self._generation_root),
            "active_generation": (
                current.get("generation_id") if current is not None else None
            ),
            "source_current": source_current,
            "last_refresh": (
                self._last_report.to_dict() if self._last_report is not None else None
            ),
        }

    def _build_signature(self) -> str:
        model_info = (
            self._embedding_provider.model_info.to_dict()
            if self._embedding_provider is not None
            else None
        )
        value = {
            "schema_version": GENERATION_SCHEMA_VERSION,
            "backend": self._rag_config.backend,
            "source_kind": self._rag_config.source_kind,
            "embedding": model_info,
        }
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_generation(
        self,
        generation_dir: Path,
        *,
        build_bm25: bool,
        build_dense: bool,
    ) -> None:
        if build_bm25:
            BM25Retriever.load(generation_dir / "bm25")
        if build_dense:
            if self._embedding_provider is None:
                raise ValueError("dense generation requires embedding_provider")
            DenseRetriever.load(
                generation_dir / "dense",
                self._embedding_provider,
            )

    def _current_generation_valid(self, metadata: dict[str, Any]) -> bool:
        if not _generation_paths_valid(self._generation_root, metadata):
            return False
        generation_id = _nonempty_string(metadata.get("generation_id"))
        if generation_id is None:
            return False
        generation_dir = self._generation_root / "generations" / generation_id
        try:
            manifest = _read_json_if_valid(
                generation_dir / "generation.json"
            )
            if manifest is None:
                return False
            for key in (
                "generation_id",
                "source_kind",
                "source_version",
                "build_signature",
            ):
                if manifest.get(key) != metadata.get(key):
                    return False
            self._validate_generation(
                generation_dir,
                build_bm25=metadata.get("bm25_index_dir") is not None,
                build_dense=metadata.get("dense_index_dir") is not None,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return False
        return True

    def _record_attempt(
        self,
        report: MemoryRagRefreshReport,
        *,
        trigger_reason: str,
        boundary_id: str | None,
    ) -> None:
        self._last_report = report
        payload = {
            **report.to_dict(),
            "trigger_reason": trigger_reason,
            "boundary_id": boundary_id,
            "attempted_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _atomic_write_json(
                self._generation_root / LAST_ATTEMPT_FILE,
                payload,
            )
        except OSError:
            pass


def _generation_paths_valid(root: Path, metadata: dict[str, Any]) -> bool:
    generation_id = _nonempty_string(metadata.get("generation_id"))
    if generation_id is None:
        return False
    generation_dir = root / "generations" / generation_id
    if not generation_dir.is_dir():
        return False
    for key in ("bm25_index_dir", "dense_index_dir"):
        value = metadata.get(key)
        if value is not None and not (root / str(value)).is_dir():
            return False
    return True


def _read_json_if_valid(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != GENERATION_SCHEMA_VERSION:
        return None
    return value


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


@contextmanager
def _exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
