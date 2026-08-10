"""Non-overwriting, content-addressed artifact bundles for evaluations."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Any, Iterable, Mapping
from uuid import uuid4

from fireclaw_core.evaluation.contracts import EVALUATION_RUN_SCHEMA_VERSION


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def make_run_id(lane: str) -> str:
    """Return a filesystem-safe, collision-resistant UTC run identifier."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{lane}-{timestamp}-{uuid4().hex[:8]}"


class EvaluationRunBundle:
    """Own one immutable run directory while allowing in-run finalization.

    The directory must be empty when claimed. This prevents a rerun from
    silently replacing failed or inconvenient samples. Individual artifacts
    are write-once; only explicitly managed lifecycle documents may be
    replaced by :meth:`replace_json` during the same process.
    """

    def __init__(self, run_dir: str | Path, *, run_id: str) -> None:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError("evaluation run_id contains unsafe characters")
        target = Path(run_dir).resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            raise FileExistsError(
                f"evaluation run directory is not empty: {target}"
            )
        self.run_id = run_id
        self.run_dir = target
        self._claim_path = target / ".evaluation-run.json"
        self._claim_path.write_text(
            json.dumps(
                {
                    "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
                    "run_id": run_id,
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def path(self, relative_name: str | Path) -> Path:
        relative = Path(relative_name)
        if relative.is_absolute() or not relative.parts:
            raise ValueError("artifact path must be a non-empty relative path")
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("artifact path contains an unsafe segment")
        target = (self.run_dir / relative).resolve(strict=False)
        target.relative_to(self.run_dir)
        return target

    def write_json(self, name: str | Path, value: Any) -> Path:
        target = self.path(name)
        if target.exists():
            raise FileExistsError(f"evaluation artifact already exists: {target}")
        return self._atomic_json(target, value)

    def replace_json(self, name: str | Path, value: Any) -> Path:
        """Atomically update a lifecycle document owned by this run."""

        return self._atomic_json(self.path(name), value)

    def write_jsonl(
        self,
        name: str | Path,
        values: Iterable[Mapping[str, Any]],
    ) -> Path:
        target = self.path(name)
        if target.exists():
            raise FileExistsError(f"evaluation artifact already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                for value in values:
                    handle.write(
                        json.dumps(
                            dict(value),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    def copy_file(
        self,
        name: str | Path,
        source: str | Path,
    ) -> Path:
        """Copy byte-identical, non-symlink evidence into this run."""

        origin = Path(source)
        if origin.is_symlink() or not origin.is_file():
            raise ValueError(
                "evaluation source must be a regular non-symlink file"
            )
        target = self.path(name)
        if target.exists():
            raise FileExistsError(
                f"evaluation artifact already exists: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(origin, temporary)
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    def finalize_artifact_manifest(self) -> dict[str, Any]:
        """Write hashes and sizes for every completed artifact in the run."""

        manifest_path = self.path("artifact-manifest.json")
        if manifest_path.exists():
            raise FileExistsError(
                f"evaluation artifact already exists: {manifest_path}"
            )
        files = []
        for path in sorted(self.run_dir.rglob("*")):
            if not path.is_file() or path == manifest_path:
                continue
            relative = path.relative_to(self.run_dir).as_posix()
            files.append({
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "media_type": (
                    mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream"
                ),
            })
        manifest = {
            "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifact_count": len(files),
            "files": files,
        }
        self.write_json("artifact-manifest.json", manifest)
        return manifest

    def _atomic_json(self, target: Path, value: Any) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target


def sha256_file(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


__all__ = [
    "EvaluationRunBundle",
    "canonical_json_sha256",
    "make_run_id",
    "sha256_file",
]
