"""FireClaw Multi-Version Profile Snapshot and Rollback Manager."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fireclaw_core.infra.runtime_paths import resolve_fireclaw_runtime_root


@dataclass
class ConfigSnapshot:
    """Represents an archived immutable profile snapshot."""
    snapshot_id: str
    profile_name: str
    timestamp_iso: str
    hash8: str
    file_path: str
    summary: str = "配置更新"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "profile_name": self.profile_name,
            "timestamp_iso": self.timestamp_iso,
            "hash8": self.hash8,
            "file_path": self.file_path,
            "summary": self.summary,
        }


class ProfileSnapshotManager:
    """Manages rolling configuration snapshots (up to max_snapshots) and atomic rollbacks."""

    def __init__(
        self,
        history_root: Optional[Path] = None,
        max_snapshots: int = 20,
    ) -> None:
        if history_root is not None:
            self.history_root = Path(history_root).resolve()
        else:
            runtime_root = resolve_fireclaw_runtime_root()
            self.history_root = runtime_root / "profiles" / ".history"

        self.max_snapshots = max_snapshots
        try:
            self.history_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            pass

    def create_snapshot(
        self,
        profile_path: Path,
        summary: str = "配置更新",
    ) -> ConfigSnapshot:
        """Create a new snapshot of the given profile and prune old ones beyond max_snapshots."""
        profile_path = Path(profile_path).resolve()
        if not profile_path.exists():
            raise FileNotFoundError(f"Profile file not found: {profile_path}")

        content_bytes = profile_path.read_bytes()
        hash8 = hashlib.sha256(content_bytes).hexdigest()[:8]

        now_utc = datetime.now(timezone.utc)
        ts_compact = now_utc.strftime("%Y%m%dT%H%M%S") + f"{now_utc.microsecond:06d}Z"
        timestamp_iso = now_utc.isoformat()

        profile_name = profile_path.stem
        snapshot_id = f"{profile_name}.{ts_compact}.{hash8}"

        self.history_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        snapshot_file = self.history_root / f"{snapshot_id}.toml"
        meta_file = self.history_root / f"{snapshot_id}.meta.json"

        # Atomic write snapshot content
        self._atomic_write_bytes(snapshot_file, content_bytes)

        snapshot = ConfigSnapshot(
            snapshot_id=snapshot_id,
            profile_name=profile_name,
            timestamp_iso=timestamp_iso,
            hash8=hash8,
            file_path=str(snapshot_file),
            summary=summary,
        )

        # Atomic write metadata
        meta_bytes = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write_bytes(meta_file, meta_bytes)

        # Prune older snapshots
        self._prune_snapshots(profile_name)

        return snapshot

    def list_snapshots(self, profile_name: str) -> list[ConfigSnapshot]:
        """List all archived snapshots for a given profile name, sorted newest first."""
        snapshots: list[ConfigSnapshot] = []

        if not self.history_root.exists():
            return snapshots

        for meta_file in self.history_root.glob("*.meta.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                if data.get("profile_name") == profile_name:
                    snapshots.append(
                        ConfigSnapshot(
                            snapshot_id=data["snapshot_id"],
                            profile_name=data["profile_name"],
                            timestamp_iso=data["timestamp_iso"],
                            hash8=data["hash8"],
                            file_path=data["file_path"],
                            summary=data.get("summary", "配置更新"),
                        )
                    )
            except Exception:
                continue

        # Sort newest first (descending timestamp)
        snapshots.sort(key=lambda s: s.timestamp_iso, reverse=True)
        return snapshots

    def get_snapshot(self, snapshot_id: str) -> Optional[ConfigSnapshot]:
        """Retrieve a specific snapshot by its unique ID."""
        if not self.history_root.exists():
            return None

        meta_file = self.history_root / f"{snapshot_id}.meta.json"
        if not meta_file.exists():
            return None

        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            return ConfigSnapshot(
                snapshot_id=data["snapshot_id"],
                profile_name=data["profile_name"],
                timestamp_iso=data["timestamp_iso"],
                hash8=data["hash8"],
                file_path=data["file_path"],
                summary=data.get("summary", "配置更新"),
            )
        except Exception:
            return None

    def rollback_to_snapshot(
        self,
        snapshot_id: str,
        target_profile_path: Path,
    ) -> tuple[bool, str]:
        """Atomically restore a snapshot content into target_profile_path and record audit snapshot."""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return False, f"快照不存在: {snapshot_id}"

        source_file = Path(snapshot.file_path)
        if not source_file.exists():
            return False, f"快照源文件丢失: {snapshot.file_path}"

        target_path = Path(target_profile_path).resolve()
        try:
            content_bytes = source_file.read_bytes()
            self._atomic_write_bytes(target_path, content_bytes)

            # Record audit snapshot
            self.create_snapshot(target_path, summary=f"Rollback to {snapshot_id}")
            return True, f"成功回滚至快照 {snapshot_id}"
        except Exception as err:
            return False, f"回滚失败: {err}"

    def _prune_snapshots(self, profile_name: str) -> None:
        """Keep only the latest max_snapshots versions and remove older files."""
        snapshots = self.list_snapshots(profile_name)
        if len(snapshots) <= self.max_snapshots:
            return

        to_remove = snapshots[self.max_snapshots:]
        for snap in to_remove:
            snap_file = Path(snap.file_path)
            meta_file = self.history_root / f"{snap.snapshot_id}.meta.json"
            try:
                if snap_file.exists():
                    snap_file.unlink()
            except OSError:
                pass
            try:
                if meta_file.exists():
                    meta_file.unlink()
            except OSError:
                pass

    def _atomic_write_bytes(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=str(target.parent),
            delete=False,
            prefix=f".tmp_{target.stem}_",
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp_path = Path(tmp.name)

        temp_path.replace(target)
