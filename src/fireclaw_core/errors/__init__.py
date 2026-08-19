"""Friendly error handling system for FireClaw."""
from __future__ import annotations

from fireclaw_core.errors.friendly_errors import (
    FriendlyErrorResponse,
    FriendlyErrorTemplate,
    FALLBACK_TEMPLATE,
    format_friendly_error_cli,
    resolve_friendly_error,
)
from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY

__all__ = [
    "FriendlyErrorTemplate",
    "FriendlyErrorResponse",
    "FALLBACK_TEMPLATE",
    "FRIENDLY_ERROR_REGISTRY",
    "resolve_friendly_error",
    "format_friendly_error_cli",
]
