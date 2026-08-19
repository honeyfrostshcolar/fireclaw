"""FireClaw Web Console package and resource loader."""
from __future__ import annotations

try:
    import importlib.resources as importlib_resources
except ImportError:
    import importlib_resources  # type: ignore

__all__ = [
    "read_web_console_asset",
    "ALLOWED_WEB_CONSOLE_ASSETS",
]

ALLOWED_WEB_CONSOLE_ASSETS: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


def read_web_console_asset(name: str) -> tuple[bytes, str]:
    """Read a validated Web Console static asset by filename.

    Returns (content_bytes, content_type).
    Raises FileNotFoundError for unknown assets, directory requests, or path traversal attempts.
    """
    clean_name = name.strip().lstrip("/")
    if not clean_name or clean_name not in ALLOWED_WEB_CONSOLE_ASSETS or "/" in clean_name or "\\" in clean_name or ".." in clean_name:
        raise FileNotFoundError(f"Web Console asset '{name}' not found or not permitted.")

    content_type = ALLOWED_WEB_CONSOLE_ASSETS[clean_name]
    try:
        resource_dir = importlib_resources.files("fireclaw_core.web_console")
        target_file = resource_dir.joinpath(clean_name)
        if not target_file.is_file():
            raise FileNotFoundError(f"Web Console asset '{clean_name}' not found in package.")
        return target_file.read_bytes(), content_type
    except (TypeError, AttributeError, ModuleNotFoundError) as exc:
        raise FileNotFoundError(f"Could not load Web Console asset '{clean_name}': {exc}") from exc
