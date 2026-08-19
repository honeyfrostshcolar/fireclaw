"""FriendlyError data structures, resolution engine, and CLI formatter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


UNVERIFIED_ROBOT_STATUS = (
    "UNKNOWN：未收到可验证的机器人物理状态证据；请按停止未确认处置。"
)
UNVERIFIED_ACTION_STATUS = (
    "仅生成了错误提示；未收到可验证的自动处置回执，"
    "不声明机器人已停止、制动或处于安全状态。"
)


@dataclass
class FriendlyErrorTemplate:
    """Template for a friendly error message.

    Static templates cannot prove physical state or an executed action. The
    two legacy fields remain for API compatibility but are normalized to
    conservative values at construction time.
    """
    error_code: str
    severity: str  # "critical" | "warning" | "info"
    what_happened: str
    robot_safe_status: str
    action_taken: str
    next_steps: str
    suggested_actions: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.robot_safe_status = UNVERIFIED_ROBOT_STATUS
        self.action_taken = UNVERIFIED_ACTION_STATUS


@dataclass
class FriendlyErrorResponse:
    """Resolved friendly error response ready for presentation or API serialization."""
    error_code: str
    severity: str
    what_happened: str
    robot_safe_status: str
    action_taken: str
    next_steps: str
    suggested_actions: list[dict[str, str]] = field(default_factory=list)
    technical_details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the response to a dictionary."""
        d: dict[str, Any] = {
            "error_code": self.error_code,
            "severity": self.severity,
            "what_happened": self.what_happened,
            "robot_safe_status": self.robot_safe_status,
            "action_taken": self.action_taken,
            "next_steps": self.next_steps,
            "suggested_actions": [dict(a) for a in self.suggested_actions],
            "technical_details": self.technical_details,
        }
        return d


class _SafeDict(dict):
    """Dictionary that returns the key in braces when a key is missing."""
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format(template_str: str, context: dict[str, Any] | None) -> str:
    """Format template string safely without raising KeyError for missing keys."""
    if not template_str:
        return ""
    mapping = _SafeDict(context or {})
    try:
        return template_str.format_map(mapping)
    except Exception:
        import re

        def _repl(match: re.Match) -> str:
            k = match.group(1)
            return str(mapping.get(k, "{" + k + "}"))

        return re.sub(r"\{([^{}]+)\}", _repl, template_str)


FALLBACK_TEMPLATE = FriendlyErrorTemplate(
    error_code="unknown_error",
    severity="warning",
    what_happened="发生未分类系统错误: {error_code}。系统已捕获该异常。",
    robot_safe_status=UNVERIFIED_ROBOT_STATUS,
    action_taken=UNVERIFIED_ACTION_STATUS,
    next_steps="请查看日志排查详细原因，或使用 --verbose 参数重试以获取完整技术信息。",
    suggested_actions=[
        {"label": "查看系统日志", "action": "view_logs"},
        {"label": "重试操作", "action": "retry"},
    ],
)


def resolve_friendly_error(
    error_code: str,
    context: dict[str, Any] | None = None,
    technical_details: str | None = None,
) -> FriendlyErrorResponse:
    """Resolve an error without inventing robot state or action receipts.

    Registry templates predate evidence-bearing FriendlyError contracts. Their
    safety/action prose is therefore not authoritative and is deliberately not
    exposed. A later evidence-aware resolver may replace these conservative
    values only after validating typed Adapter evidence and action receipts.
    """
    from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY

    tpl = FRIENDLY_ERROR_REGISTRY.get(error_code, FALLBACK_TEMPLATE)
    ctx: dict[str, Any] = {"error_code": error_code}
    if context:
        ctx.update(context)

    what_happened = _safe_format(tpl.what_happened, ctx)
    robot_safe_status = UNVERIFIED_ROBOT_STATUS
    action_taken = UNVERIFIED_ACTION_STATUS
    next_steps = _safe_format(tpl.next_steps, ctx)

    return FriendlyErrorResponse(
        error_code=error_code,
        severity=tpl.severity,
        what_happened=what_happened,
        robot_safe_status=robot_safe_status,
        action_taken=action_taken,
        next_steps=next_steps,
        suggested_actions=[dict(a) for a in tpl.suggested_actions],
        technical_details=technical_details,
    )


def format_friendly_error_cli(response: FriendlyErrorResponse, verbose: bool = False) -> str:
    """Format a FriendlyErrorResponse into a 4-part Chinese boxed CLI output."""
    icon = "✖" if response.severity == "critical" else ("⚠" if response.severity == "warning" else "ℹ")
    lines = [
        f"┌─ {icon} [{response.severity.upper()}] {response.error_code} ─┐",
        f"│ ❶ 发生了什么:     {response.what_happened}",
        f"│ ❷ 机器人安全证据: {response.robot_safe_status}",
        f"│ ❸ 自动处置回执:   {response.action_taken}",
        f"│ ❹ 建议下一步:     {response.next_steps}",
    ]
    if response.suggested_actions:
        actions_str = ", ".join(f"[{a.get('label', a.get('action', ''))}]" for a in response.suggested_actions)
        lines.append(f"│    推荐操作:      {actions_str}")
    if verbose and response.technical_details:
        lines.append("├─ 技术详情 ─────────────────────────────────────────────┤")
        for tech_line in str(response.technical_details).splitlines():
            lines.append(f"│ {tech_line}")
    lines.append("└────────────────────────────────────────────────────────┘")
    return "\n".join(lines)
