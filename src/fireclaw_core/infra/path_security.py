"""Host path policy for FireClaw runtime and sandbox boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


_BLOCKED_SYSTEM_PATHS = (
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/proc"),
    Path("/root"),
    Path("/run"),
    Path("/sys"),
    Path("/var/run"),
    Path("/private/etc"),
    Path("/private/var/run"),
)
_BLOCKED_HOME_NAMES = (
    ".aws",
    ".cargo",
    ".config",
    ".docker",
    ".gnupg",
    ".netrc",
    ".npm",
    ".ssh",
)
_BLOCKED_PROJECT_NAMES = (
    ".codegraph",
    ".git",
    "extensions",
    "openclaw",
    "skills",
    "src",
)


def validate_sandbox_workspace_root(
    value: str | Path,
    *,
    allowed_roots: Iterable[str | Path] = (),
    require_exists: bool = False,
    project_root: str | Path | None = None,
) -> Path:
    """Return a canonical workspace path or reject unsafe host exposure."""

    raw = os.fspath(value)
    if not raw or "\x00" in raw:
        raise ValueError("Sandbox workspace_root must be a non-empty path.")
    if any(character in raw for character in ("\n", "\r", ",")):
        raise ValueError(
            "Sandbox workspace_root contains characters unsafe for a Docker mount."
        )

    authored = Path(raw).expanduser()
    canonical = authored.resolve(strict=False)
    if not canonical.is_absolute():
        raise ValueError("Sandbox workspace_root must resolve to an absolute path.")
    if require_exists:
        if not canonical.exists():
            raise ValueError(
                f"Sandbox workspace_root does not exist: {canonical}"
            )
        if not canonical.is_dir():
            raise ValueError(
                f"Sandbox workspace_root must be a directory: {canonical}"
            )
    elif canonical.exists() and not canonical.is_dir():
        raise ValueError(
            f"Sandbox workspace_root must be a directory: {canonical}"
        )
    if canonical == Path("/"):
        raise ValueError("Sandbox workspace_root targets protected host path /.")

    home_roots = _home_roots()
    blocked_targets = list(_BLOCKED_SYSTEM_PATHS)
    blocked_exact_roots = list(home_roots)
    blocked_targets.extend(
        home / name
        for home in home_roots
        for name in _BLOCKED_HOME_NAMES
    )

    resolved_project_root = (
        Path(project_root).expanduser().resolve(strict=False)
        if project_root is not None
        else discover_fireclaw_project_root()
    )
    if resolved_project_root is not None:
        blocked_exact_roots.append(resolved_project_root)
        blocked_targets.extend(
            resolved_project_root / name
            for name in _BLOCKED_PROJECT_NAMES
        )

    for blocked_path in blocked_targets:
        blocked_canonical = blocked_path.resolve(strict=False)
        relation = _path_relation(canonical, blocked_canonical)
        if relation == "targets":
            raise ValueError(
                "Sandbox workspace_root targets protected host path "
                f"{blocked_canonical}: {canonical}"
            )
        if relation == "covers":
            raise ValueError(
                "Sandbox workspace_root is too broad and covers protected host "
                f"path {blocked_canonical}: {canonical}"
            )
    for blocked_path in blocked_exact_roots:
        blocked_canonical = blocked_path.resolve(strict=False)
        if canonical == blocked_canonical:
            raise ValueError(
                "Sandbox workspace_root targets protected host path "
                f"{blocked_canonical}: {canonical}"
            )
        if is_path_within(canonical, blocked_canonical):
            raise ValueError(
                "Sandbox workspace_root is too broad and covers protected host "
                f"path {blocked_canonical}: {canonical}"
            )

    canonical_allowed_roots = tuple(
        Path(root).expanduser().resolve(strict=False)
        for root in allowed_roots
    )
    if canonical_allowed_roots and not any(
        is_path_within(root, canonical)
        for root in canonical_allowed_roots
    ):
        allowed = ", ".join(str(root) for root in canonical_allowed_roots)
        raise ValueError(
            f"Sandbox workspace_root {canonical} is outside allowed roots: {allowed}"
        )
    return canonical


def validate_runtime_root(value: str | Path) -> Path:
    """Resolve a stable process directory without admitting sensitive roots."""

    raw = os.fspath(value)
    if not raw or "\x00" in raw:
        raise ValueError("FireClaw runtime root must be a non-empty path.")
    root = Path(raw).expanduser().resolve(strict=False)
    home_roots = _home_roots()
    if root == Path("/") or root in home_roots:
        raise ValueError(
            f"FireClaw runtime root targets protected path: {root}"
        )
    for blocked_path in (
        *_BLOCKED_SYSTEM_PATHS,
        *(
            home / name
            for home in home_roots
            for name in _BLOCKED_HOME_NAMES
        ),
    ):
        blocked_path = blocked_path.resolve(strict=False)
        if is_path_within(blocked_path, root) or is_path_within(
            root,
            blocked_path,
        ):
            raise ValueError(
                f"FireClaw runtime root targets protected path: {root}"
            )
    if root.exists() and not root.is_dir():
        raise ValueError(f"FireClaw runtime root must be a directory: {root}")
    return root


def is_path_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def discover_fireclaw_project_root(
    start: str | Path | None = None,
) -> Path | None:
    current = (
        Path(start).expanduser().resolve(strict=False)
        if start is not None
        else Path(__file__).resolve().parent
    )
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate.resolve(strict=False)
    return None


def _home_roots() -> tuple[Path, ...]:
    candidates: set[Path] = {
        Path.home().expanduser().resolve(strict=False),
    }
    for variable in ("HOME", "USERPROFILE"):
        value = os.environ.get(variable)
        if value:
            candidates.add(Path(value).expanduser().resolve(strict=False))
    try:
        import pwd

        candidates.add(
            Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=False)
        )
    except (ImportError, KeyError, OSError):
        pass
    return tuple(sorted(candidates, key=str))


def _path_relation(source: Path, blocked: Path) -> str | None:
    if is_path_within(blocked, source):
        return "targets"
    if is_path_within(source, blocked):
        return "covers"
    return None
