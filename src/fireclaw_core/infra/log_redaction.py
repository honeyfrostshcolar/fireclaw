"""Utilities for redacting secrets from log text and structured data."""
from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9]{8,}"), "sk-***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}"), "Bearer ***"),
    (re.compile(r"api_key[=:]\s*[A-Za-z0-9._-]{8,}"), "api_key=***"),
    (re.compile(r"password[=:]\s*\S{4,}"), "password=***"),
    (re.compile(r"token[=:]\s*[A-Za-z0-9._-]{8,}"), "token=***"),
]


def redact_secrets(text: str) -> str:
    """Replace sensitive patterns with *** in text."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact string values in a dict."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = redact_secrets(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value)
        elif isinstance(value, list):
            result[key] = [_redact_value(item) for item in value]
        else:
            result[key] = value
    return result


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value
