"""Stable runtime-root resolution independent of the launch shell directory."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from fireclaw_core.infra.path_security import validate_runtime_root


FIRECLAW_HOME_ENV = "FIRECLAW_HOME"


def resolve_fireclaw_runtime_root(
    *,
    configured: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    launch_cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    """Resolve the process-owned root used for relative runtime artifacts."""

    environment = os.environ if env is None else env
    cwd = (
        Path.cwd().resolve(strict=False)
        if launch_cwd is None
        else Path(launch_cwd).expanduser().resolve(strict=False)
    )
    resolved_config_path = (
        _resolve_from(config_path, cwd)
        if config_path is not None
        else None
    )
    base = resolved_config_path.parent if resolved_config_path else cwd

    candidate: str | Path | None = configured
    environment_root = environment.get(FIRECLAW_HOME_ENV)
    if candidate is None and environment_root is not None:
        if not Path(environment_root).expanduser().is_absolute():
            raise ValueError(f"{FIRECLAW_HOME_ENV} must be an absolute path.")
        candidate = environment_root
    if candidate is None and resolved_config_path is not None:
        candidate = resolved_config_path.parent
    if candidate is None:
        home_path = (
            Path.home()
            if home is None
            else Path(home).expanduser()
        )
        candidate = home_path / ".fireclaw"

    root = _resolve_from(candidate, base)
    return validate_runtime_root(root)


def resolve_runtime_path(
    value: str | Path,
    *,
    runtime_root: str | Path,
) -> Path:
    root = validate_runtime_root(runtime_root)
    return _resolve_from(value, root)


@contextmanager
def fireclaw_runtime_directory(
    root: str | Path,
) -> Iterator[Path]:
    """Temporarily make the validated runtime root the process cwd."""

    canonical = validate_runtime_root(root)
    canonical.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = Path.cwd()
    os.chdir(canonical)
    try:
        yield canonical
    finally:
        os.chdir(original)


def _resolve_from(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)
