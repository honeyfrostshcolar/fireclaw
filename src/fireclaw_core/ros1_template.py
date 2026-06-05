from __future__ import annotations

import re
from typing import Any


_WHOLE_TEMPLATE_RE = re.compile(r"^\{\{\s*(.*?)\s*\}\}$")
_INLINE_TEMPLATE_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_INPUT_SUBSTITUTION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_ros1_template(
    template: Any,
    *,
    inputs: dict[str, Any],
    targets: dict[str, Any],
) -> Any:
    context = {"inputs": inputs, "targets": targets, **inputs}
    return _render_value(template, context)


def _render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    if isinstance(value, str):
        return _render_string(value, context)
    return value


def _render_string(value: str, context: dict[str, Any]) -> Any:
    whole_match = _WHOLE_TEMPLATE_RE.match(value)
    if whole_match is not None:
        return _resolve_expression(whole_match.group(1), context)

    def replace(match: re.Match[str]) -> str:
        resolved = _resolve_expression(match.group(1), context)
        return str(resolved)

    return _INLINE_TEMPLATE_RE.sub(replace, value)


def _resolve_expression(expression: str, context: dict[str, Any]) -> Any:
    expanded = _INPUT_SUBSTITUTION_RE.sub(lambda match: str(_lookup_path(match.group(1), context)), expression.strip())
    return _lookup_path(expanded, context)


def _lookup_path(path: str, context: dict[str, Any]) -> Any:
    parts = path.split(".")
    value: Any = context
    consumed: list[str] = []
    for part in parts:
        consumed.append(part)
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        raise ValueError(f"Missing ROS1 template reference: {path}")
    return value
